from flask import Flask, request, jsonify, send_from_directory
import requests
import os
import re
import datetime

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

# ── SEC EDGAR — Form 4 index (Pipeline 1 backbone) ────────────────────────────
@app.route('/api/sec/form4index')
def sec_form4_index():
    """
    Fetches the EDGAR quarterly full-index form.idx file.
    Filters to Form 4 filings from the last N days.
    Returns list of {cik, companyName, dateFiled} — reliable, no search API needed.
    """
    days = int(request.args.get('days', 30))
    limit = int(request.args.get('limit', 200))

    try:
        # Determine current quarter
        now = datetime.datetime.utcnow()
        quarter = (now.month - 1) // 3 + 1
        cutoff = (now - datetime.timedelta(days=days)).date()

        idx_url = f'https://www.sec.gov/Archives/edgar/full-index/{now.year}/QTR{quarter}/form.idx'
        r = requests.get(idx_url, headers={**SEC_HEADERS, 'Accept': 'text/plain'}, timeout=30)
        r.raise_for_status()

        # Parse the fixed-width text file
        # Format: Form Type  |  Company Name  |  CIK  |  Date Filed  |  Filename
        # Lines before the separator (------) are headers; skip them.
        lines = r.text.splitlines()
        data_started = False
        results = []
        seen_cik = set()

        for line in lines:
            if not data_started:
                if line.startswith('----------'):
                    data_started = True
                continue

            # Form 4 lines start with '4 '
            if not line.startswith('4 '):
                continue

            # Fixed-width columns: form(12) company(62) cik(12) date(12) filename
            try:
                form_type   = line[0:12].strip()
                company     = line[12:74].strip()
                cik_raw     = line[74:86].strip()
                date_filed  = line[86:98].strip()
                filename    = line[98:].strip()

                if form_type != '4':
                    continue

                filed_date = datetime.date.fromisoformat(date_filed)
                if filed_date < cutoff:
                    continue

                cik = str(int(cik_raw))  # strip leading zeros
                if cik in seen_cik:
                    continue
                seen_cik.add(cik)

                results.append({
                    'cik':         cik,
                    'companyName': company,
                    'dateFiled':   date_filed,
                    'filename':    filename
                })

                if len(results) >= limit:
                    break

            except (ValueError, IndexError):
                continue

        return jsonify({
            'count':   len(results),
            'cutoff':  str(cutoff),
            'source':  idx_url,
            'filings': results
        })

    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ── SEC EDGAR — Form 4 detail + CIK→ticker resolution ────────────────────────
@app.route('/api/sec/form4')
def sec_form4():
    """
    Accepts a ticker symbol OR raw CIK number as 'ticker' param.
    Resolves to ticker and returns recent Form 4 filing metadata.
    """
    ticker_or_cik = request.args.get('ticker', '').strip()
    if not ticker_or_cik:
        return jsonify({'error': 'Missing ticker'}), 400

    try:
        tickers_r    = requests.get('https://www.sec.gov/files/company_tickers.json',
                                    headers=SEC_HEADERS, timeout=20)
        tickers_data = tickers_r.json()

        cik = ticker = name = None

        if re.match(r'^\d+$', ticker_or_cik):
            target_cik = int(ticker_or_cik)
            for entry in tickers_data.values():
                if entry.get('cik_str') == target_cik:
                    cik    = str(target_cik)
                    ticker = entry.get('ticker', '').upper()
                    name   = entry.get('title', '')
                    break
        else:
            ticker = ticker_or_cik.upper()
            for entry in tickers_data.values():
                if entry.get('ticker', '').upper() == ticker:
                    cik  = str(entry['cik_str'])
                    name = entry.get('title', '')
                    break

        if not cik:
            return jsonify({'error': 'Not found', 'input': ticker_or_cik}), 404

        padded  = cik.zfill(10)
        sub_url = f'{EDGAR_BASE}/submissions/CIK{padded}.json'
        sub_r   = requests.get(sub_url, headers=SEC_HEADERS, timeout=20)
        sub     = sub_r.json()

        ticker = sub.get('tickers', [ticker])[0] if sub.get('tickers') else ticker
        name   = sub.get('name', name)

        recent  = sub.get('filings', {}).get('recent', {})
        forms   = recent.get('form', [])
        dates   = recent.get('filingDate', [])
        accNums = recent.get('accessionNumber', [])
        docs    = recent.get('primaryDocument', [])

        form4s = []
        for i, f in enumerate(forms):
            if f == '4' and len(form4s) < 10:
                form4s.append({
                    'filingDate':      dates[i]   if i < len(dates)   else '',
                    'accessionNumber': accNums[i] if i < len(accNums) else '',
                    'primaryDocument': docs[i]    if i < len(docs)    else '',
                })

        return jsonify({'ticker': ticker, 'cik': cik, 'companyName': name, 'form4s': form4s})

    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ── SEC EDGAR — CIK lookup by ticker ─────────────────────────────────────────
@app.route('/api/sec/cik')
def sec_cik():
    ticker = request.args.get('ticker', '').upper().strip()
    if not ticker:
        return jsonify({'error': 'Missing ticker'}), 400
    try:
        r = requests.get('https://www.sec.gov/files/company_tickers.json',
                         headers=SEC_HEADERS, timeout=20)
        for entry in r.json().values():
            if entry.get('ticker', '').upper() == ticker:
                return jsonify({'ticker': ticker, 'cik': str(entry['cik_str']),
                                'name': entry.get('title', '')})
        return jsonify({'error': 'Not found'}), 404
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ── SEC EDGAR — dilution check via XBRL ──────────────────────────────────────
@app.route('/api/sec/dilution')
def sec_dilution():
    cik = request.args.get('cik', '').strip()
    if not cik:
        return jsonify({'error': 'Missing cik'}), 400
    try:
        padded = cik.zfill(10)
        url    = f'{EDGAR_BASE}/api/xbrl/companyfacts/CIK{padded}.json'
        r      = requests.get(url, headers=SEC_HEADERS, timeout=25)
        data   = r.json()

        us_gaap      = data.get('facts', {}).get('us-gaap', {})
        shares       = us_gaap.get('CommonStockSharesOutstanding', {})
        units        = shares.get('units', {})
        share_series = units.get('shares', list(units.values())[0] if units else [])

        annual = [
            {'end': e['end'], 'val': e['val'], 'form': e.get('form', '')}
            for e in share_series
            if e.get('form', '') in ('10-K', '10-K/A')
        ]
        annual.sort(key=lambda x: x['end'])

        for i in range(1, len(annual)):
            prev = annual[i-1]['val']
            curr = annual[i]['val']
            annual[i]['yoy_pct'] = round((curr - prev) / prev * 100, 1) if prev else None

        return jsonify({'cik': cik, 'annual_shares': annual})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ── SEC EDGAR — generic EFTS proxy ────────────────────────────────────────────
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
