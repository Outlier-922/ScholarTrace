"""Source scoping and non-copying contracts; not linguistic quality scores."""
import asyncio
import pytest
from backend.app import translation


def paragraph(identity,page,text):
    return {'id':identity,'page':page,'text':text,'type':'paragraph'}


def test_cross_page_context_is_bounded_and_version_scoped(monkeypatch):
    target=paragraph('target',2,'teacher for an additional term in the loss.')
    earlier=paragraph('earlier',1,'We used a teacher for adaptation. '*100)
    after=paragraph('after',2,'The next experiment has a different purpose. '*100)
    calls=[]
    def get(document,page=None,parse_run_id=None):
        calls.append((document,page,parse_run_id))
        return {'blocks':[earlier] if page==1 else [target,after]}
    monkeypatch.setattr(translation.store,'get_structure',get)
    context=translation.translation_context('doc','historical',target)
    assert {c['block_id'] for c in context}=={'earlier','after'}
    assert all(len(c['text'])<=800 for c in context)
    assert all(doc=='doc' and run=='historical' for doc,_,run in calls)
    assert target['text']=='teacher for an additional term in the loss.'


def test_unmatched_source_cannot_acquire_context(monkeypatch):
    monkeypatch.setattr(translation.store,'get_structure',lambda *a,**k:{'blocks':[paragraph('target',2,'Changed source')]})
    assert not translation.translation_context('doc','run',paragraph('target',2,'Original source'))


@pytest.mark.parametrize('copy_neighbor_number',[False,True])
def test_context_is_not_target_and_protected_meanings_are_visible(monkeypatch,copy_neighbor_number):
    block=paragraph('target',1,'A loss term on 7 examples.')
    monkeypatch.setattr(translation.research_store,'get_translation',lambda key:None)
    saved=[]
    monkeypatch.setattr(translation.research_store,'save_translation',lambda *a:saved.append(a))
    monkeypatch.setattr(translation,'translation_context',lambda *a:[{'block_id':'neighbor','page':1,'text':'99 examples.'}])
    async def model(system,payload,**kwargs):
        assert payload['context'][0]['block_id']=='neighbor'
        token=next(iter(payload['protected_contents']))
        assert payload['protected_contents'][token]=='7'
        assert '99' not in payload['text'] and kwargs['max_tokens']==3500
        return {'translation':f'{token} 个样本上的损失项。'+('99' if copy_neighbor_number else '')}
    monkeypatch.setattr(translation.model_client,'chat_json',model)
    if copy_neighbor_number:
        with pytest.raises(ValueError,match='translation_changed_numbers'):
            asyncio.run(translation.translate_block('doc','run',block,'zh',{}))
        assert not saved
    else:
        _,text,status=asyncio.run(translation.translate_block('doc','run',block,'zh',{}))
        assert text=='7 个样本上的损失项。' and status=='translated' and saved


def test_old_translation_cache_is_not_reused(monkeypatch):
    block=paragraph('target',1,'The original sentence.')
    current=translation.cache_key('doc','run',block,'zh',{})
    monkeypatch.setattr(translation,'PROMPT_VERSION','translation-v1')
    assert current!=translation.cache_key('doc','run',block,'zh',{})


def test_ambiguous_percent_point_unit_is_preserved_as_one_token():
    original='Only 0.6% point behind while 40% smaller.'
    protected,contents=translation.protect_text(original,{})
    assert '0.6% point' in contents.values()
    assert translation.restore_text(protected,contents)==original


def test_fragment_and_unit_risks_remain_visible_after_review():
    fragment=paragraph('a',2,'teacher for an additional term in the loss. '+('The experiment is described here. '*3))
    assert translation.quality_warnings(fragment,'已校订译文')
    ordinary=paragraph('b',1,'The model is smaller. It is evaluated on the same benchmark.')
    assert not translation.quality_warnings(ordinary,'普通译文')
    assert translation.quality_warnings(paragraph('c',1,'The gap is 0.6% point.'),'差距0.6%分')
