"""Bounded controller contracts; provider failures are injected, never live."""
import copy
import io
import json
import zipfile
import httpx
import pytest
from backend.app import workflow, model_client
from test_workflow import papers, planned, start, wait, configure


def failing_provider(monkeypatch):
    configure(monkeypatch)
    requests = []
    class Failed:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def post(self, url, **kwargs):
            requests.append(kwargs['json'])
            raise httpx.ReadTimeout('SYNTHETIC PRIVATE PROVIDER BODY', request=httpx.Request('POST', url))
    monkeypatch.setattr(model_client.httpx, 'AsyncClient', Failed)
    return requests


def test_model_failure_changes_plan_once_and_keeps_failure_and_sources(client, papers, monkeypatch):
    requests = failing_provider(monkeypatch)
    task = planned(client, papers, use_model=True, actions=['evidence', 'comparison', 'writing', 'diagram'])
    sources = copy.deepcopy(task['sources'])
    start(client, task); result = wait(client, task['id'])
    assert result['status'] == 'completed_with_gaps', result
    assert len(requests) == 1 and result['usage']['model']['calls'] == 1
    assert result['usage']['model']['reserved_output_tokens'] == 1600
    assert result['usage']['model']['unreported_usage'] == 1
    assert result['sources'] == sources and result['execution_model_disabled'] is True
    failed, recovery, comparison = result['steps'][:3]
    assert failed['status'] == 'failed' and failed['attempts'] == 1
    assert failed['error_code'] == 'ReadTimeout' and failed['recovery_status'] == 'local_completed'
    assert failed['partial_result']['evidence'] == recovery['result']['evidence']
    assert recovery['recovery_for'] == failed['id'] and recovery['result']['recovery']['model_retried'] is False
    assert comparison['depends_on'] == [recovery['id']]
    assert result['plan_version'] == 2 and len(result['plan_history']) == 2
    assert len({v['source_digest'] for v in result['plan_history']}) == 1
    assert 'SYNTHETIC PRIVATE PROVIDER BODY' not in json.dumps(result)
    exported = client.get('/api/research-tasks/' + task['id'] + '/export')
    assert exported.status_code == 200
    with zipfile.ZipFile(io.BytesIO(exported.content)) as archive:
        assert json.loads(archive.read('plan-history.json')) == result['plan_history']
        assert 'model_step_failed' in archive.read('task.md').decode()


@pytest.mark.parametrize('options', [{'recovery_policy': 'stop'}, {'max_replans': 0}])
def test_explicit_stop_or_zero_adjustments_preserves_partial_result(client, papers, monkeypatch, options):
    requests = failing_provider(monkeypatch)
    task = planned(client, papers, use_model=True, actions=['evidence', 'writing'], **options)
    start(client, task); result = wait(client, task['id'])
    assert result['status'] == 'failed' and len(requests) == 1
    assert result['steps'][0]['partial_result']['evidence']
    assert result['steps'][1]['attempts'] == 0 and result['plan_version'] == 1


def test_recovery_resumes_after_tool_budget_without_reissuing_failed_model(client, papers, monkeypatch):
    requests = failing_provider(monkeypatch)
    task = planned(client, papers, use_model=True, actions=['evidence', 'writing'], max_tool_calls=1)
    start(client, task); checkpoint = wait(client, task['id'])
    assert checkpoint['status'] == 'budget_exhausted' and checkpoint['steps'][0]['resolved_by']
    monkeypatch.setenv('LLM_API_KEY', '')
    response = client.post('/api/research-tasks/' + task['id'] + '/control', json={'action': 'resume', 'max_tool_calls': 6})
    assert response.status_code == 200, response.text
    result = wait(client, task['id'])
    assert result['status'] == 'completed_with_gaps' and len(requests) == 1
    assert result['steps'][0]['attempts'] == 1 and result['usage']['tool_calls'] == 3


