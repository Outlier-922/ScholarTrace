"""Official-PDF tool chain plus explicitly simulated timeout; zero live calls."""
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


def digest_source(store, source):
    structure = store.get_structure(source['id'], parse_run_id=source['parse_run_id'])
    return hashlib.sha256(json.dumps([{k: b[k] for k in ('id', 'text', 'page', 'bbox')} for b in structure['blocks']], ensure_ascii=False, sort_keys=True).encode()).hexdigest()


async def exercise(frozen, output):
    from backend.app import workflow, model_client, store
    import httpx
    original = model_client.httpx.AsyncClient
    original_retrieve = workflow.qa._retrieve_question
    simulated_requests = []
    class TimeoutProvider:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def post(self, url, **kwargs):
            simulated_requests.append(kwargs['json'])
            raise httpx.ReadTimeout('Explicit local simulation; no HTTP transmission.', request=httpx.Request('POST', url))
    def save(name, value):
        (output/name).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8', newline='\n')
    workflow.repo.init_db(); workflow.start()
    before = store.stats(); cases = []
    try:
        for name in ['offline_chain', 'simulated_timeout', 'simulated_timeout_missing']:
            simulated = name != 'offline_chain'
            missing = name == 'simulated_timeout_missing'
            request_count_before = len(simulated_requests)
            retrieval_scopes = []
            workflow.qa._retrieve_question = original_retrieve
            if missing:
                def first_document_only(question, documents, limit, trace):
                    retrieval_scopes.append([document['id'] for document in documents])
                    selected = documents[:1] if len(retrieval_scopes) == 1 else documents
                    return original_retrieve(question, selected, limit, trace)
                workflow.qa._retrieve_question = first_document_only
            if simulated:
                os.environ.update(LLM_API_KEY='synthetic-feedback-fixture-not-a-secret', LLM_BASE_URL='https://provider.invalid/v1',
                                  LLM_MODEL='synthetic-timeout-fixture', LLM_API_PROFILE='compatible')
                model_client.httpx.AsyncClient = TimeoutProvider
            goal = ('【模拟故障+漏检演练】' if missing else '【模拟故障演练】' if simulated else '【本地闭环验收】') + frozen['goal']
            body = workflow.PlanRequest(goal=goal, document_ids=[s['id'] for s in frozen['sources']],
                                        actions=frozen['actions'], dimensions=frozen['dimensions'], use_model=simulated,
                                        max_tool_calls=8, max_model_calls=2, max_output_tokens=5000,
                                        max_active_seconds=180, max_replans=2, recovery_policy='local_fallback')
            planned = await workflow.plan(body)
            if simulated:
                workflow.repo.mutate(planned['id'], lambda task: task.update(simulation={
                    'description': '本记录是注入模型超时'+('与首轮漏检' if missing else '')+'的恢复演练，不代表服务商真实故障或实际账单',
                    'external_provider_calls': 0, 'provider_error_simulated': True, 'retrieval_missing_simulated': missing}))
            current = workflow.repo.get(planned['id'])
            workflow.repo.mutate(planned['id'], lambda task: task.update(status='queued'), current['revision'])
            await workflow.run(planned['id'])
            result = workflow.repo.get(planned['id'])
            assert result['status'] == 'completed_with_gaps'
            assert result['sources'] == planned['sources'] and result['budgets'] == planned['budgets']
            for step in result['steps']:
                if step.get('result'): workflow.validate_result(result, step['result'])
            if simulated:
                assert len(simulated_requests)-request_count_before == 1 and result['usage']['model']['calls'] == 1
                assert result['steps'][0]['status'] == 'failed' and result['steps'][0]['recovery_status'] == 'local_completed'
                assert result['execution_model_disabled'] and result['plan_version'] == (3 if missing else 2)
                if missing:
                    uncovered = result['steps'][0]['partial_result']['uncovered_documents']
                    assert uncovered and retrieval_scopes[1] == uncovered
                    supplement = next(step for step in result['steps'] if step['id'] == 'supplement')
                    assert {item['document_id'] for item in supplement['result']['evidence']} == set(uncovered)
            else:
                assert all(step['status'] == 'completed' for step in result['steps'])
                assert result['usage']['model']['calls'] == 0
            save(name+'.json', result)
            bundle = workflow.export(result['id']).body
            (output/(name+'.zip')).write_bytes(bundle)
            with zipfile.ZipFile(output/(name+'.zip')) as archive:
                assert archive.testzip() is None
                assert {'task.json','plan-history.json','comparison.md','graph.json','writing-plan.md','references.bib','diagram/figure.pdf'} <= set(archive.namelist())
                assert json.loads(archive.read('task.json')) == result
            cases.append({'id': result['id'], 'case': name, 'status': result['status'], 'plan_version': result['plan_version'],
                          'step_statuses': [s['status'] for s in result['steps']], 'usage': result['usage'],
                          'external_provider_calls': 0, 'injected_first_retrieval_miss': missing, 'zip_crc_and_contents_match': True})
            print(json.dumps(cases[-1], ensure_ascii=False), flush=True)
        assert store.stats() == before
        save('summary.json', {'application_version': frozen['application_version'], 'cases': cases,
                              'source_snapshots_match': True, 'library_stats_unchanged': before,
                              'external_provider_calls': 0, 'simulation_labeled': True, 'semantic_quality_verified': False})
        save('simulated_request.json', {'simulated_only': True, 'external_provider_calls': 0, 'requests': simulated_requests})
    finally:
        model_client.httpx.AsyncClient = original
        workflow.qa._retrieve_question = original_retrieve
        await workflow.shutdown()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ['freeze','workspace','output']:parser.add_argument('--'+name, type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():parser.error('Preserve first outputs; choose a new directory.')
    frozen = json.loads(args.freeze.read_text(encoding='utf-8'))
    for name, expected in frozen['files'].items():
        if hashlib.sha256((ROOT/name).read_text(encoding='utf-8').encode()).hexdigest() != expected:
            raise SystemExit('Frozen code changed: '+name)
    from dotenv import load_dotenv
    load_dotenv(ROOT/'.env', override=False)
    os.environ.update(SCHOLARTRACE_DATA_DIR=str(args.workspace.resolve()), SCHOLARTRACE_ENABLE_DENSE='false',
                      SCHOLARTRACE_ENABLE_OCR='false', SCHOLARTRACE_ENABLE_TABLES='false', LLM_API_KEY='')
    from backend.app import store
    for source in frozen['sources']:
        assert store.get_document(source['id'])['sha256'] == source['sha256']
        assert digest_source(store, source) == source['blocks_sha256']
    args.output.mkdir(parents=True)
    asyncio.run(exercise(frozen, args.output))


if __name__ == '__main__':main()
