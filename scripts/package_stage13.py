"""Package selected demo source and verification, excluding private workspaces."""
import hashlib
import json
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]
FILES = [
    'README.md', '.env.example', '.env.demo.example', '.gitignore',
    '配置演示访问.cmd', '启动受控演示.cmd', 'backend/app/demo_access.py',
    'backend/app/main.py', 'backend/app/model_client.py', 'backend/app/__init__.py',
    'apps/web/package.json', 'apps/web/src/App.tsx', 'apps/web/src/types.ts',
    'scripts/configure_demo.py', 'scripts/serve_demo.py', 'scripts/evaluate_qa.py',
    'scripts/package_stage13.py', 'tests/test_demo_access.py', 'tests/test_demo_configuration.py',
    'docs/CONTROLLED_DEMO.md', 'docs/STAGE13_TEST_REPORT.md', 'docs/PROGRESS.md',
    'docs/PROMPT_AUDIT.md', 'docs/SUBMISSION_CHECKLIST.md', 'deploy/Caddyfile.example',
    'docs/screenshots/stage13-demo.png', 'docs/screenshots/stage13-demo-mobile.png',
    'evaluation/results/stage13-verification.json',
]


def main():
    files = {name: (ROOT / name).read_bytes() for name in FILES}
    assert all(not name.startswith('data/') and name != '.env.demo' and not name.endswith(('.db', '.sqlite3', '.pdf')) for name in files)
    manifest = {'version': '0.10.0',
                'purpose': 'Selected controlled shared-demo source and local verification; not a complete deployment package',
                'public_deployed': False, 'multi_user_isolation': False, 'new_real_model_calls': 0,
                'files': {name: {'bytes': len(blob), 'sha256': hashlib.sha256(blob).hexdigest()} for name, blob in files.items()}}
    target = ROOT / 'data/stage13_review.zip'
    target.parent.mkdir(exist_ok=True)
    with zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, blob in files.items(): archive.writestr(name, blob)
        archive.writestr('MANIFEST.json', json.dumps(manifest, ensure_ascii=False, indent=2))
    with zipfile.ZipFile(target) as archive:
        assert archive.testzip() is None
        assert all(hashlib.sha256(archive.read(name)).hexdigest() == record['sha256'] for name, record in manifest['files'].items())
    print(f'{target}: {len(files)} review files; CRC and manifest SHA256 checked.')


if __name__ == '__main__': main()
