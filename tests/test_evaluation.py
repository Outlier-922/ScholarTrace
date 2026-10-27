"""Evaluation integrity using synthetic blocks, never the actual gold answers."""
from copy import deepcopy
import pytest
from scripts.eval_core import (anchor_matches, bind_sources, digest, request_body,
                               score_response, summarize, text_hash, validate_pack, percentile)


@pytest.fixture
def evaluation():
    block={'id':'b1','page':2,'bbox':[1,2,3,4],'text':'SYNTHETIC batch 37.'}
    anchor={'source':'paper','page':2,'bbox':[1,2,3,4],'text_sha256':text_hash(block['text'])}
    source={'work':'1234.56789v1','aliases':['arxiv:1234.56789v1'],'sha256':'sha1',
            'title':'Synthetic example','version_label':'v1','split':'development'}
    case={'id':'D01','split':'development','sources':['paper'],'question':'SYNTHETIC batch size?',
          'gold_groups':[[anchor]],'expected_facts':'37'}
    pack={'sources':{'paper':source},'cases':[case]}
    document={'id':'d1','sha256':'sha1','version_label':'v1','parse_run_id':'r1'}
    bound={'paper':{'document':document,'parse_run_id':'r1','blocks':{'b1':block}}}
    evidence={'id':'ev1','document_id':'d1','parse_run_id':'r1','block_id':'b1',**{k:block[k] for k in ('page','bbox','text')}}
    response={'mode':'llm','evidence':[evidence],'claims':[{'text':'37','evidence_ids':['ev1']}]}
    return pack,case,bound,response


def test_pack_scope_and_duplicates(evaluation):
    pack,case,_,_=evaluation;validate_pack(pack)
    pack['cases'].append(case)
    with pytest.raises(ValueError,match='duplicate_case'):validate_pack(pack)


@pytest.mark.parametrize('alias', ['arxiv:1234.56789v7','https://arxiv.org/abs/1234.56789v2'])
def test_work_version_leakage(evaluation,alias):
    pack,*_=evaluation
    pack['sources']['other']={**pack['sources']['paper'],'work':alias,'aliases':[],
                              'sha256':'different','title':'Different title','split':'holdout'}
    with pytest.raises(ValueError,match='work_leakage'):validate_pack(pack)


def test_same_content_leakage(evaluation):
    pack,*_=evaluation
    pack['sources']['copy']={**pack['sources']['paper'],'work':'different', 'aliases':[],
                             'title':'Other','split':'holdout'}
    with pytest.raises(ValueError,match='work_leakage'):validate_pack(pack)


def test_cross_split_case_rejected(evaluation):
    pack,case,*_=evaluation;case['split']='holdout'
    with pytest.raises(ValueError,match='crosses_split'):validate_pack(pack)


def test_sha_version_and_gold_binding(evaluation):
    pack,_,bound,_=evaluation;b=bound['paper'];document=b['document']
    get=lambda *_:{'parse_run_id':'r1','blocks':list(b['blocks'].values())}
    assert bind_sources(pack,[document],get)['paper']['parse_run_id']=='r1'
    with pytest.raises(ValueError,match='source_sha'):bind_sources(pack,[{**document,'sha256':'wrong'}],get)
    with pytest.raises(ValueError,match='source_version'):bind_sources(pack,[{**document,'version_label':'v2'}],get)
    with pytest.raises(ValueError,match='gold_anchor'):bind_sources(pack,[document],lambda *_:{'parse_run_id':'r2','blocks':[]})


def test_gold_never_sent(evaluation):
    _,case,bound,_=evaluation
    request=request_body(case,bound,False)
    assert set(request)=={'question','document_ids','parse_runs','limit','use_model'}
    assert request['parse_runs']=={'d1':'r1'} and not request['use_model']


@pytest.mark.parametrize('field,value',[('document_id','outside'),('parse_run_id','new'),('text','tampered'),('bbox',[0,0,0,0])])
def test_response_anchor_tampering(evaluation,field,value):
    _,case,bound,response=evaluation;response['evidence'][0][field]=value
    score=score_response(case,response,bound)
    assert score['valid_anchor_count']==0 and score['gold_groups_retrieved']==0
    assert score['claims_with_valid_source_ids']==0


def test_duplicate_evidence_and_invalid_citations(evaluation):
    _,case,bound,response=evaluation
    response['evidence'].append(deepcopy(response['evidence'][0]))
    response['claims'][0]['evidence_ids']=['invented']
    score=score_response(case,response,bound)
    assert score['evidence_count']==2 and score['valid_anchor_count']==1
    assert score['claims_with_valid_source_ids']==0


def make_row(evaluation):
    _,case,bound,response=evaluation
    return {'case_id':case['id'],'case_sha256':digest(case),'response_sha256':digest(response),
            'elapsed_seconds':1,'gold_groups':1,'live_model_requested':True,
            'score':score_response(case,response,bound)}


def test_unreviewed_does_not_become_accuracy(evaluation):
    report=summarize([make_row(evaluation)])
    assert report['evidence_anchor_validity']==1 and report['review_coverage']==0
    assert report['fully_correct_over_all_cases'] is None and report['citation_semantic_support'] is None


def test_errors_count_in_recall_denominator(evaluation):
    row=make_row(evaluation)
    result=summarize([row,{'case_id':'failed','elapsed_seconds':2,'gold_groups':1}])
    assert result['gold_group_recall_at_6']==.5 and result['request_errors']==1
    assert result['latency_seconds']['n']==2


def test_attributed_review_hash_and_denominators(evaluation):
    row=make_row(evaluation)
    review={'case_id':'D01','case_sha256':row['case_sha256'],'response_sha256':row['response_sha256'],
            'reviewer':'Synthetic tester','status':'reviewed','judgment':'partial','supported_claims':1}
    report=summarize([row],[review]);assert report['fully_correct_over_all_cases']==0
    assert report['citation_semantic_support']==1
    review['response_sha256']='old'
    with pytest.raises(ValueError,match='stale'):summarize([row],[review])


def test_retrieval_only_is_not_refusal(evaluation):
    row=make_row(evaluation);row['score'].update(mode='evidence_only',claim_count=0);row['live_model_requested']=False
    report=summarize([row]);assert report['abstained_case_count']==0 and report['model_fallback_count']==0
    assert report['fully_correct_over_all_cases'] is None


def test_percentile_small_samples():
    assert percentile([5,1,3],.5)==3 and percentile([5,1,3],.95)==5
    assert percentile([], .5) is None


def test_offline_review_without_api(evaluation,tmp_path):
    import json
    from scripts.review_evaluation import review_run
    row=make_row(evaluation);row['split']='development';row['response']=evaluation[3]
    (tmp_path/'raw.json').write_text(json.dumps([row]),encoding='utf-8')
    assert review_run(tmp_path,[])['overall']['fully_correct_over_all_cases'] is None
    with pytest.raises(ValueError,match='unknown_review'):
        review_run(tmp_path,[{'case_id':'unknown'}])


def test_offline_review_detects_raw_change(evaluation,tmp_path):
    import json
    from scripts.review_evaluation import review_run
    row=make_row(evaluation);row['split']='development';row['response']={'changed':True}
    (tmp_path/'raw.json').write_text(json.dumps([row]),encoding='utf-8')
    with pytest.raises(ValueError,match='response_changed'):
        review_run(tmp_path,[])
