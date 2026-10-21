import hashlib
import http.client
import json
import os
from pathlib import Path
import shutil
from urllib.parse import urlencode, urlsplit
from urllib.request import urlopen
import zipfile

event = json.loads(Path(os.environ['GITHUB_EVENT_PATH']).read_text())
source_url = event['client_payload']['source_artifact_url']
assert urlsplit(source_url).scheme == 'https'
assert urlsplit(source_url).hostname.endswith('.blob.core.windows.net')
name = 'exvi.tracer-0.65.1-macOS-arm64-Internal-corresponding-source.tar.gz'
expected = '371e7a21647d5afcae4dfbd7a70a5276794c668e630ac7d748ca6b97f1e917ba'
# The temporary artifact URL stays in memory; never print or persist it.
with urlopen(source_url, timeout=120) as response, open('artifact.zip', 'wb') as output:
    shutil.copyfileobj(response, output, 1024 * 1024)
with zipfile.ZipFile('artifact.zip') as archive:
    members = [x for x in archive.namelist() if x.split('/')[-1] == name]
    assert len(members) == 1
    with archive.open(members[0]) as source, open(name, 'wb') as output:
        shutil.copyfileobj(source, output, 1024 * 1024)
with open(name, 'rb') as source:
    actual = hashlib.file_digest(source, 'sha256').hexdigest()
assert actual == expected and Path(name).stat().st_size == 986515953
connection = http.client.HTTPSConnection('uploads.github.com', timeout=180)
path = '/repos/exvi-git/exvi-tracer-beta-downloads/releases/419164556/assets?' + urlencode({'name': name})
connection.putrequest('POST', path)
connection.putheader('Authorization', 'Bearer ' + os.environ['GH_TOKEN'])
connection.putheader('User-Agent', 'Exvi-source-release-relay')
connection.putheader('Content-Type', 'application/gzip')
connection.putheader('Content-Length', str(Path(name).stat().st_size))
connection.endheaders()
with open(name, 'rb') as source:
    while block := source.read(1024 * 1024):
        connection.send(block)
response = connection.getresponse()
body = response.read()
assert response.status == 201, 'GitHub asset upload HTTP ' + str(response.status)
asset = json.loads(body)
assert asset['digest'] == 'sha256:' + expected and asset['size'] == 986515953
print(json.dumps({k: asset[k] for k in ('id', 'name', 'size', 'digest', 'state')}))
