from flask import Flask, request, jsonify, send_from_directory
import requests
import os
import re

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
    from_date = request.args.get('from_date', '')
    if not from_date:
        return jsonify({'error': 'Missing from_date'}), 400

    tickers = set()

    # Method 1 - EDGAR full text search paginated up to 40 pages
    try:
        base_url = 'https://efts.sec.gov/LATEST/search-index'
        for page in range(40):
            params = {
                'forms': '4',
                'dateRange': 'custom',
                'startdt': from_date,
                'from': str(page * 100),
                'size': '100'
            }
            r = requests.get(base_url, params=params, headers=SEC_HEADERS, timeout=20)
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
                    match = re.search(r'\(([A-Z]{1,5})\)', str(n))
                    if match:
                        tickers.add(match.group(1))
            if len(hits) < 100:
                break
    except Exception:
        pass

    # Method 2 - SEC RSS feed for latest filings
    try:
        rss_url = 'https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=4&dateb=&owner=include&count=100&search_text=&output=atom'
        r2 = requests.get(rss_url, headers=SEC_HEADERS, timeout=20)
        if r2.ok:
            content = r2.text
            for m in re.finditer(r'\(([A-Z]{1,5})\)', content):
                if len(m.group(1)) <= 5:
                    tickers.add(m.group(1))
    except Exception:
        pass

    # Remove false positives
    false_positives = {
        'LLC','INC','LTD','CORP','CO','LP','NA','US','USA',
        'SEC','CEO','CFO','COO','CTO','SVP','EVP','VP','THE',
        'AND','FOR','NEW','OLD','NET','COM','INT','GRP','HLD',
        'CAP','MGT','INV','SVC','ETF','ADR','PRF'
    }
    tickers = tickers - false_positives
    tickers = {t for t in tickers if re.match(r'^[A-Z]{1,5}$', t)}

    return jsonify({'tickers': list(tickers), 'count': len(tickers)})

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
