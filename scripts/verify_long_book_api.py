"""Loopback-only page/RAG checks for the frozen book; generation is disabled."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import sys
import time
from urllib.parse import urlparse
import httpx

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--api', default='http://127.0.0.1:8000/api')
    parser.add_argument('--freeze', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--phase', choices=['before', 'after'], required=True)
    args = parser.parse_args()
    if urlparse(args.api).hostname not in {'127.0.0.1', 'localhost', '::1'}:
        parser.error('Only the local development API is allowed.')
    if args.output.exists():
        parser.error('Choose a new output; first results must remain.')
    frozen = json.loads(args.freeze.read_text(encoding='utf-8'))
    for name, expected in frozen['files'].items():
        if hashlib.sha256((ROOT/name).read_text(encoding='utf-8').encode()).hexdigest() != expected:
            raise SystemExit('Frozen product changed: ' + name)
    source = frozen['source']; identity = source['id']; run = source['parse_run_id']
    result = {'phase': args.phase, 'provider_calls': 0, 'passed': False,
              'runner_lf_sha256': hashlib.sha256(Path(__file__).read_text(encoding='utf-8').encode()).hexdigest(),
              'pages': [], 'queries': [], 'parallel_page_reads': []}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with httpx.Client(base_url=args.api, timeout=60, trust_env=False) as client:
        health = client.get('/health'); health.raise_for_status()
        assert health.json()['version'] == frozen['application_version']
        document = client.get('/documents/' + identity); document.raise_for_status()
        assert document.json()['sha256'] == source['sha256']
        def page(number):
            start = time.monotonic()
            response = client.get('/documents/' + identity + '/structure', params={'page': number, 'parse_run_id': run})
            response.raise_for_status(); data = response.json()
            assert data['parse_run_id'] == run and all(b['page'] == number for b in data['blocks'])
            return {'page': number, 'wire_bytes': len(response.content), 'block_count': len(data['blocks']),
                    'seconds': round(time.monotonic()-start, 4)}, data
        cached = {}
        for number in frozen['pages']:
            summary, data = page(number); result['pages'].append(summary); cached[number] = data
        with ThreadPoolExecutor(max_workers=4) as pool:
            for summary, _ in pool.map(page, [32, 222, 363, 541]):
                result['parallel_page_reads'].append(summary)
        for case in frozen['queries'][:1] if args.phase == 'before' else frozen['queries']:
            start = time.monotonic()
            response = client.post('/ask', json={'question': case['question'], 'document_ids': [identity],
                                                 'parse_runs': {identity: run}, 'limit': 6, 'use_model': False})
            response.raise_for_status(); answer = response.json()
            assert answer['mode'] == 'evidence_only' and not answer['claims'] and answer['evidence']
            if args.phase == 'before':
                assert answer['retrieval']['mode'] == 'bm25'
            else:
                assert 'dense' in answer['retrieval']['mode'] and not answer['retrieval'].get('index_required')
            for evidence in answer['evidence']:
                number = evidence['page']
                if number not in cached:
                    _, cached[number] = page(number)
                block = next(b for b in cached[number]['blocks'] if b['id'] == evidence['block_id'])
                assert evidence['parse_run_id'] == run and evidence['document_id'] == identity
                assert all(evidence[k] == block[k] for k in ('text', 'page', 'bbox'))
            result['queries'].append({'case_id': case['id'], 'seconds': round(time.monotonic()-start, 3), 'answer': answer})
        result['passed'] = True
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8', newline='\n')
    print(json.dumps({'phase': args.phase, 'passed': True, 'pages_checked': len(result['pages']),
                      'parallel_page_reads': len(result['parallel_page_reads']), 'queries': len(result['queries'])}))


if __name__ == '__main__':
    main()
