"""Verify the real endpoint and stream final HTTPS downloads without saving binaries."""
import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
SUPPLIED = ROOT / 'releases/exvi.tracer-0.64.1-privacy-r1'
PUBLIC_MAP = ROOT / 'releases/exvi.tracer-0.64.1-github-assets/public-downloads.json'
CURRENT = ROOT / 'releases/exvi.tracer-current.json'
if CURRENT.exists():
    PUBLIC_MAP = ROOT / json.loads(CURRENT.read_text())['public_downloads']
PUBLIC = {x['logical_path']: x for x in json.loads(PUBLIC_MAP.read_text())} if PUBLIC_MAP.exists() else {}
ENDPOINT = '/api/odp-beta-status.php'
QUERY = '?product_id=exvi.tracer&channel=beta'
HEADERS = {'User-Agent': 'Exvi-release-verification', 'Accept-Encoding': 'identity'}
SECURITY_HEADERS = {
    'x-content-type-options': 'nosniff',
    'referrer-policy': 'no-referrer',
    'content-security-policy': "default-src 'none'; frame-ancestors 'none'",
    'cross-origin-resource-policy': 'same-site',
    'pragma': 'no-cache',
}


def response(url, method='GET', extra_headers=None):
    request = Request(url, method=method, headers={**HEADERS, **(extra_headers or {})})
    try:
        return urlopen(request, timeout=90)  # Default TLS validation; never -k/unverified.
    except HTTPError as error:
        return error


