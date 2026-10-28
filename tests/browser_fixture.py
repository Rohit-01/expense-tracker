"""JSON pipe transport for browser tests. No real network, credentials, or vault."""
import base64
import json
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from werkzeug.security import generate_password_hash
from production import create_app

with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    auth = root / 'auth.json'
    auth.write_text(json.dumps(dict(username='tester', passwordHash=generate_password_hash('isolated-browser-password', method='scrypt'))))
    app = create_app(dict(POCKET_DOMAIN='pocket.example.test', POCKET_AUTH_FILE=str(auth),
        POCKET_DATA_DIR=str(root/'data'), POCKET_VAULT=str(root/'vault')))
    client = app.test_client(use_cookies=False)
    print(json.dumps(dict(ready=True)), flush=True)
    for line in sys.stdin:
        item = json.loads(line)
        response = client.open(item['url'], method=item['method'], headers=item['headers'],
                               data=item.get('body'), base_url='https://pocket.example.test')
        print(json.dumps(dict(id=item['id'], status=response.status_code,
            headers=dict(response.headers), body=base64.b64encode(response.data).decode())), flush=True)
