"""Exercise the shipped YAML against a real Docker engine, without source mounts.

Requires only Python's standard library and Docker Compose. Each run uses a unique
project, random credentials and loopback ports. Only its own volumes are removed.
"""
import argparse
import http.cookiejar
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import tempfile
import urllib.request


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--full', action='store_true', help='Also start and verify DTK')
    parser.add_argument('--image', default='pushytao/notify:0.1.0')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    source = root / 'deploy' / ('compose.full.yaml' if args.full else 'compose.image.yaml')
    project = 'notify-smoke-' + secrets.token_hex(5)
    password = secrets.token_hex(24)
    port = free_port()
    config = source.read_text(encoding='utf-8')
    config = config.replace('APP_SECRET_KEY: ""', 'APP_SECRET_KEY: "' + secrets.token_hex(32) + '"')
    config = config.replace('ADMIN_PASSWORD: ""', 'ADMIN_PASSWORD: "' + password + '"')
    config = config.replace('DTK_SECRET_KEY: ""', 'DTK_SECRET_KEY: "' + secrets.token_hex(32) + '"')
    # URL-reserved characters exercise the connection-string encoder, not just hex passwords.
    config = config.replace('&postgres-password ""', '&postgres-password "' + secrets.token_hex(16) + ':@/+#%"')
    config = config.replace('&redis-password ""', '&redis-password "' + secrets.token_hex(16) + ':@/+#%"')
    config = config.replace('0.0.0.0:8080:8080', f'127.0.0.1:{port}:8080')
    config = config.replace('127.0.0.1:8000:8000', f'127.0.0.1:{free_port()}:8000')
    env = dict(os.environ, NOTIFY_IMAGE=args.image, COMPOSE_ANSI='never')
    # Ignore a caller's unrelated Compose project/environment overrides.
    for key in ('COMPOSE_FILE', 'COMPOSE_PROJECT_NAME', 'COMPOSE_PROFILES', 'COMPOSE_ENV_FILES'):
        env.pop(key, None)

    with tempfile.TemporaryDirectory(prefix=project) as directory:
        path = Path(directory) / 'compose.yaml'
        path.write_text(config, encoding='utf-8')
        command = ['docker', 'compose', '-p', project, '-f', str(path)]

        def compose(*arguments, capture=False):
            result = subprocess.run(command + list(arguments), env=env, check=True,
                                    capture_output=capture, text=True, encoding='utf-8')
            return result.stdout if capture else None

        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()),
        )

        def request(route, payload=None, csrf=None):
            headers = {'Content-Type': 'application/json'}
            if csrf:
                headers['X-CSRF-Token'] = csrf
            req = urllib.request.Request(f'http://127.0.0.1:{port}' + route,
                                         data=json.dumps(payload).encode() if payload is not None else None,
                                         headers=headers)
            with opener.open(req, timeout=15) as response:
                return response.read().decode()

        try:
            compose('config', '--quiet')
            compose('up', '-d', '--no-build', '--wait', '--wait-timeout', '240')
            assert '长虹' in request('/'), 'Public page unavailable'
            assert request('/admin'), 'Admin page unavailable'
            csrf = json.loads(request('/api/login', {'password': password}))['csrf']
            author = {'name': 'Image smoke test', 'source_id': 'smoke-test', 'enabled': False}
            request('/api/admin/authors', author, csrf)
            state = json.loads(request('/api/admin/state'))
            assert state['status']['worker_heartbeat'], 'Web cannot see worker heartbeat'
            if args.full:
                compose('exec', '-T', 'worker', 'python', '-c',
                        "import urllib.request; urllib.request.urlopen('http://collector:8000/readyz',timeout=10)")
            # Recreate containers, retaining the data volume; login and data must survive.
            compose('up', '-d', '--no-build', '--force-recreate', '--wait', '--wait-timeout', '240', 'web', 'worker')
            request('/api/login', {'password': password})
            state = json.loads(request('/api/admin/state'))
            assert any(a['name'] == author['name'] for a in state['authors']), 'Data lost after recreation'
            print('PASS: public/admin pages, login, shared worker heartbeat, persisted data' +
                  (', DTK migrations/readiness/internal connectivity' if args.full else ''), flush=True)
        except Exception:
            compose('ps', '-a')
            # Avoid dumping DTK's one-time setup token into CI logs.
            compose('logs', '--tail=60', 'web', 'worker')
            raise
        finally:
            # Unique generated project; never touches a deployment's named volumes.
            compose('down', '--volumes', '--remove-orphans')


if __name__ == '__main__':
    main()
