"""Real local executors with scripted provider replies, not model quality claims."""
import asyncio
import copy
import json
import threading

import httpx
import pytest

from backend.app import model_client, workflow, workflow_agent
from test_workflow import papers, planned, start, wait, configure
from test_tables import grid_pdf
from conftest import upload_pdf, wait_for_document


def provider(monkeypatch, replies):
    configure(monkeypatch)
    seen = []

    class Client:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def post(self, url, **kwargs):
            payload = json.loads(kwargs['json']['messages'][1]['content'])
            seen.append(payload)
            result = replies[len(seen)-1]
            if callable(result): result = result(payload)
            return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps(result)}}],
                                           'usage': {'prompt_tokens': 50, 'completion_tokens': 20}},
                                  request=httpx.Request('POST', url))
    monkeypatch.setattr(model_client.httpx, 'AsyncClient', Client)
    return seen


def call(tool, query=None):
    return {'action': 'call', 'tool': tool, 'arguments': {'query': query} if query else {}, 'reason': '测试选择依据'}


FINISH = {'action': 'finish', 'reason': '证据已收集，语义仍待核对'}


def test_loop_chooses_order_from_real_observations_and_exports(client, papers, monkeypatch):
    def after_graph(payload):
        assert payload['observations'][0]['tool'] == 'graph'
        assert payload['observations'][0]['counts']['nodes'] > 0
        return call('evidence', 'model architecture')
    seen = provider(monkeypatch, [call('graph'), after_graph, call('hybrid_retrieval', 'training objective'), FINISH])
    task = planned(client, papers, planner='agent', actions=['evidence', 'graph'])
    assert task['steps'] == [] and task['planner_model_calls'] == 0
    start(client, task)
    result = wait(client, task['id'])
    assert result['status'] == 'completed_with_gaps', result
    assert [step['tool'] for step in result['steps']] == ['graph', 'evidence', 'hybrid_retrieval']
    assert result['usage']['model']['calls'] == 4
    assert result['usage']['tool_calls'] == 3
    assert result['steps'][1]['result']['mode'] == 'evidence_only'
    assert result['steps'][1]['result']['claims'] == []
    assert all('parameters' in tool for payload in seen for tool in payload['tools'])
    assert seen[2]['observations'][1]['evidence']
    exported = client.get('/api/research-tasks/' + task['id'] + '/export?format=json').json()
    assert len(exported['agent']['decisions']) == 4
    assert exported['sources'] == task['sources']


def test_review_reuses_exact_graph_step_in_source_only_mode(client, papers, monkeypatch):
    seen = provider(monkeypatch, [call('graph'), call('review'), FINISH])
    task = planned(client, papers, goal='构建引用图谱，再展示综述原文提纲', planner='agent', actions=['graph', 'review'])
    start(client, task)
    result = wait(client, task['id'])
    first, second = [step['result'] for step in result['steps']]
    assert second['graph_id'] == first['id']
    assert second['mode'] == 'evidence_only' and second['evidence']
    assert client.get('/api/graphs').json()['items'][0]['id'] == first['id']
    assert len(seen) == 3


@pytest.mark.parametrize('bad', [call('shell'), call('graph', 'forbidden'),
    {'action': 'call', 'tool': 'evidence', 'arguments': {'query': 'x y', 'document_ids': ['foreign']}, 'reason': 'bad'},
    {'action': 'finish', 'reason': 'done', 'answer': 'fabricated'}])
def test_invalid_decision_falls_back_without_generation_or_new_sources(client, papers, monkeypatch, bad):
    seen = provider(monkeypatch, [bad])
    task = planned(client, papers, planner='agent', actions=['evidence'])
    start(client, task)
    result = wait(client, task['id'])
    assert result['status'] == 'completed_with_gaps', result
    assert result['execution_model_disabled']
    assert len(seen) == 1
    assert result['steps'][0]['result']['mode'] == 'evidence_only'
    assert result['sources'] == task['sources']
    assert not result['agent']['decisions']


