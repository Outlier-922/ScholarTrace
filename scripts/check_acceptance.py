"""Print the explicit delivery gate; --require-ready fails on unfinished items."""
import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATES = {'verified', 'partial', 'missing', 'external_pending'}


def assess(data, root=ROOT):
    items = data['items']
    if not items or not any(item.get('required') is True for item in items):
        raise ValueError('acceptance_requires_nonempty_required_scope')
    if len({item['id'] for item in items}) != len(items):
        raise ValueError('duplicate_acceptance_item')
    for item in items:
        if item['state'] not in STATES or not isinstance(item['required'], bool):
            raise ValueError('invalid_acceptance_state')
        if item['state'] == 'verified':
            if not item.get('proof_paths'):
                raise ValueError('verified_item_without_evidence')
            for name in item['proof_paths']:
                path = (root / name).resolve()
                if not path.is_relative_to(root.resolve()) or not path.is_file():
                    raise ValueError('missing_acceptance_evidence:' + name)
    pending = [item['id'] for item in items if item['required'] and item['state'] != 'verified']
    return {'application_version': data['application_version'], 'submission_ready': not pending,
            'unfinished_required_items': pending,
            'notice': 'Checklist status is an attributed project assessment, not an organizer score or automatic proof of factual quality.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--file', type=Path, default=ROOT / 'evaluation/acceptance.json')
    parser.add_argument('--require-ready', action='store_true')
    args = parser.parse_args()
    result = assess(json.loads(args.file.read_text(encoding='utf-8')))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.require_ready and not result['submission_ready']:
        raise SystemExit(1)


if __name__ == '__main__': main()
