"""Fictional title/name witnesses; no real paper labels or gold values."""
import copy
import pytest
from backend.app import graph,graph_identity as identity


def block(key,text,kind,y,font=16):return {'id':key,'page':1,'bbox':[.15,y,.85,y+.025],'text':text,'type':kind,'metadata':{'font_size':font}}


def witness():
    blocks=[block('t','Synthetic Evidence Methods','title',.1),block('subtitle','with Recoverable Sources','heading',.13),block('a','Mira Example*, Alan Fiction','paragraph',.2,10)]
    return identity.pdf_identity({'title':'Synthetic Evidence Methods'},{'blocks':blocks})


def sources():return [{'node_id':'local:A','document_id':'A','parse_run_id':'rA','title':'Another source'},
                      {'node_id':'local:B','document_id':'B','parse_run_id':'rB','title':'Synthetic Evidence Methods','identity_witness':witness()}]


def unit(text,ids=None):return {'id':'u','text':text,'source':sources()[0],'sources':[],'backlinks':[],'identifiers':ids or []}


def test_title_continuation_and_author_have_original_block_witnesses():
    w=witness();assert w['title']=='Synthetic Evidence Methods with Recoverable Sources' and w['surname']=='example'
    assert [b['block_id'] for b in w['title_sources']]==['t','subtitle'] and w['author_source']['block_id']=='a'


@pytest.mark.parametrize('reference',['M. Other. Synthetic Evidence Methods with Recoverable Sources.','M. Example. Similar Evidence Methods with Recoverable Sources.','M. Example. Synthetic Evidence Methods.'])
def test_title_prefix_similarity_or_title_without_author_is_not_a_match(reference):
    assert not identity.matches(reference,sources())


def test_affiliation_and_sideways_identifier_are_not_author_names():
    blocks=[block('t','Synthetic Evidence Methods','title',.1),block('id','arXiv:2699.11111','heading',.2,20),block('org','Example Research Institute','paragraph',.25,10)]
    assert identity.pdf_identity({'title':'Synthetic Evidence Methods'},{'blocks':blocks}) is None


def test_local_title_author_edge_has_no_fabricated_registration_and_enters_metrics():
    result=graph.assemble(sources(),[unit('M. Example. Synthetic Evidence Methods with Recoverable Sources. 2099.')],{},'fictional',20)
    edge=result['edges'][0];assert edge['status']=='local_title_author_matched' and edge['target']=='local:B'
    assert result['metrics']['eligible_edges']==1 and edge['evidence'][0]['cited_identifier']==''
    assert edge['evidence'][0]['target_identity']['document_id']=='B' and edge['evidence'][0]['cited_version_status']=='not_determined'
    assert result['nodes'][1]['landing_url'] is None and result['nodes'][1]['year'] is None


def test_explicit_identifier_alias_maps_to_local_work_but_conflicting_local_id_does_not():
    reference='M. Example. Synthetic Evidence Methods with Recoverable Sources. arXiv:2699.33333.'
    g=graph.assemble(sources(),[unit(reference,[('arxiv:2699.33333','2699.33333')])],{},'fictional',20)
    assert len(g['nodes'])==2 and g['edges'][0]['target']=='local:B'
    g=graph.assemble(sources(),[unit(reference,[('local:A','2699.33333')])],{},'fictional',20)
    assert not g['edges'] and g['unresolved_references']


def test_multiple_local_candidates_or_multiple_identifiers_are_not_guessed():
    s=sources();s.append({**copy.deepcopy(s[1]),'node_id':'local:C','document_id':'C'})
    ref='M. Example. Synthetic Evidence Methods with Recoverable Sources.'
    assert not graph.assemble(s,[unit(ref)],{},'fictional',20)['edges']
    assert not graph.assemble(sources(),[unit(ref,[('arxiv:2699.33333','2699.33333'),('arxiv:2699.44444','2699.44444')])],{},'fictional',20)['edges']


@pytest.mark.parametrize('extraction,expected',[('native_pdf',True),('generated_table',False)])
def test_only_native_bibliography_continuation_can_recover_a_table_candidate(extraction,expected):
    head=block('h','References','heading',.1);first=block('r','[1] M. Example, A. Fiction,','paragraph',.2,10)
    continuation=block('c','B. Tester. Synthetic Evidence Methods with Recoverable Sources. In Fictional Proceedings, 2099. URL https://example.test/ref','table_candidate',.25,10)
    continuation['metadata']['extraction']=extraction
    structure={'blocks':[head,first,continuation],'references':[{'id':'r1','marker':'1','block_id':'r','text':first['text']},{'id':'r2','marker':None,'block_id':'c','text':continuation['text']}]}
    units=graph.bibliography_units(structure)
    assert ('Recoverable Sources' in units[0]['text'])==expected
    assert continuation['type']=='table_candidate'
