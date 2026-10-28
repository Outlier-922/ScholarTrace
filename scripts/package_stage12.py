"""Bundle current review materials, excluding local evidence HTML and responses."""
import hashlib
import json
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]
FILES = [
    'README.md', '打开人工复核.cmd', 'docs/HUMAN_REVIEW.md',
    'docs/STAGE12_TEST_REPORT.md', 'docs/STAGE11_TEST_REPORT.md',
    'docs/TECHNICAL_DESIGN_CURRENT.md', 'docs/technical_v090.json',
    'docs/DEMO_SCRIPT.md', 'docs/SUBMISSION_CHECKLIST.md', 'docs/PROGRESS.md',
    'docs/PROMPT_AUDIT.md', 'scripts/build_review_workbench.py',
    'scripts/build_technical_pdf.py', 'scripts/review_evaluation.py',
    'scripts/eval_core.py', 'scripts/package_stage12.py', 'tests/test_review_workbench.py',
    'evaluation/results/stage12-verification.json',
    'output/pdf/ScholarTrace_技术说明_0.9.0_草稿.pdf',
    'docs/screenshots/stage12-review.png', 'docs/screenshots/stage12-review-mobile.png',
    'docs/screenshots/stage11-fields.png', 'docs/screenshots/stage6-source.png',
    'docs/screenshots/stage5-network.png', 'docs/screenshots/stage6-chart.png',
]


def main():
    files = {name: (ROOT / name).read_bytes() for name in FILES}
    assert all(not name.startswith('data/') and not name.endswith(('.env', '.db', '.html')) for name in files)
    manifest = {
        'purpose': 'Local stage12 review materials; not a complete deployment package or final submission',
        'application_version': '0.9.0', 'product_base_commit': 'b65058f',
        'human_reviewed_research_cases': 0, 'new_generation_requests': 0,
        'files': {name: {'sha256': hashlib.sha256(blob).hexdigest(), 'bytes': len(blob)}
                  for name, blob in files.items()},
        'excluded': ['local HTML with paper snippets', 'raw responses', 'credentials',
                     'database', 'paper corpus', 'model caches', 'video (not recorded)'],
    }
    output = ROOT / 'data/stage12_review_materials.zip'
    output.parent.mkdir(exist_ok=True)
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, blob in files.items():
            archive.writestr(name, blob)
        archive.writestr('MANIFEST.json', json.dumps(manifest, ensure_ascii=False, indent=2))
    with zipfile.ZipFile(output) as archive:
        assert archive.testzip() is None
        for name, record in manifest['files'].items():
            assert hashlib.sha256(archive.read(name)).hexdigest() == record['sha256']
    print(f'{output}: {len(files)} files; CRC and manifest SHA256 checked.')


if __name__ == '__main__':
    main()