def test_shared_budget_stops_and_resume_does_not_repeat_tool(client, papers, monkeypatch):
    seen = provider(monkeypatch, [call('evidence', 'model architecture'), FINISH])
    task = planned(client, papers, planner='agent', actions=['evidence'], max_model_calls=1)
    start(client, task)
    exhausted = wait(client, task['id'])
    assert exhausted['status'] == 'budget_exhausted'
    assert exhausted['usage']['model']['calls'] == 1
    assert exhausted['usage']['tool_calls'] == 1
    assert client.post('/api/research-tasks/' + task['id'] + '/control', json={'action': 'resume', 'max_model_calls': 2}).status_code == 200
    finished = wait(client, task['id'])
    assert finished['status'] == 'completed_with_gaps'
    assert len(seen) == 2 and finished['usage']['tool_calls'] == 1
    assert finished['steps'][0]['result'] == exhausted['steps'][0]['result']


def test_last_allowed_tool_can_complete_required_scope(client, papers, monkeypatch):
    seen = provider(monkeypatch, [call('evidence', 'model architecture')])
    task = planned(client, papers, goal='检索模型架构原文', planner='agent',
                   actions=['evidence'], max_tool_calls=1)
    start(client, task)
    result = wait(client, task['id'])
    assert result['status'] == 'completed_with_gaps'
    assert result['steps'][0]['status'] == 'completed'
    assert result['usage']['tool_calls'] == len(seen) == 1
    assert not any('evidence' in gap for gap in result['gaps'])


def test_early_finish_records_unfulfilled_goal(client, papers, monkeypatch):
    provider(monkeypatch, [FINISH])
    task = planned(client, papers, planner='agent', actions=['graph'])
    start(client, task)
    finished = wait(client, task['id'])
    assert finished['usage']['tool_calls'] == 0
    assert any('graph' in gap for gap in finished['gaps'])


def test_pause_during_decision_does_not_execute_tool(client, papers, monkeypatch):
    configure(monkeypatch)
    entered = threading.Event(); release = threading.Event()
    async def decision(*args, **kwargs):
        entered.set()
        while not release.is_set(): await asyncio.sleep(.01)
        return call('evidence', 'model architecture')
    monkeypatch.setattr(model_client, 'chat_json', decision)
    task = planned(client, papers, planner='agent', actions=['evidence'])
    start(client, task)
    assert entered.wait(3)
    client.post('/api/research-tasks/' + task['id'] + '/control', json={'action': 'pause'})
    release.set()
    paused = wait(client, task['id'])
    assert paused['status'] == 'paused'
    assert paused['usage']['tool_calls'] == 0
    assert paused['steps'][0]['status'] == 'waiting'


def test_catalog_omits_unconfirmed_specs_and_blocked_tools(client, papers, monkeypatch):
    configure(monkeypatch)
    task = planned(client, papers, planner='agent', goal='检索模型架构，不要引用图谱')
    names = {item['name'] for item in workflow_agent.catalog(task)}
    assert {'evidence','hybrid_retrieval','table_qa'} <= names
    assert not names.intersection({'graph','translation','discovery','plot'})
    before = copy.deepcopy(task)
    workflow_agent.accept(task, call('evidence', 'architecture'))
    with pytest.raises(ValueError, match='duplicate_tool_call'):
        workflow_agent.accept(task, call('evidence', 'architecture'))
    assert task['sources'] == before['sources']


def test_no_model_agent_cannot_silently_claim_autonomy(client, papers, monkeypatch):
    monkeypatch.setenv('LLM_API_KEY', '')
    response = client.post('/api/research-tasks', json={'goal':'检索模型架构', 'document_ids':[papers[0]['id']], 'planner':'agent'})
    assert response.status_code == 409


def test_decision_timeout_is_budget_exhaustion(client, papers, monkeypatch):
    configure(monkeypatch)
    async def slow(*args, **kwargs): await asyncio.sleep(10)
    monkeypatch.setattr(model_client, 'chat_json', slow)
    task = planned(client, papers, planner='agent', max_active_seconds=5)
    workflow.repo.mutate(task['id'], lambda value: value['usage'].update(active_seconds=4.95))
    start(client, task)
    finished = wait(client, task['id'])
    assert finished['status'] == 'budget_exhausted'
    assert finished['usage']['tool_calls'] == 0


