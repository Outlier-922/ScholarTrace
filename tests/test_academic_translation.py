"""Software protection checks; scripted replies do not measure translation quality."""
import re

from backend.app import model_client
from test_workflow_agent import provider

TEXT='本研究计划比较两种方法，使用 32 个样本，学习率为 0.001。该实验尚未实施，不应解释为方法已得到验证。'


def translated(payload):
    tokens=re.findall(r'\[STP_[a-f0-9]+_\d+\]',payload['text'])
    return {'translation':'We plan to compare two methods using '+tokens[0]+' samples and a learning rate of '+tokens[1]+'. The experiment has not yet been conducted and should not be interpreted as validation of the method.'}


def test_persist_cache_export_and_original_snapshot(client,monkeypatch):
    seen=provider(monkeypatch,[translated])
    body={'text':TEXT,'use_model':True}
    first=client.post('/api/academic-translations',json=body)
    assert first.status_code==200,first.text
    value=first.json()
    assert value['status']=='translated' and value['source_text']==TEXT
    assert value['checks']['numbers_preserved'] and not value['semantic_verified']
    assert value['usage']['calls']==1
    again=client.post('/api/academic-translations',json=body).json()
    assert again['status']=='cached' and len(seen)==1
    assert client.get('/api/academic-translations/'+value['id']).json()['translation']==value['translation']
    export=client.get('/api/academic-translations/'+value['id']+'/markdown')
    assert TEXT in export.text and value['translation'] in export.text
    assert any(item['id']==value['id'] for item in client.get('/api/academic-translations').json()['items'])


def test_disabled_or_missing_model_shows_source_only_even_if_cached(client,monkeypatch):
    def forbidden(*args,**kwargs):raise AssertionError('No model request permitted')
    monkeypatch.setattr(model_client,'chat_json',forbidden)
    for enabled in [False,True]:
        monkeypatch.setenv('LLM_API_KEY','')
        value=client.post('/api/academic-translations',json={'text':TEXT,'use_model':enabled}).json()
        assert value['status']=='not_translated' and value['translation'] is None and value['source_text']==TEXT
        assert value['usage']['calls']==0 and 'id' not in value


def test_changed_number_or_missing_placeholder_not_saved(client,monkeypatch):
    before=client.get('/api/academic-translations').json()
    provider(monkeypatch,[{'translation':'The results prove that the method is better in 999 cases.'}])
    response=client.post('/api/academic-translations',json={'text':TEXT+' 这是保护检查。','use_model':True})
    assert response.status_code==502
    assert client.get('/api/academic-translations').json()==before


def test_wrong_language_rejected(client,monkeypatch):
    text='该研究讨论证据溯源和推理可靠性之间的关系，当前仅为计划，尚无实验结论。'
    provider(monkeypatch,[{'translation':text}])
    assert client.post('/api/academic-translations',json={'text':text,'use_model':True}).status_code==502


def test_formula_citation_and_glossary_preserved(client,monkeypatch):
    text='我们计划使用混合召回验证公式 $x=2y$ 的示例，并参照文献 [12] 的论述；这里没有报告已完成的实验。'
    def result(payload):
        tokens=re.findall(r'\[STP_[a-f0-9]+_\d+\]',payload['text'])
        assert len(tokens)==3
        return {'translation':f'We plan to use {tokens[0]} to validate an example of {tokens[1]}, referring to the discussion in {tokens[2]}; no completed experiment is reported here.'}
    provider(monkeypatch,[result])
    response=client.post('/api/academic-translations',json={'text':text,'glossary':{'混合召回':'hybrid retrieval'},'use_model':True})
    assert response.status_code==200,response.text
    english=response.json()['translation']
    assert all(item in english for item in ['hybrid retrieval','$x=2y$','[12]'])


def test_unknown_fields_or_invalid_glossary_rejected(client):
    for changes in [{'glossary':{'不存在的术语':'missing'}},{'max_model_calls':100},{'text':'English only abstract without any Chinese content.'}]:
        assert client.post('/api/academic-translations',json={'text':TEXT,**changes}).status_code==422
