"""Bundle fresh-work evaluation records, excluding paper PDFs and credentials."""
import hashlib
import json
from pathlib import Path
import zipfile

ROOT=Path(__file__).resolve().parents[1]


def main():
    names=['docs/STAGE10_TEST_REPORT.md','evaluation/qa_holdout_v3.json','evaluation/freeze_holdout_v3.json',
           'scripts/prepare_holdout_corpus.py','scripts/build_holdout_v3.py','scripts/evaluate_missing_fields.py',
           'scripts/evaluate_qa.py','scripts/eval_core.py','scripts/review_evaluation.py','tests/test_missing_field_evaluation.py']
    names += [p.relative_to(ROOT).as_posix() for p in sorted((ROOT/'evaluation/results').glob('stage10-*.json'))]
    files={name:(ROOT/name).read_bytes() for name in names}
    manifest={'purpose':'Local evaluation review; not a deployment package or independently adjudicated benchmark',
              'product_commit':'b7dc401','files':{name:{'bytes':len(blob),'sha256':hashlib.sha256(blob).hexdigest()} for name,blob in files.items()}}
    target=ROOT/'data/stage10_review.zip'
    with zipfile.ZipFile(target,'w',zipfile.ZIP_DEFLATED) as archive:
        for name,blob in files.items():archive.writestr(name,blob)
        archive.writestr('MANIFEST.json',json.dumps(manifest,ensure_ascii=False,indent=2))
    with zipfile.ZipFile(target) as archive:assert archive.testzip() is None
    print(target,':',len(files),'review files; ZIP CRC checked.')


if __name__=='__main__':main()