def endpoint_checks(base, active, end='2027-01-01T00:00:00Z', extra_cases=False):
    cases = [('valid', 'GET', QUERY, 200), ('missing', 'GET', '', 400),
             ('extra', 'GET', QUERY + '&extra=1', 400), ('post', 'POST', QUERY, 405)]
    if extra_cases:
        cases += [('wrong_product', 'GET', '?product_id=other&channel=beta', 400),
                  ('wrong_channel', 'GET', '?product_id=exvi.tracer&channel=stable', 400),
                  ('head', 'HEAD', QUERY, 405)]
    results = []
    for name, method, query, expected in cases:
        url = base.rstrip('/') + ENDPOINT + query
        item = {'name': name, 'method': method, 'url': url, 'expected_status': expected}
        errors = []
        try:
            with response(url, method) as res:
                # Hosts can add a second CSP. Multiple CSPs are enforced together;
                # do not discard the application's policy by keeping only the last.
                header_values = {k.lower(): res.headers.get_all(k) for k in res.headers}
                headers = {k: ', '.join(v) for k, v in header_values.items()}
                body = res.read(65537)
                item.update(status=res.status, final_url=res.url, headers=headers,
                            bytes=len(body), tls_certificate_validated=url.startswith('https:'))
            if item['status'] != expected: errors.append('Unexpected HTTP status')
            if item['final_url'] != url: errors.append('Unexpected redirect')
            if len(body) >= 65536: errors.append('Response must be under 64 KiB')
            if not headers.get('content-type', '').startswith('application/json'):
                errors.append('Content-Type is not application/json')
            if 'no-store' not in headers.get('cache-control', ''): errors.append('Missing no-store')
            if 'set-cookie' in headers: errors.append('Endpoint sets a cookie')
            for key, value in SECURITY_HEADERS.items():
                if value not in header_values.get(key, []): errors.append('Wrong/missing ' + key)
            if expected == 405 and headers.get('allow') != 'GET': errors.append('Wrong Allow header')
            if method != 'HEAD':
                try:
                    data = json.loads(body)
                    item['json'] = data
                except (ValueError, UnicodeError):
                    errors.append('Invalid JSON'); data = None
                if expected == 200 and isinstance(data, dict):
                    fields = {'schema_version', 'product_id', 'beta_active', 'message',
                              'server_time_utc', 'phase_ends_at_utc'}
                    if set(data) != fields: errors.append('Unexpected response fields')
                    if data.get('schema_version') != 'exvi.beta-entitlement/1': errors.append('Wrong schema')
                    if data.get('product_id') != 'exvi.tracer': errors.append('Wrong product')
                    if type(data.get('beta_active')) is not bool or data['beta_active'] != active:
                        errors.append('Wrong beta_active value/type')
                    for key in fields - {'beta_active'}:
                        if type(data.get(key)) is not str: errors.append('Wrong type for ' + key)
                    if data.get('phase_ends_at_utc') != end: errors.append('Wrong cutoff')
                    try:
                        stamp = datetime.strptime(data['server_time_utc'], '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc)
                        if abs((datetime.now(timezone.utc) - stamp).total_seconds()) > 120:
                            errors.append('Stale server time')
                    except (KeyError, ValueError, TypeError): errors.append('Invalid server time')
                elif expected == 400 and data != {'error': 'invalid_request'}:
                    errors.append('Wrong invalid-request response')
                elif expected == 405 and data != {'error': 'method_not_allowed'}:
                    errors.append('Wrong method response')
                elif expected == 200:
                    errors.append('Response is not an object')
        except (OSError, URLError) as error:
            errors.append(str(error))
        item.update(errors=errors, passed=not errors)
        results.append(item)
    return results


def release_files():
    if PUBLIC:
        return [(path, item['sha256'], item['bytes']) for path, item in PUBLIC.items()]
    entries = []
    for line in (SUPPLIED / 'SHA256SUMS.txt').read_text().splitlines():
        expected, relative = line.split(None, 1)
        entries.append((relative, expected, (SUPPLIED / relative).stat().st_size))
    sums = SUPPLIED / 'SHA256SUMS.txt'
    entries.append(('SHA256SUMS.txt', hashlib.sha256(sums.read_bytes()).hexdigest(), sums.stat().st_size))
    return entries


def download_check(base, relative, expected, size):
    url = PUBLIC[relative]['url'] if relative in PUBLIC else base.rstrip('/') + '/downloads/exvi.tracer-0.64.1/' + relative
    item = {'path': relative, 'url': url, 'expected_bytes': size, 'expected_sha256': expected}
    errors = []
    suffix = Path(relative).suffix
    mime = {'.exe': 'application/x-msdownload', '.zip': 'application/zip',
            '.dmg': 'application/x-apple-diskimage', '.pkg': 'application/octet-stream',
            '.gz': 'application/gzip', '.json': 'application/json', '.txt': 'text/plain'}[suffix]
    try:
        with response(url, extra_headers={'Range': 'bytes=0-1023'}) as res:
            partial = res.read(1024)
            item['range_status'] = res.status
            item['content_range'] = res.headers.get('Content-Range')
            if size >= 1024 and res.status == 206:
                if item['content_range'] != f'bytes 0-1023/{size}': errors.append('Wrong Content-Range')
            elif res.status == 200:
                item['range_supported'] = False
                if res.headers.get('Accept-Ranges') == 'bytes': errors.append('Advertises ranges but ignores Range')
            elif res.status != 206:
                errors.append('Range request failed')
        # A full GET is mandatory. Hash the response stream; never infer from ETag.
        with response(url) as res:
            headers = {k.lower(): v for k, v in res.headers.items()}
            final = urlsplit(res.url)
            # GitHub release redirects contain short-lived asset tokens. Record
            # the stable public URL above, and omit ephemeral query credentials.
            item.update(status=res.status, final_url=final._replace(query='',fragment='').geturl(), headers=headers,
                        tls_certificate_validated=url.startswith('https:'))
            if res.status != 200: errors.append('Download is not HTTP 200')
            github_asset = url.startswith('https://github.com/exvi-git/exvi-tracer-beta-downloads/releases/download/')
            if res.url != url and not (github_asset and final.scheme == 'https' and final.hostname == 'release-assets.githubusercontent.com'):
                errors.append('Unexpected redirect')
            actual_mime=headers.get('content-type', '').split(';')[0].strip()
            allowed={mime}
            if github_asset and 'attachment' in headers.get('content-disposition',''):
                allowed.add('application/octet-stream')
            if actual_mime not in allowed: errors.append('Wrong MIME type')
            if headers.get('content-encoding', 'identity') != 'identity': errors.append('Download is transformed')
            if 'content-length' in headers and headers['content-length'] != str(size): errors.append('Wrong Content-Length')
            sha = hashlib.sha256(); count = 0
            while block := res.read(1024 * 1024):
                count += len(block); sha.update(block)
                if count > size: errors.append('Response exceeds expected size'); break
            item.update(bytes=count, sha256=sha.hexdigest())
            if count != size: errors.append('Wrong file size')
            if sha.hexdigest() != expected: errors.append('SHA-256 mismatch')
    except (OSError, URLError) as error:
        errors.append(str(error))
    item.update(errors=errors, passed=not errors)
    return item


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', default='https://exvi.io')
    parser.add_argument('--expect-active', choices=['true', 'false'], default='true')
    parser.add_argument('--end', default='2027-01-01T00:00:00Z')
    parser.add_argument('--endpoint-only', action='store_true')
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    report = {'checked_utc': datetime.now(timezone.utc).isoformat(), 'base_url': args.base_url,
              'endpoint': endpoint_checks(args.base_url, args.expect_active == 'true', args.end), 'downloads': []}
    for check in report['endpoint']:
        print(f"Endpoint {check['name']}: HTTP {check.get('status')} {'PASS' if check['passed'] else 'FAIL'}", flush=True)
    if not args.endpoint_only:
        for relative, expected, size in release_files():
            check = download_check(args.base_url, relative, expected, size)
            report['downloads'].append(check)
            print(f"Download {relative}: HTTP {check.get('status')} {'PASS' if check['passed'] else 'FAIL'}", flush=True)
    report['passed'] = all(c['passed'] for c in report['endpoint'] + report['downloads'])
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    raise SystemExit(0 if report['passed'] else 1)


if __name__ == '__main__':
    main()
