from flask import Flask, request, jsonify, send_from_directory
import requests
import os
import re
from datetime import datetime, timedelta

app = Flask(__name__, static_folder='static')

FINNHUB_BASE = 'https://finnhub.io/api/v1'
SEC_HEADERS = {
    'User-Agent': 'APEX/SCAN research@apex-scan.com',
    'Accept': 'application/json'
}

@app.route('/')
def index():
    return send_from_directory('static', 'index.html')

@app.route('/api/finnhub')
def finnhub_proxy():
    path = request.args.get('path')
    token = request.args.get('token')
    if not path or not token:
        return jsonify({'error': 'Missing path or token'}), 400
    try:
        url = FINNHUB_BASE + path
        params = dict(request.args)
        params.pop('path', None)
        params['token'] = token
        r = requests.get(url, params=params, timeout=15)
        return jsonify(r.json()), r.status_code
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/sec/insider-tickers')
def sec_insider_tickers():
    """
    Pulls insider purchase tickers from multiple SEC EDGAR sources.
    Uses the EDGAR company search RSS feed which updates in real time
    and contains ALL Form 4 filings, not just a search subset.
    """
    from_date = request.args.get('from_date', '')
    lookback_days = int(request.args.get('lookback_days', 90))

    tickers = set()
    from_dt = datetime.now() - timedelta(days=lookback_days)

    # ── METHOD 1: SEC EDGAR full-text search, multiple pages ──
    try:
        base_url = 'https://efts.sec.gov/LATEST/search-index'
        for page in range(40):  # up to 4,000 results
            params = {
                'forms': '4',
                'dateRange': 'custom',
                'startdt': from_date,
                'from': page * 100,
                'size': 100
            }
            r = requests.get(base_url, params=params,
                           headers=SEC_HEADERS, timeout=20)
            if not r.ok:
                break
            data = r.json()
            hits = data.get('hits', {}).get('hits', [])
            if not hits:
                break
            for h in hits:
                src = h.get('_source', {})
                names = src.get('display_names', [])
                for n in names:
                    m = re.search(r'\(([A-Z]{1,5})\)', str(n))
                    if m:
                        tickers.add(m.group(1))
            if len(hits) < 100:
                break
    except Exception:
        pass

    # ── METHOD 2: SEC EDGAR RSS feed for latest Form 4s ──
    try:
        rss_url = 'https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=4&dateb=&owner=include&count=100&search_text=&output=atom'
        r2 = requests.get(rss_url, headers=SEC_HEADERS, timeout=20)
        if r2.ok:
            content = r2.text
            # Extract tickers from category terms
            for m in re.finditer(r'<category[^>]*term="([A-Z]{1,5})"', content):
                tickers.add(m.group(1))
            # Extract from titles like "CompanyName (TICK)"
            for m in re.finditer(r'\(([A-Z]{1,5})\)', content):
                if len(m.group(1)) <= 5:
                    tickers.add(m.group(1))
    except Exception:
        pass

    # ── METHOD 3: SEC EDGAR company search for Form 4 filers ──
    try:
        for start in range(0, 1000, 100):
            url3 = 'https://efts.sec.gov/LATEST/search-index'
            params3 = {
                'q': 'transaction',
                'forms': '4',
                'dateRange': 'custom',
                'startdt': from_date,
                'from': start,
                'size': 100
            }
            r3 = requests.get(url3, params=params3,
                            headers=SEC_HEADERS, timeout=20)
            if not r3.ok:
                break
            data3 = r3.json()
            hits3 = data3.get('hits', {}).get('hits', [])
            if not hits3:
                break
            for h in hits3:
                src = h.get('_source', {})
                for field in ['display_names', 'period_of_report',
                             'entity_name']:
                    val = src.get(field, '')
                    if isinstance(val, list):
                        for v in val:
                            m = re.search(r'\(([A-Z]{1,5})\)', str(v))
                            if m:
                                tickers.add(m.group(1))
                    elif isinstance(val, str):
                        m = re.search(r'\(([A-Z]{1,5})\)', val)
                        if m:
                            tickers.add(m.group(1))
            if len(hits3) < 100:
                break
    except Exception:
        pass

    # ── METHOD 4: Direct SEC EDGAR submissions for known micro cap CIKs ──
    # Pull the most recent Form 4 filings index
    try:
        idx_url = 'https://www.sec.gov/Archives/edgar/full-index/2025/QTR2/company.idx'
        r4 = requests.get(idx_url, headers=SEC_HEADERS, timeout=30)
        if r4.ok:
            lines = r4.text.split('\n')
            for line in lines:
                if '4 ' in line or '\t4\t' in line:
                    # Extract ticker-like patterns
                    parts = line.split()
                    for part in parts:
                        if re.match(r'^[A-Z]{1,5}$', part):
                            tickers.add(part)
    except Exception:
        pass

    # Clean up false positives
    false_positives = {
        'LLC','INC','LTD','CORP','CO','LP','NA','US','USA',
        'SEC','CEO','CFO','COO','CTO','SVP','EVP','VP','THE',
        'AND','FOR','NEW','OLD','NET','COM','INT','GRP','HLD',
        'CAP','MGT','INV','SVC','GRP','ETF','ADR','PRF'
    }
    tickers = tickers - false_positives

    # Only keep valid ticker patterns (1-5 uppercase letters)
    tickers = {t for t in tickers if re.match(r'^[A-Z]{1,5}$', t)}

    return jsonify({
        'tickers': list(tickers),
        'count': len(tickers),
        'sources': 'SEC EDGAR full-text search + RSS + index'
    })

@app.route('/api/sec/edgar')
def sec_edgar_proxy():
    path = request.args.get('path')
    if not path:
        return jsonify({'error': 'Missing path'}), 400
    try:
        url = 'https://efts.sec.gov' + path
        r = requests.get(url, headers=SEC_HEADERS, timeout=20)
        return jsonify(r.json()), r.status_code
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/sec/data')
def sec_data_proxy():
    path = request.args.get('path')
    if not path:
        return jsonify({'error': 'Missing path'}), 400
    try:
        url = 'https://data.sec.gov' + path
        r = requests.get(url, headers=SEC_HEADERS, timeout=20)
        return jsonify(r.json()), r.status_code
    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
