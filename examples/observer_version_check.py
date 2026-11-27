"""Public, credential-free deployment identity audit. Does not open an SSE stream.

python examples/observer_version_check.py --base-url https://HOST --proof result.json
An offline/error response is never hashed or classified as deployed JavaScript.
"""
import argparse
import hashlib
import json
import re
import subprocess
import tempfile
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlsplit

BASELINE = '6b6d262295d56d9f71546a6b79f6eeb0f0caded00fc242c5b9cd28cf1e4274e9'


class Scripts(HTMLParser):
    def __init__(self):
        super().__init__(); self.sources = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'script' and attrs.get('src'):
            self.sources.append(attrs['src'])


def get(url):
    with tempfile.TemporaryDirectory() as tmp:
        body, headers = Path(tmp)/'body', Path(tmp)/'headers'
        result = subprocess.run(['curl', '--http1.1', '--max-time', '20', '-sS',
            '-H', 'Cache-Control: no-cache', '-H', 'ngrok-skip-browser-warning: version-audit',
            '-D', str(headers), '-o', str(body), url], capture_output=True, text=True)
        raw = headers.read_text() if headers.exists() else ''
        content = body.read_bytes() if body.exists() else b''
        codes = re.findall(r'^HTTP/\S+ (\d+)', raw, re.M)
        safe_headers = '\n'.join(line for line in raw.splitlines()
                                 if not line.lower().startswith(('set-cookie:', 'authorization:')))
        text = content.decode('utf-8', 'replace')
        errors = sorted(set(re.findall(r'ERR_NGROK_\d+', raw + text)))
        return {'url': url, 'at_utc': datetime.now(timezone.utc).isoformat(),
                'http_status': int(codes[-1]) if codes else None,
                'curl_exit': result.returncode, 'curl_error': result.stderr,
                'headers': safe_headers, 'ngrok_errors': errors,
                'response_body_sha256': hashlib.sha256(content).hexdigest()}, text


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', required=True); parser.add_argument('--proof', required=True)
    args = parser.parse_args(); base = args.base_url.rstrip('/')
    parsed = urlsplit(base)
    if parsed.scheme not in ('http', 'https') or not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment:
        parser.error('Use a server origin without credentials, query or fragment')
    report = {'baseline_commit': '9f94c5a01d4d0ca843cfdb3a322df5968671935f',
              'baseline_js_sha256': BASELINE, 'requests': [], 'deployed_js_sha256': None,
              'conclusion': 'BLOCKED: deployment identity not established',
              'transport_validation': 'NOT RUN: version gate only'}
    deployed = None; script_sources = []
    for path in ('/observer', '/visualizer?mode=observer'):
        record, body = get(base + path)
        is_observer = record['http_status'] == 200 and not record['ngrok_errors'] and 'Research Observer' in body
        record['ember_observer_html'] = is_observer
        if is_observer:
            parser_html = Scripts(); parser_html.feed(body)
            script_sources.extend(parser_html.sources)
            record['script_sources'] = parser_html.sources
            record['old_body_watchdog'] = 'setTimeout(()=>active.abort(),10000)' in body
            record['service_worker_registration'] = 'serviceWorker.register' in body
            if record['old_body_watchdog']:
                report['conclusion'] = 'DEPLOYMENT VERSION MISMATCH CONFIRMED'
        report['requests'].append(record)
    script_sources = list(dict.fromkeys(script_sources + ['/v1/observer/transport.js']))
    for source in script_sources:
        url = urljoin(base + '/', source)
        if urlsplit(url).netloc != parsed.netloc:
            continue  # Never follow arbitrary cross-origin references.
        record, body = get(url)
        is_js = record['http_status'] == 200 and not record['ngrok_errors'] and 'application/javascript' in record['headers'].lower()
        record['javascript_response'] = is_js
        if is_js:
            deployed = record['response_body_sha256']
            record['matches_9f94c5a'] = deployed == BASELINE
            record['native_eventsource'] = 'new EventSource(' in body
            record['old_body_watchdog'] = 'setTimeout(()=>active.abort(),10000)' in body
            record['rest_timeout_only_signature'] = "c.abort('REST timeout'),10000" in body
            # Prefer the URL actually referenced by the HTML over a legacy alias.
            if report['deployed_js_sha256'] is None:
                report['deployed_js_sha256'] = deployed
                report['loaded_asset_url'] = url
        report['requests'].append(record)
    record, body = get(base + '/v1/observer/build')
    if record['http_status'] == 200 and not record['ngrok_errors']:
        try: report['build_metadata'] = json.loads(body)
        except ValueError: pass
    report['requests'].append(record)
    if report['conclusion'].startswith('BLOCKED') and report['deployed_js_sha256']:
        report['conclusion'] = ('DEPLOYED ASSET MATCHES 9f94c5a; transport not tested'
            if report['deployed_js_sha256'] == BASELINE else 'DEPLOYMENT VERSION MISMATCH CONFIRMED')
    Path(args.proof).write_text(json.dumps(report, indent=2) + '\n')
    print(report['conclusion'])


if __name__ == '__main__':
    main()
