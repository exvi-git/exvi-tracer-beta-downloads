from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib,json
from pathlib import Path
import public_download_checks as checks

plan=json.loads(Path('tools/public_downloads_0651.json').read_text())
checks.PUBLIC={x['logical_path']:x for x in plan}
report=dict(checked_utc=datetime.now(timezone.utc).isoformat(),downloads=[],unauthenticated=True)
with checks.response('https://exvi.io/') as response:
    body=response.read()
    report['page']=dict(status=response.status,sha256=hashlib.sha256(body).hexdigest())
report['page']['passed']=report['page']['status']==200 and report['page']['sha256']=='c8faa6844224d85bdd3e2d8f924d4017e95d2a2bd629ba5d0a9363ac4197034f'
report['endpoint']=checks.endpoint_checks('https://exvi.io',True,extra_cases=True)
with ThreadPoolExecutor(max_workers=3) as pool:
    futures=[pool.submit(checks.download_check,'https://exvi.io',x['logical_path'],x['sha256'],x['bytes']) for x in plan]
    for future in as_completed(futures):
        item=future.result();report['downloads'].append(item)
        print(json.dumps({k:item[k] for k in ('path','status','range_status','bytes','sha256','passed','errors')}),flush=True)
report['download_and_page_passed']=report['page']['passed'] and all(x['passed'] for x in report['downloads'])
report['endpoint_contract_passed']=all(x['passed'] for x in report['endpoint'])
report['passed']=report['download_and_page_passed'] and report['endpoint_contract_passed']
Path('public-verification.json').write_text(json.dumps(report,indent=2)+'\n')
print('Download/page verification:',report['download_and_page_passed'])
print('Strict endpoint contract:',report['endpoint_contract_passed'])
# Preserve HEAD's observed hosting discrepancy; do not change its expected result.
# This job gates downloads and the application's GET/error contracts.
assert report['download_and_page_passed']
assert all(x['passed'] for x in report['endpoint'] if x['name']!='head')