def test_recovery_checkpoint_restart_keeps_failed_parent_resolved(client, papers, monkeypatch):
    requests = failing_provider(monkeypatch)
    task = planned(client, papers, use_model=True, actions=['evidence', 'writing'], max_tool_calls=1)
    start(client, task); wait(client, task['id'])
    def interrupted(value):
        value['status'] = 'running'; value['steps'][1]['status'] = 'running'
    workflow.repo.mutate(task['id'], interrupted); workflow.repo.recover()
    assert client.post('/api/research-tasks/' + task['id'] + '/control', json={'action': 'resume', 'max_tool_calls': 6}).status_code == 200
    result = wait(client, task['id'])
    assert result['status'] == 'completed_with_gaps' and len(requests) == 1
    assert result['steps'][0]['attempts'] == 1 and result['steps'][1]['recovered_after_interruption']


def test_unexpected_tool_failure_is_not_silently_recovered(client, papers, monkeypatch):
    original = workflow.execute
    async def broken(payload, step):
        if step['tool'] == 'writing': raise RuntimeError('synthetic tool bug')
        return await original(payload, step)
    monkeypatch.setattr(workflow, 'execute', broken)
    task = planned(client, papers, actions=['evidence', 'writing']); start(client, task)
    result = wait(client, task['id'])
    assert result['status'] == 'failed' and result['plan_version'] == 1
    assert result['steps'][0]['status'] == 'completed' and result['steps'][1]['status'] == 'failed'


def test_missing_document_feedback_rewires_dependencies_once(client, papers):
    task = planned(client, papers, goal='qxzunmatchedkeyword', actions=['evidence', 'writing'])
    start(client, task); result = wait(client, task['id'])
    assert result['status'] == 'completed_with_gaps'
    assert result['steps'][2]['depends_on'] == ['supplement']
    assert result['plan_history'][1]['reason'] == 'missing_documents'
    assert result['query_expansions'] == 1


def test_zero_adjustments_does_not_insert_supplement(client, papers):
    task = planned(client, papers, goal='qxzunmatchedkeyword', actions=['evidence'], max_replans=0)
    start(client, task); result = wait(client, task['id'])
    assert len(result['steps']) == 1 and result['plan_version'] == 1
    assert result['steps'][0]['result']['uncovered_documents']


def test_model_planner_cannot_override_explicit_exclusions_or_drop_required_output(client, papers, monkeypatch):
    configure(monkeypatch)
    async def proposal(*args, **kwargs):
        return {'actions': ['graph', 'writing'], 'dimensions': ['hardware']}
    monkeypatch.setattr(model_client, 'chat_json', proposal)
    task = planned(client, papers, goal='比较硬件，不要引用图谱，不要写作', planner='model')
    assert [s['tool'] for s in task['steps']] == ['evidence', 'comparison']
    assert task['planner_model_calls'] == 1 and task['warnings']
    assert 'hardware' in task['dimensions']


def test_manual_actions_do_not_pay_for_an_ignored_model_plan(client, papers, monkeypatch):
    async def forbidden(*args, **kwargs): raise AssertionError('No planning request is needed')
    monkeypatch.setattr(model_client, 'chat_json', forbidden)
    monkeypatch.setenv('LLM_API_KEY', '')
    task = planned(client, papers, planner='model', actions=['evidence'])
    assert task['planner_model_calls'] == 0 and task['planner_mode'] == 'rules'


@pytest.mark.parametrize('value', [-1, 4])
def test_adjustment_limit_is_bounded(client, papers, value):
    response = client.post('/api/research-tasks', json={'goal': 'compare hardware', 'document_ids': [p['id'] for p in papers], 'max_replans': value})
    assert response.status_code == 422


def test_unmet_dependency_stops_without_invoking_later_tool(client, papers):
    task = planned(client, papers, actions=['evidence', 'writing'])
    workflow.repo.mutate(task['id'], lambda value: value['steps'][1].update(depends_on=['missing-step']))
    start(client, task); result = wait(client, task['id'])
    assert result['status'] == 'failed' and result['steps'][1]['attempts'] == 0
    assert any(e['label'] == '步骤依赖未满足' for e in result['events'])


