"""Exercise the shipped YAML against a real Docker engine, without source mounts.

Requires only Python's standard library and Docker Compose. Each run uses a unique
project, random credentials, temporary bind mounts and loopback ports.
"""
import argparse
import http.cookiejar
import json
import os
import re
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
    parser.add_argument('--image', default='pushytao/notify:latest')
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
    config = config.replace('0.0.0.0:8888:8080', f'127.0.0.1:{port}:8080')
    config = config.replace('127.0.0.1:8000:8000', f'127.0.0.1:{free_port()}:8000')
    env = dict(os.environ, NOTIFY_IMAGE=args.image, COMPOSE_ANSI='never')
    # Ignore a caller's unrelated Compose project/environment overrides.
    for key in ('COMPOSE_FILE', 'COMPOSE_PROJECT_NAME', 'COMPOSE_PROFILES', 'COMPOSE_ENV_FILES'):
        env.pop(key, None)

    with tempfile.TemporaryDirectory(prefix=project) as directory:
        runtime_data = Path(directory) / 'data'
        runtime_data.mkdir()
        env['CHANGHONG_DATA_DIR'] = runtime_data.as_posix()
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
            resolved = json.loads(compose('config', '--format', 'json', capture=True))
            mounts = resolved['services']['web']['volumes']
            assert any(v['type'] == 'bind' and v['target'] == '/data' for v in mounts), 'App data must use a host bind mount'
            if args.full:
                # A stale HA database must block a silent switch to an empty store.
                legacy = runtime_data / 'dtk' / 'postgres'
                legacy.mkdir(parents=True)
                marker = legacy / 'PG_VERSION'
                marker.write_text('17\n', encoding='utf-8')
                probe = subprocess.run(command + ['run', '--rm', '--no-deps', 'init-data'],
                                       env=env, capture_output=True, text=True, encoding='utf-8')
                assert probe.returncode != 0 and 'Existing HA database detected' in probe.stderr + probe.stdout
                assert marker.read_text(encoding='utf-8') == '17\n'
                assert not (runtime_data / 'dtk' / 'postgres-lite').exists(), 'Old database guard initialized a new store'
                marker.unlink()
                legacy.rmdir()
            compose('up', '-d', '--no-build', '--wait', '--wait-timeout', '240')
            assert (runtime_data / 'app').is_dir(), 'Initializer did not create the host app directory'
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
                extensions = compose('exec', '-T', 'postgres', 'psql', '-U', 'dtk', '-d', 'dtk', '-Atc',
                                     "SELECT extname FROM pg_extension WHERE extname IN ('timescaledb', 'pg_trgm')", capture=True)
                assert set(extensions.split()) == {'timescaledb', 'pg_trgm'}, 'DTK database extensions missing'
                compose('exec', '-T', 'postgres', 'psql', '-U', 'dtk', '-d', 'dtk', '-v', 'ON_ERROR_STOP=1', '-c',
                        'CREATE TABLE notify_smoke_persistence (value integer); INSERT INTO notify_smoke_persistence VALUES (42);')
                compose('up', '-d', '--no-deps', '--no-build', '--force-recreate', '--wait', '--wait-timeout', '120', 'postgres')
                saved = compose('exec', '-T', 'postgres', 'psql', '-U', 'dtk', '-d', 'dtk', '-Atc',
                                'SELECT value FROM notify_smoke_persistence', capture=True)
                assert saved.strip() == '42', 'DTK database lost after PostgreSQL recreation'
                compose('exec', '-T', 'postgres', 'psql', '-U', 'dtk', '-d', 'dtk', '-c', 'DROP TABLE notify_smoke_persistence;')
            # Recreate containers, retaining the data volume; login and data must survive.
            compose('up', '-d', '--no-build', '--force-recreate', '--wait', '--wait-timeout', '240', 'web', 'worker')
            request('/api/login', {'password': password})
            state = json.loads(request('/api/admin/state'))
            assert any(a['name'] == author['name'] for a in state['authors']), 'Data lost after recreation'
            print('PASS: host bind mount, directory initialization, public/admin pages, login, shared worker heartbeat, persisted data' +
                  (', HA data guard, DTK extensions/migrations/readiness, PostgreSQL persistence' if args.full else ''), flush=True)
        except Exception:
            compose('ps', '-a')
            # Avoid dumping DTK's one-time setup token into CI logs.
            compose('logs', '--tail=60', 'web', 'worker')
            if args.full:
                log = compose('logs', '--tail=35', 'migrate', capture=True)
                log = re.sub(r'postgresql[^\s]*://[^\s]*', '[database URL redacted]', log)
                print(log)
            raise
        finally:
            # Unique generated project; never touches a deployment's named volumes.
            compose('down', '--volumes', '--remove-orphans')
            # Linux containers create files with their own UID. Permit the host's
            # test user to remove only this run's temporary directory afterwards.
            assert runtime_data.resolve().parent == Path(directory).resolve()
            subprocess.run([
                'docker', 'run', '--rm', '--user', '0:0', '--read-only',
                '--mount', f'type=bind,src={runtime_data.resolve()},dst=/cleanup',
                '--entrypoint', 'python', args.image, '-c',
                "import os; [(os.chmod(base,0o777), [os.chmod(os.path.join(base,f),0o666) for f in files if not os.path.islink(os.path.join(base,f))]) for base,dirs,files in os.walk('/cleanup')]",
            ], check=True)


if __name__ == '__main__':
    main()
