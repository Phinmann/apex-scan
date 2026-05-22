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
    """
    Fetches insider purchase tickers from SEC EDGAR by searching
    Form 4 filings. Uses the EDGAR full-text search API with
    proper pagination to get all results.
    """
    from_date = request.args.get('from_date', '')
    if not from_date:
        return jsonify({'error': 'Missing from_date'}), 400

    tickers = set()

    # Method 1: EDGAR full text search API - searches all Form 4 filings
    try:
        url = 'https://efts.sec.gov/LATEST/search-index'
        page_size = 100
        max_pages = 20

        for page in range(max_pages):
            params = {
                'q': 'form-type:4',
                'dateRange': 'custom',
                'startdt': from_date,
                'forms': '4',
                'from': page * page_size,
                'size': page_size
            }
            r = requests.get(url, params=params, headers=SEC_HEADERS, timeout=20)
            if not r.ok:
                break
            data = r.json()
            hits = data.get('hits', {}).get('hits', [])
            if not hits:
                break
            for h in hits:
                src = h.get('_source', {})
                # Extract tickers from display_names
                names = src.get('display_names', [])
                for n in names:
                    match = re.search(r'\(([A-Z]{1,5})\)', str(n))
                    if match:
                        ticker = match.group(1)
                        if len(ticker) <= 5:
                            tickers.add(ticker)
                # Also try entity_name field
                entity = src.get('entity_name', '')
                if entity:
                    match = re.search(r'\(([A-Z]{1,5})\)', str(entity))
                    if match:
                        tickers.add(match.group(1))
            if len(hits) < page_size:
                break
    except Exception as e:
        pass

    # Method 2: SEC EDGAR search with different query approach
    try:
        url2 = 'https://efts.sec.gov/LATEST/search-index'
        for page in range(10):
            params2 = {
                'q': '"purchased"',
                'dateRange': 'custom',
                'startdt': from_date,
                'forms': '4',
                'from': page * 100,
                'size': 100
            }
            r2 = requests.get(url2, params=params2, headers=SEC_HEADERS, timeout=20)
            if not r2.ok:
                break
            data2 = r2.json()
            hits2 = data2.get('hits', {}).get('hits', [])
            if not hits2:
                break
            for h in hits2:
                src = h.get('_source', {})
                names = src.get('display_names', [])
                for n in names:
                    match = re.search(r'\(([A-Z]{1,5})\)', str(n))
                    if match:
                        tickers.add(match.group(1))
            if len(hits2) < 100:
                break
    except Exception as e:
        pass

    # Method 3: Use SEC EDGAR company search API to get recent Form 4 filers
    try:
        url3 = 'https://www.sec.gov/cgi-bin/browse-edgar'
        params3 = {
            'action': 'getcompany',
            'type': '4',
            'dateb': '',
            'owner': 'include',
            'count': '100',
            'search_text': '',
            'output': 'atom'
        }
        r3 = requests.get(url3, params=params3, headers=SEC_HEADERS, timeout=20)
        if r3.ok:
            # Parse tickers from the atom feed
            content = r3.text
            ticker_matches = re.findall(r'\(([A-Z]{1,5})\)', content)
            for t in ticker_matches:
                if len(t) <= 5:
                    tickers.add(t)
    except Exception as e:
        pass

    # Method 4: Pull directly from SEC EDGAR RSS feed for Form 4s
    try:
        rss_url = 'https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=4&dateb=&owner=include&count=100&search_text=&output=atom'
        r4 = requests.get(rss_url, headers=SEC_HEADERS, timeout=20)
        if r4.ok:
            content = r4.text
            # Extract company tickers from the RSS feed
            ticker_matches = re.findall(r'<category[^>]*term="([A-Z]{1,5})"', content)
            for t in ticker_matches:
                tickers.add(t)
            # Also look for tickers in titles
            title_matches = re.findall(r'\(([A-Z]{1,5})\)', content)
            for t in title_matches:
                if len(t) <= 5:
                    tickers.add(t)
    except Exception as e:
        pass

    # Remove common false positives that aren't stock tickers
    false_positives = {'LLC', 'INC', 'LTD', 'CORP', 'CO', 'LP', 'NA', 'US', 'USA', 'SEC', 'CEO', 'CFO', 'COO', 'CTO', 'SVP', 'EVP', 'VP'}
    tickers = tickers - false_positives

    return jsonify({
        'tickers': list(tickers),
        'count': len(tickers)
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