def test_bad_partial_anchor_cannot_be_used_as_local_recovery(client, papers, monkeypatch):
    configure(monkeypatch)
    original = workflow.execute
    async def bad(payload, step):
        if step['tool'] != 'evidence': return await original(payload, step)
        partial = await workflow.retrieve({**payload, 'use_model': False})
        partial['evidence'][0]['page'] += 1
        raise workflow.policy.ModelStepFailure(partial, httpx.ReadTimeout('synthetic'))
    monkeypatch.setattr(workflow, 'execute', bad)
    task = planned(client, papers, use_model=True, actions=['evidence', 'writing'])
    start(client, task); result = wait(client, task['id'])
    assert result['status'] == 'failed' and result['plan_version'] == 1
    assert result['steps'][0]['status'] == 'failed' and not result['steps'][0].get('resolved_by')
    assert result['steps'][1]['attempts'] == 0


@pytest.mark.parametrize('failing_tool,expected_requests', [('comparison', 3), ('review', 2)])
def test_internal_model_degradation_disables_later_model_steps(client, papers, monkeypatch, failing_tool, expected_requests):
    configure(monkeypatch); requests = []
    class Provider:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def post(self, url, **kwargs):
            requests.append(kwargs['json'])
            if len(requests) > 1:
                raise httpx.ReadTimeout('synthetic private body', request=httpx.Request('POST', url))
            return httpx.Response(200, json={'choices': [{'message': {'content': '{"claims":[]}'}}]}, request=httpx.Request('POST', url))
    monkeypatch.setattr(model_client.httpx, 'AsyncClient', Provider)
    task = planned(client, papers, use_model=True, actions=['evidence', failing_tool, 'writing'])
    start(client, task); result = wait(client, task['id'])
    assert result['status'] == 'completed_with_gaps', result
    assert len(requests) == expected_requests and result['execution_model_disabled'] is True
    assert result['plan_history'][-1]['reason'] == 'model_budget_or_tool_degradation'
    assert result['steps'][1]['result']['model_failures']


def test_adjustment_cap_applies_to_missing_evidence_and_failure_together(client, papers, monkeypatch):
    requests = failing_provider(monkeypatch)
    task = planned(client, papers, goal='qxzunmatchedkeyword', use_model=True, actions=['evidence'], max_replans=1)
    start(client, task); result = wait(client, task['id'])
    assert result['status'] == 'failed' and len(requests) == 1
    assert result['plan_version'] == 2 and len(result['steps']) == 2
    assert result['steps'][1]['status'] == 'failed'


def test_plan_sources_and_budgets_are_not_changed_by_feedback(client, papers, monkeypatch):
    failing_provider(monkeypatch)
    task = planned(client, papers, use_model=True, actions=['evidence', 'writing'])
    start(client, task); result = wait(client, task['id'])
    assert result['budgets'] == task['budgets'] and result['sources'] == task['sources']
    assert all(step['tool'] in workflow.TOOLS for step in result['steps'])
    assert len(result['plan_history'])-1 <= task['max_replans']


def test_missing_document_supplement_uses_recovered_evidence_after_model_failure(client, papers, monkeypatch):
    requests = failing_provider(monkeypatch)
    original = workflow.qa._retrieve_question
    scopes = []
    def retrieve(question, documents, limit, trace):
        scopes.append([document['id'] for document in documents])
        selected = documents[:1] if len(scopes) == 1 else documents
        return original(question, selected, limit, trace)
    monkeypatch.setattr(workflow.qa, '_retrieve_question', retrieve)
    task = planned(client, papers, use_model=True, actions=['evidence', 'writing'])
    start(client, task); result = wait(client, task['id'])
    assert result['status'] == 'completed_with_gaps' and len(requests) == 1
    assert scopes[1] == [papers[1]['id']]
    failed, recovery, supplement, writing = result['steps']
    assert failed['partial_result']['uncovered_documents'] == [papers[1]['id']]
    assert {item['document_id'] for item in supplement['result']['evidence']} == {papers[1]['id']}
    assert recovery['status'] == supplement['status'] == writing['status'] == 'completed'
    assert writing['depends_on'] == ['supplement']
    assert [version['reason'] for version in result['plan_history']] == ['initial_plan', 'model_step_failed', 'missing_documents']
    assert result['plan_version'] == 3 and result['usage']['tool_calls'] == 4
