"""Frozen official paragraph: source preview, at most one live request, cache chain."""
import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


async def exercise(frozen, output):
    from backend.app import workflow, model_client, research_store, store, translation
    original = model_client.httpx.AsyncClient
    records = []
    def save(name, value):
        (output / name).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8', newline='\n')
    def scrub(text):
        secret = os.getenv('LLM_API_KEY', '')
        return text.replace(secret, '[REDACTED]') if secret else text
    class Recorder:
        def __init__(self, *args, **kwargs): self.client = original(*args, **kwargs)
        async def __aenter__(self): await self.client.__aenter__(); return self
        async def __aexit__(self, *args): return await self.client.__aexit__(*args)
        async def post(self, url, **kwargs):
            if len(records) >= frozen['external_provider_call_cap']:
                raise RuntimeError('Frozen provider call cap reached; no transmission.')
            record = {'request': kwargs['json']}; records.append(record)
            save('provider_records.json', records)
            try:
                response = await self.client.post(url, **kwargs)
                record.update(http_status=response.status_code, response_text=scrub(response.text))
                save('provider_records.json', records)
                return response
            except Exception as error:
                record['error_code'] = type(error).__name__; save('provider_records.json', records); raise
    workflow.repo.init_db(); workflow.start()
    before = store.stats(); cases = []
    try:
        source = frozen['source']; structure = store.get_structure(source['document_id'], page=source['page'], parse_run_id=source['parse_run_id'])
        block = next(block for block in structure['blocks'] if block['id'] == source['block_id'])
        assert hashlib.sha256(json.dumps({key: block[key] for key in ['id', 'page', 'bbox', 'text']}, ensure_ascii=False, sort_keys=True).encode()).hexdigest() == source['block_sha256']
        assert store.get_document(source['document_id'])['sha256'] == source['sha256']
        assert model_client.identity() == frozen['model_identity']
        spec = frozen['translation_spec']
        key = translation.cache_key(source['document_id'], source['parse_run_id'], block, spec['target_language'], spec['glossary'])
        assert research_store.get_translation(key) is None, 'Choose an uncached paragraph before freezing; never delete an existing translation.'
        model_client.httpx.AsyncClient = Recorder
        for name, enabled, actions, goal in [
            ('source_preview', False, ['translation'], '【段落原文预览】翻译指定 Transfer Learning 段落'),
            ('live_translation', True, ['translation'], '【真实段落翻译】翻译指定 Transfer Learning 段落'),
            ('cached_chain', False, ['translation', 'writing', 'diagram'], '【译文缓存与写作流程】翻译已选 Transfer Learning 段落，组织写作框架和任务流程图'),
        ]:
            calls_before = len(records)
            body = workflow.PlanRequest(goal=goal, document_ids=[source['document_id']], actions=actions, translation=spec,
                                        use_model=enabled, max_tool_calls=8, max_model_calls=1,
                                        max_output_tokens=3500, max_active_seconds=180)
            task = await workflow.plan(body)
            workflow.repo.mutate(task['id'], lambda value: value.update(status='queued'), task['revision'])
            await workflow.run(task['id'])
            result = workflow.repo.get(task['id']); save(name + '.json', result)
            bundle = workflow.export(task['id']).body; (output / (name + '.zip')).write_bytes(bundle)
            with zipfile.ZipFile(output / (name + '.zip')) as archive:
                assert archive.testzip() is None and 'translations/translation-1.json' in archive.namelist()
                assert json.loads(archive.read('task.json')) == result
            translated = next((step['result'] for step in result['steps'] if step['tool'] == 'translation' and step.get('result')), None)
            cases.append({'name': name, 'task_id': task['id'], 'status': result['status'], 'translation_status': translated and translated['translation_status'],
                          'usage': result['usage'], 'external_provider_calls': len(records)-calls_before,
                          'sources_unchanged': result['sources'] == task['sources'], 'budgets_unchanged': result['budgets'] == task['budgets'], 'zip_valid': True})
            save('summary.json', {'cases': cases, 'external_provider_calls': len(records), 'semantic_quality_verified': False})
            print(json.dumps(cases[-1], ensure_ascii=False), flush=True)
            assert result['status'] == 'completed_with_gaps'
            if name == 'source_preview': assert translated['translation'] is None and len(records) == 0
            if name == 'live_translation': assert translated and translated['translation_status'] == 'translated' and len(records) == 1
            if name == 'cached_chain': assert translated['translation_status'] == 'cached' and len(records) == calls_before
        assert store.stats() == before
        save('summary.json', {'application_version': frozen['application_version'], 'cases': cases, 'library_stats': before,
                              'external_provider_calls': len(records), 'semantic_quality_verified': False})
    finally:
        model_client.httpx.AsyncClient = original
        await workflow.shutdown()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ['freeze', 'workspace', 'output']: parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists(): parser.error('Preserve previous results; select a new output directory.')
    frozen = json.loads(args.freeze.read_text(encoding='utf-8'))
    assert frozen['external_provider_call_cap'] == 1
    for name, expected in frozen['files'].items():
        assert hashlib.sha256((ROOT / name).read_text(encoding='utf-8').encode()).hexdigest() == expected, name
    from dotenv import load_dotenv
    load_dotenv(ROOT / '.env', override=False)
    os.environ.update(SCHOLARTRACE_DATA_DIR=str(args.workspace.resolve()), SCHOLARTRACE_ENABLE_DENSE='false', SCHOLARTRACE_ENABLE_OCR='false', SCHOLARTRACE_ENABLE_TABLES='false')
    args.output.mkdir(parents=True)
    asyncio.run(exercise(frozen, args.output))


if __name__ == '__main__': main()
