"""Create a stage 11 review bundle from an explicit source/report allowlist."""
import hashlib
import json
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    freeze = json.loads((ROOT / 'evaluation/freeze_stage11_v3.json').read_text(encoding='utf-8'))
    names = [
        'README.md', 'docs/STAGE11_TEST_REPORT.md', 'docs/FIELD_ANSWERS.md',
        'docs/PROGRESS.md', 'docs/PROMPT_AUDIT.md',
        'evaluation/README.md', 'evaluation/qa_stage11_regression.json',
        'evaluation/freeze_stage11_v1.json', 'evaluation/freeze_stage11_v2.json',
        'evaluation/freeze_stage11_v3.json',
        'apps/web/package.json', 'apps/web/src/App.tsx', 'apps/web/src/types.ts',
        'apps/web/src/AnswerCoverage.tsx', 'apps/web/src/answer-coverage.css',
        'backend/app/__init__.py', 'scripts/prepare_stage11_regression.py',
        'scripts/evaluate_missing_fields.py', 'scripts/package_stage11.py',
        'tests/test_answer_fields.py', 'tests/test_evaluation.py',
    ]
    names += list(freeze['files'])
    names += [p.relative_to(ROOT).as_posix() for p in sorted((ROOT / 'evaluation/results').glob('stage11-*.json'))]
    names += [p.relative_to(ROOT).as_posix() for p in sorted((ROOT / 'docs/screenshots').glob('stage11-*.png'))]
    files = {name: (ROOT / name).read_bytes() for name in dict.fromkeys(names)}
    manifest = {
        'version': '0.9.0',
        'purpose': 'Selected source and local development-regression records; not an installation package or independently adjudicated benchmark',
        'fingerprint_format': freeze['fingerprint_format'],
        'files': {name: {'bytes': len(blob), 'sha256': hashlib.sha256(blob).hexdigest()} for name, blob in files.items()},
    }
    target = ROOT / 'data/stage11_review.zip'
    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, blob in files.items():
            archive.writestr(name, blob)
        archive.writestr('MANIFEST.json', json.dumps(manifest, ensure_ascii=False, indent=2))
    with zipfile.ZipFile(target) as archive:
        assert archive.testzip() is None
        for name, record in manifest['files'].items():
            assert hashlib.sha256(archive.read(name)).hexdigest() == record['sha256']
    print(f'{target}: {len(files)} review files; ZIP CRC and manifest SHA256 checked.')


if __name__ == '__main__':
    main()
