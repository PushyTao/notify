"""Create first-run deployment secrets; never overwrite an existing .env."""
import os
from pathlib import Path
import secrets


def main():
    path = Path(__file__).resolve().parents[1] / '.env'
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raise SystemExit('.env already exists; keeping your existing credentials.')
    with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as stream:
        stream.write('APP_SECRET_KEY=' + secrets.token_urlsafe(48) + '\n')
        stream.write('ADMIN_PASSWORD=' + secrets.token_urlsafe(24) + '\n')
        stream.write('APP_PORT=8888\nBIND_HOST=0.0.0.0\nCOOKIE_SECURE=false\n')
    print('Created .env. Read ADMIN_PASSWORD there for your initial /admin login. Keep this file private.')


if __name__ == '__main__':
    main()
