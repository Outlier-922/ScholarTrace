"""Create a separate demo configuration; password input is hidden."""
from __future__ import annotations
import argparse
from getpass import getpass
from pathlib import Path
import re
import sys
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.app.demo_access import password_hash


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / '.env.demo')
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit('Configuration already exists. Edit it locally; this wizard never overwrites credentials.')
    origin = (input('演示地址（回车默认 http://127.0.0.1:8000；公网须为https）：').strip() or 'http://127.0.0.1:8000').rstrip('/')
    parsed = urlsplit(origin)
    if not ((parsed.scheme == 'https' or parsed.scheme == 'http' and parsed.hostname in {'localhost', '127.0.0.1', '::1'}) and parsed.hostname and not any([parsed.username, parsed.password, parsed.path, parsed.query, parsed.fragment])):
        raise SystemExit('Invalid demo origin.')
    username = input('演示用户名（回车默认 reviewer；3-64位英文字母/数字/._-）：').strip() or 'reviewer'
    if not re.fullmatch(r'[A-Za-z0-9_.-]{3,64}', username):
        raise SystemExit('Invalid username.')
    password = getpass('演示密码（12-256字符，输入不显示）：')
    if password != getpass('再输入一次：'):
        raise SystemExit('Passwords differ; no file written.')
    encoded = password_hash(password)
    text = '\n'.join([
        '# Private demo configuration. Never share or commit this file.',
        'SCHOLARTRACE_DEMO_MODE=true', f'SCHOLARTRACE_DEMO_ORIGIN={origin}',
        f'SCHOLARTRACE_DEMO_USERNAME={username}', f"SCHOLARTRACE_DEMO_PASSWORD_HASH='{encoded}'",
        'SCHOLARTRACE_DATA_DIR=./data/demo_workspace', 'SCHOLARTRACE_SERVE_WEB=true',
        'SCHOLARTRACE_DEMO_ALLOW_WRITES=false', 'SCHOLARTRACE_DEMO_REQUESTS_PER_MINUTE=360',
        'SCHOLARTRACE_DEMO_MAX_REQUEST_MB=110', 'SCHOLARTRACE_DEMO_MAX_MUTATIONS=2',
        'SCHOLARTRACE_DEMO_MODEL_CALLS_PER_HOUR=0', 'SCHOLARTRACE_DEMO_MODEL_TOKENS_PER_HOUR=0',
        'SCHOLARTRACE_MAX_UPLOAD_MB=100', 'SCHOLARTRACE_MAX_PAGES=1500',
        'SCHOLARTRACE_PARSE_WORKERS=2', 'SCHOLARTRACE_ENABLE_OCR=false',
        'SCHOLARTRACE_ENABLE_TABLES=false', 'SCHOLARTRACE_ENABLE_DENSE=false',
        '# Explicit blanks prevent inheriting provider credentials from the development .env.',
        'LLM_BASE_URL=', 'LLM_API_KEY=', 'LLM_MODEL=', '',
    ])
    with args.output.open('x', encoding='utf-8') as stream:
        stream.write(text)
    try:
        args.output.chmod(0o600)
    except OSError:
        pass
    print('Separate read-only demo configuration created. Password and hash were not printed.')


if __name__ == '__main__':
    main()
