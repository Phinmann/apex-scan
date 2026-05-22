from flask import Flask, request, jsonify, send_from_directory
import requests
import os

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
    Fetches ALL insider purchase tickers from SEC EDGAR by paginating
    through every page of Form 4 filings in the lookback period.
    Returns a deduplicated list of ticker symbols.
    """
    from_date = request.args.get('from_date', '')
    if not from_date:
        return jsonify({'error': 'Missing from_date'}), 400

    tickers = set()
    base_url = 'https://efts.sec.gov/LATEST/search-index'
    page = 0
    page_size = 100
    max_pages = 20  # Up to 2000 filings per run

    while page < max_pages:
        try:
            params = {
                'q': '"transaction code" "P"',
                'dateRange': 'custom',
                'startdt': from_date,
                'forms': '4',
                'hits.hits._source': 'display_names,file_date',
                'hits.hits.total': 'true',
                '_source': 'display_names',
                'from': page * page_size,
                'size': page_size
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
                    import re
                    match = re.search(r'\(([A-Z]{1,5})\)', n)
                    if match:
                        tickers.add(match.group(1))
            # If we got fewer results than page size we're on the last page
            if len(hits) < page_size:
                break
            page += 1
        except Exception as e:
            break

    return jsonify({'tickers': list(tickers), 'count': len(tickers)})

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
