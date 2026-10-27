"""Apply attributed reviews to an existing run without any API/model calls."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts.eval_core import digest, summarize


def review_run(directory, reviews):
    rows=json.loads((directory/'raw.json').read_text(encoding='utf-8'))
    if any(row.get('response') is not None and digest(row['response'])!=row['response_sha256'] for row in rows):
        raise ValueError('response_changed_since_run')
    known={row['case_id'] for row in rows}
    if any(review['case_id'] not in known for review in reviews):
        raise ValueError('unknown_review_case')
    return {'run_id':directory.name,'overall':summarize(rows,reviews),
            'by_split':{split:summarize([row for row in rows if row['split']==split],reviews)
                        for split in sorted({row['split'] for row in rows})}}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run',type=Path,required=True)
    parser.add_argument('--reviews',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    reviews=json.loads(args.reviews.read_text(encoding='utf-8'))
    report=review_run(args.run,reviews)
    with args.output.open('x',encoding='utf-8') as stream:
        json.dump(report,stream,ensure_ascii=False,indent=2)
    print(json.dumps(report['overall'],ensure_ascii=False,indent=2))


if __name__=='__main__':main()
