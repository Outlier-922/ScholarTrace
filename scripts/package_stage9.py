"""Local stage9 review bundle; application source/history remains in Git."""
import hashlib
import json
from pathlib import Path
import zipfile

ROOT=Path(__file__).resolve().parents[1]


def main():
    paths=['docs/STAGE9_TEST_REPORT.md','docs/INDEX_AND_LAYOUT.md','tests/test_retrieval_revision.py',
           'scripts/verify_stage9.py','evaluation/qa_regression_v2.json',
           'backend/app/qa.py','backend/app/parser.py','backend/app/semantic.py','backend/app/semantic_jobs.py',
           'apps/web/src/SemanticIndexPanel.tsx','apps/web/src/semantic-index.css']
    paths += ['evaluation/freeze_regression_v'+str(i)+'.json' for i in [2,3,4,5]]
    paths += [p.relative_to(ROOT).as_posix() for p in sorted((ROOT/'evaluation/results').glob('stage9-*.json'))]
    paths += [p.relative_to(ROOT).as_posix() for p in sorted((ROOT/'docs/screenshots').glob('stage9-*.png'))]
    files={name:(ROOT/name).read_bytes() for name in paths}
    manifest={'purpose':'Local review bundle; not a deployment package or independently graded evaluation',
              'files':{name:{'bytes':len(blob),'sha256':hashlib.sha256(blob).hexdigest()} for name,blob in files.items()}}
    target=ROOT/'data/stage9_review.zip'
    with zipfile.ZipFile(target,'w',zipfile.ZIP_DEFLATED) as archive:
        for name,blob in files.items():archive.writestr(name,blob)
        archive.writestr('MANIFEST.json',json.dumps(manifest,ensure_ascii=False,indent=2))
    with zipfile.ZipFile(target) as archive:assert archive.testzip() is None
    print(target,':',len(files),'review files; ZIP CRC checked; no .env/database/raw PDF included.')


if __name__=='__main__':main()
