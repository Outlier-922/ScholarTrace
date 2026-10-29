"""Frozen two-paper smoke; default is retrieval only, at most two paid calls."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def source_digest(store, source):
    structure = store.get_structure(source['id'], parse_run_id=source['parse_run_id'])
    return digest([{key: block[key] for key in ('id', 'text', 'page', 'bbox')} for block in structure['blocks']])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ['freeze', 'workspace', 'output']:
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--live-model', action='store_true')
    args = parser.parse_args()
    if args.output.exists():
        parser.error('Preserve first results; choose a new output path.')
    frozen = json.loads(args.freeze.read_text(encoding='utf-8'))
    for name, expected in frozen['files'].items():
        if hashlib.sha256((ROOT / name).read_text(encoding='utf-8').encode()).hexdigest() != expected:
            raise SystemExit('Frozen file changed: ' + name)
    os.environ['SCHOLARTRACE_DATA_DIR'] = str(args.workspace.resolve())
    for feature in ['OCR', 'TABLES', 'DENSE']:
        os.environ['SCHOLARTRACE_ENABLE_' + feature] = 'false'
    os.environ['SCHOLARTRACE_DEMO_MODE'] = 'false'
    from backend.app import model_client, store
    from backend.app.main import app
    from fastapi.testclient import TestClient
    for source in frozen['sources']:
        document = store.get_document(source['id'])
        if not document or document['sha256'] != source['sha256'] or source_digest(store, source) != source['blocks_sha256']:
            raise SystemExit('Frozen paper/ParseRun changed.')
    args.output.mkdir(parents=True)
    def save(name, value):
        (args.output / name).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8', newline='\n')
    save('manifest.json', {'freeze': str(args.freeze), 'live_model': args.live_model,
                           'runner_lf_sha256': hashlib.sha256(Path(__file__).read_text(encoding='utf-8').encode()).hexdigest()})
    budget = model_client.CallBudget(max_calls=min(2, frozen['max_model_calls']),
                                    max_output_tokens=min(5000, frozen['max_reserved_output_tokens']))
    original = model_client.chat_json
    requests, responses = [], []
    async def capture(system, payload, *, max_tokens):
        if not args.live_model:
            raise AssertionError('Provider forbidden in retrieval-only smoke.')
        requests.append({'system': system, 'payload': payload, 'max_tokens': max_tokens})
        save('provider_requests.json', requests)
        with model_client.metered(budget):
            response = await original(system, payload, max_tokens=max_tokens)
        responses.append(response)
        save('provider_first_responses.json', responses)
        return response
    model_client.chat_json = capture
    try:
        with TestClient(app) as client:
            request = {**frozen['request'], 'use_model': args.live_model}
            response = client.post('/api/comparisons', json=request)
            response.raise_for_status()
            result = response.json()
            history = client.get('/api/comparisons/' + result['id'])
            history.raise_for_status()
            exported = client.get('/api/comparisons/' + result['id'] + '/json')
            exported.raise_for_status()
            save('results.json', {'request': request, 'result': result, 'budget': budget.snapshot(),
                                 'historical_record_matches': history.json() == result,
                                 'json_export_matches': exported.json() == result,
                                 'semantic_verified': False})
            print(json.dumps({'mode': result['mode'], 'budget': budget.snapshot(),
                              'statuses': [cell['status'] for row in result['rows'] for cell in row['cells']]}))
    finally:
        save('budget.json', budget.snapshot())
        model_client.chat_json = original


if __name__ == '__main__':
    main()