def test_table_tool_never_substitutes_prose_when_tables_absent(client, papers, monkeypatch):
    provider(monkeypatch, [call('table_qa', 'model architecture'), FINISH])
    task = planned(client, papers, planner='agent', actions=['evidence'])
    start(client, task)
    finished = wait(client, task['id'])
    result = finished['steps'][0]['result']
    assert result['retrieval']['mode'] == 'table_blocks_bm25'
    assert result['evidence'] == [] and result['claims'] == []


def test_table_tool_preserves_real_native_table_anchor(client, grid_pdf, monkeypatch):
    monkeypatch.setenv('SCHOLARTRACE_ENABLE_TABLES', 'true')
    paper = wait_for_document(client, upload_pdf(client, grid_pdf)['id'])
    provider(monkeypatch, [call('table_qa', 'Synthetic Alpha Accuracy'), FINISH])
    task = planned(client, [paper], planner='agent', actions=['evidence'])
    start(client, task)
    finished = wait(client, task['id'])
    assert finished['status'] == 'completed_with_gaps', finished
    evidence = finished['steps'][0]['result']['evidence']
    assert evidence and all(item['block_type'] == 'table' for item in evidence)
    assert '72.5' in evidence[0]['text']
    workflow.validate_result(finished, {'evidence': evidence})


def test_generated_claims_still_use_existing_source_and_number_checks(client, papers, monkeypatch):
    def fabricated(payload):
        return {'claims': [{'text': 'The model has 987654321 layers.', 'evidence_ids': ['invented']} ]}
    seen = provider(monkeypatch, [call('evidence', 'model architecture'), fabricated, FINISH])
    task = planned(client, papers, planner='agent', actions=['evidence'], use_model=True)
    start(client, task)
    finished = wait(client, task['id'])
    assert finished['status'] == 'completed_with_gaps', finished
    result = finished['steps'][0]['result']
    assert result['claims'] == []
    assert '987654321' not in result['answer']
    assert finished['usage']['model']['calls'] == len(seen)


def test_stop_policy_never_executes_fallback(client, papers, monkeypatch):
    provider(monkeypatch, [call('shell')])
    task = planned(client, papers, planner='agent', actions=['evidence'], recovery_policy='stop')
    start(client, task)
    finished = wait(client, task['id'])
    assert finished['status'] == 'failed' and finished['usage']['tool_calls'] == 0


def test_next_retrieval_carries_earlier_paper_within_the_shared_limit(client, papers, monkeypatch):
    def retrieve(question, documents, limit, diagnostics, *, initial_query, sparse_only):
        index = 0 if initial_query == 'first paper' else 1
        assert sparse_only == (index == 1)
        diagnostics['coverage'] = {doc['id']: int(i == index) for i, doc in enumerate(documents)}
        return workflow.qa._collect_sources([documents[index]])[:1]

    monkeypatch.setattr(workflow.qa, '_retrieve_question', retrieve)
    provider(monkeypatch, [call('hybrid_retrieval', 'first paper'), call('evidence', 'second paper'), FINISH])
    task = planned(client, papers, planner='agent', goal='查看所选论文的原文', actions=['evidence'], max_tool_calls=4)
    start(client, task)
    finished = wait(client, task['id'])
    assert finished['status'] == 'completed_with_gaps'
    first, second = [step['result'] for step in finished['steps']]
    assert len(first['uncovered_documents']) == 1
    assert second['uncovered_documents'] == []
    assert second['retrieval']['carried_evidence_count'] == 1
    assert len(second['evidence']) == 2 and sum(second['retrieval']['coverage'].values()) == 2
    assert first['evidence'][0]['id'] in {item['id'] for item in second['evidence']}
    assert second['mode'] == 'evidence_only' and second['claims'] == []
    assert all('未命中' not in gap for gap in finished['gaps'])
    assert finished['usage']['tool_calls'] == 2
