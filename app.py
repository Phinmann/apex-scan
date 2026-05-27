from flask import Flask, request, jsonify, send_from_directory
import requests
import os
import re

app = Flask(__name__, static_folder='static')

FINNHUB_BASE = 'https://finnhub.io/api/v1'
EDGAR_BASE   = 'https://data.sec.gov'
EFTS_BASE    = 'https://efts.sec.gov'

SEC_HEADERS = {
    'User-Agent': 'APEX/SCAN research@apex-scan.com',
    'Accept':     'application/json'
}

# ── Static ────────────────────────────────────────────────────────────────────
@app.route('/')
def index():
    return send_from_directory('static', 'index.html')

# ── Finnhub proxy ─────────────────────────────────────────────────────────────
@app.route('/api/finnhub')
def finnhub_proxy():
    path  = request.args.get('path')
    token = request.args.get('token')
    if not path or not token:
        return jsonify({'error': 'Missing path or token'}), 400

    # Only allow known safe Finnhub paths
    allowed = [
        '/stock/symbol', '/quote', '/stock/profile2',
        '/stock/metric', '/stock/insider-transactions',
        '/company-news', '/stock/shares'
    ]
    if not any(path.startswith(a) for a in allowed):
        return jsonify({'error': 'Path not allowed'}), 403

    try:
        url    = FINNHUB_BASE + path
        params = {k: v for k, v in request.args.items() if k not in ('path',)}
        params['token'] = token
        r = requests.get(url, params=params, timeout=15)
        return jsonify(r.json()), r.status_code
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ── SEC EDGAR — company facts (shares outstanding history + financials) ────────
@app.route('/api/sec/facts')
def sec_facts():
    """
    Returns the full company-facts JSON for a given CIK.
    Used to derive share count trend and revenue from official XBRL data.
    """
    cik = request.args.get('cik', '').strip().lstrip('0')
    if not cik:
        return jsonify({'error': 'Missing cik'}), 400
    try:
        padded = cik.zfill(10)
        url = f'{EDGAR_BASE}/api/xbrl/companyfacts/CIK{padded}.json'
        r   = requests.get(url, headers=SEC_HEADERS, timeout=25)
        return jsonify(r.json()), r.status_code
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ── SEC EDGAR — Form 4 insider filings direct feed ───────────────────────────
@app.route('/api/sec/form4')
def sec_form4():
    """
    Pulls real Form 4 filings from EDGAR full-text search for a given ticker.
    Returns raw filings list with transaction type, shares, and price.
    """
    ticker = request.args.get('ticker', '').upper().strip()
    if not ticker:
        return jsonify({'error': 'Missing ticker'}), 400

    try:
        # Step 1: resolve CIK from ticker
        cik_url = f'{EDGAR_BASE}/submissions/CIK'
        lookup  = requests.get(
            f'https://efts.sec.gov/LATEST/search-index?q=%22{ticker}%22&dateRange=custom&startdt=2020-01-01&forms=4',
            headers=SEC_HEADERS, timeout=20
        )
        # Step 1b: use the company tickers JSON — much more reliable
        tickers_r = requests.get(
            'https://www.sec.gov/files/company_tickers.json',
            headers=SEC_HEADERS, timeout=20
        )
        tickers_data = tickers_r.json()
        cik = None
        for entry in tickers_data.values():
            if entry.get('ticker', '').upper() == ticker:
                cik = str(entry['cik_str'])
                break

        if not cik:
            return jsonify({'error': 'CIK not found', 'ticker': ticker}), 404

        # Step 2: pull submissions feed for this CIK — contains all recent filings
        padded = cik.zfill(10)
        sub_url = f'{EDGAR_BASE}/submissions/CIK{padded}.json'
        sub_r   = requests.get(sub_url, headers=SEC_HEADERS, timeout=20)
        sub     = sub_r.json()

        recent  = sub.get('filings', {}).get('recent', {})
        forms   = recent.get('form', [])
        dates   = recent.get('filingDate', [])
        accNums = recent.get('accessionNumber', [])
        docs    = recent.get('primaryDocument', [])

        form4s = []
        for i, f in enumerate(forms):
            if f == '4' and len(form4s) < 20:
                form4s.append({
                    'form':            f,
                    'filingDate':      dates[i] if i < len(dates) else '',
                    'accessionNumber': accNums[i] if i < len(accNums) else '',
                    'primaryDocument': docs[i] if i < len(docs) else '',
                    'cik':             cik
                })

        return jsonify({
            'ticker':     ticker,
            'cik':        cik,
            'companyName': sub.get('name', ''),
            'form4s':     form4s
        })

    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ── SEC EDGAR — dilution check via XBRL shares outstanding ───────────────────
@app.route('/api/sec/dilution')
def sec_dilution():
    """
    Pulls CommonStockSharesOutstanding from XBRL company facts.
    Returns annual share counts so the frontend can flag >25% YoY increase.
    """
    cik = request.args.get('cik', '').strip()
    if not cik:
        return jsonify({'error': 'Missing cik'}), 400
    try:
        padded = cik.zfill(10)
        url    = f'{EDGAR_BASE}/api/xbrl/companyfacts/CIK{padded}.json'
        r      = requests.get(url, headers=SEC_HEADERS, timeout=25)
        data   = r.json()

        us_gaap = data.get('facts', {}).get('us-gaap', {})
        shares  = us_gaap.get('CommonStockSharesOutstanding', {})
        units   = shares.get('units', {})
        # Prefer 'shares' unit; fall back to first available
        share_series = units.get('shares', units.get(list(units.keys())[0], [])) if units else []

        # Keep 10-K annual filings only (form == '10-K')
        annual = [
            {'end': e['end'], 'val': e['val'], 'form': e.get('form','')}
            for e in share_series
            if e.get('form', '') in ('10-K', '10-K/A')
        ]
        annual.sort(key=lambda x: x['end'])

        # Calculate YoY change
        for i in range(1, len(annual)):
            prev = annual[i-1]['val']
            curr = annual[i]['val']
            annual[i]['yoy_pct'] = round((curr - prev) / prev * 100, 1) if prev else None

        return jsonify({'cik': cik, 'annual_shares': annual})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ── SEC EDGAR — CIK lookup by ticker ─────────────────────────────────────────
@app.route('/api/sec/cik')
def sec_cik():
    """Resolve ticker → CIK using SEC company_tickers.json"""
    ticker = request.args.get('ticker', '').upper().strip()
    if not ticker:
        return jsonify({'error': 'Missing ticker'}), 400
    try:
        r = requests.get(
            'https://www.sec.gov/files/company_tickers.json',
            headers=SEC_HEADERS, timeout=20
        )
        for entry in r.json().values():
            if entry.get('ticker', '').upper() == ticker:
                return jsonify({'ticker': ticker, 'cik': str(entry['cik_str']), 'name': entry.get('title','')})
        return jsonify({'error': 'Not found'}), 404
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ── SEC EDGAR — generic EFTS full-text search proxy ──────────────────────────
@app.route('/api/sec/edgar')
def sec_edgar_proxy():
    path = request.args.get('path')
    if not path:
        return jsonify({'error': 'Missing path'}), 400
    try:
        url = EFTS_BASE + path
        r   = requests.get(url, headers=SEC_HEADERS, timeout=20)
        return jsonify(r.json()), r.status_code
    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
