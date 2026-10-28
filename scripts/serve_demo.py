"""Serve the built frontend and API on loopback, with one shared demo workspace."""
from __future__ import annotations
import argparse
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    from dotenv import load_dotenv
    from filelock import FileLock, Timeout
    from backend.app.demo_access import enabled, Settings
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env-file', type=Path, default=ROOT / '.env.demo')
    parser.add_argument('--port', type=int, default=8000)
    parser.add_argument('--check-config', action='store_true')
    args = parser.parse_args()
    if not args.env_file.is_file():
        raise SystemExit('Demo config missing. Run scripts/configure_demo.py first.')
    if not 1 <= args.port <= 65535:
        raise SystemExit('Invalid port.')
    os.chdir(ROOT)
    load_dotenv(args.env_file.resolve(), override=True)
    if not enabled():
        raise SystemExit('This launcher requires SCHOLARTRACE_DEMO_MODE=true.')
    Settings.from_env()
    data = Path(os.environ['SCHOLARTRACE_DATA_DIR']).resolve()
    if data in {ROOT, ROOT / 'data'}:
        raise SystemExit('Use a separate demo workspace, not the development data root.')
    if args.check_config:
        print('Demo configuration valid. Single shared workspace; no credentials displayed.')
        return
    os.environ['SCHOLARTRACE_SERVE_WEB'] = 'true'
    data.mkdir(parents=True, exist_ok=True)
    lease = FileLock(data / '.demo-server.lock')
    try:
        with lease.acquire(timeout=0):
            import uvicorn
            uvicorn.run('backend.app.main:app', host='127.0.0.1', port=args.port, workers=1,
                        proxy_headers=False, access_log=False, limit_concurrency=40, timeout_keep_alive=5)
    except Timeout:
        raise SystemExit('Another demo launcher already owns this workspace.') from None


if __name__ == '__main__':
    main()
