"""Retire all observed positive/negative examples to development regression."""
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts.eval_core import validate_pack,digest


def main():
    packs=[json.loads(Path(name).read_text(encoding='utf-8')) for name in ['evaluation/qa_regression_v2.json','evaluation/qa_holdout_v3.json']]
    sources={key:value for pack in packs for key,value in pack['sources'].items()}
    cases=[case for pack in packs for case in pack['cases']]
    for source in sources.values():source['split']='development'
    for case in cases:case['split']='development'
    pack={'schema_version':1,'name':'Stage11 field-answer development regression','sources':sources,'cases':cases,
          'negative_checks':packs[1]['negative_checks'], 'prior_pack_sha256':[digest(json.loads(Path(name).read_text(encoding='utf-8'))) for name in ['evaluation/qa_regression_v2.json','evaluation/qa_holdout_v3.json']],
          'scope':'28 previously observed factual cases and 2 separately recorded missing-field probes; development regression only',
          'holdout_limit':'All cases were observed before this revision. Gold facts/anchors are unchanged assistant drafts pending independent human review. No fresh-work quality claim.'}
    validate_pack(pack)
    with Path('evaluation/qa_stage11_regression.json').open('x',encoding='utf-8') as stream:json.dump(pack,stream,ensure_ascii=False,indent=2)
    print('Prepared',len(cases),'development cases without changing gold facts.')


if __name__=='__main__':main()
