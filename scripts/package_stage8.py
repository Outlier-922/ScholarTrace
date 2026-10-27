"""Create a local review bundle using an explicit allowlist, without corpus or secrets."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import zipfile

ROOT=Path(__file__).resolve().parents[1]


def main():
    names=['README.md','docs/STAGE8_TEST_REPORT.md','docs/TECHNICAL_DESIGN_DRAFT.md',
           'docs/DEMO_SCRIPT.md','docs/SUBMISSION_CHECKLIST.md','docs/PROMPT_AUDIT.md',
           'output/pdf/ScholarTrace_技术说明_草稿.pdf','scripts/build_technical_pdf.py',
           'scripts/prepare_eval_corpus.py','scripts/build_eval_pack.py','scripts/eval_core.py',
           'scripts/evaluate_qa.py','scripts/review_evaluation.py','tests/test_evaluation.py',
           'docs/screenshots/stage6-source.png','docs/screenshots/stage5-network.png','docs/screenshots/stage6-chart.png']
    names += [path.relative_to(ROOT).as_posix() for path in sorted((ROOT/'evaluation').rglob('*')) if path.is_file()]
    entries={name:(ROOT/name).read_bytes() for name in names}
    assert all('.env' not in name and not name.startswith('data/') for name in entries)
    manifest={'purpose':'Local stage8 review materials; not a deployment bundle or final submission',
              'files':{name:{'sha256':hashlib.sha256(blob).hexdigest(),'bytes':len(blob)} for name,blob in entries.items()},
              'excluded':['API credentials','raw paper PDFs','SQLite database','raw evidence responses','model caches','video (not recorded)']}
    output=ROOT/'data/stage8_review_materials.zip';output.parent.mkdir(exist_ok=True)
    with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED) as archive:
        for name,blob in entries.items():archive.writestr(name,blob)
        archive.writestr('MANIFEST.json',json.dumps(manifest,ensure_ascii=False,indent=2))
        archive.writestr('READ_ME_FIRST.txt','Local review materials only. Full application source and Git history remain in the project repository. Model output quality is pending independent human review. No video or public deployment is included.\n')
    with zipfile.ZipFile(output) as archive:
        assert archive.testzip() is None and len(archive.namelist())==len(entries)+2
    print(output,output.stat().st_size,'bytes;',len(entries),'review files; manifest SHA256 included.')


if __name__=='__main__':main()
