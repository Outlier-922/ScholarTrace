"""Citation graph edges come from bibliography identifiers, never similarity."""
from __future__ import annotations
import hashlib
import json
import re
import unicodedata
import uuid
import xml.etree.ElementTree as ET
from collections import defaultdict
from urllib.parse import quote

import httpx
from fastapi import APIRouter,HTTPException,Query
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel,ConfigDict,Field
from starlette.concurrency import run_in_threadpool
from . import graph_store as repo,literature,literature_store,qa,reading,store

router=APIRouter(prefix='/api',tags=['citation-graph'])
ARXIV_SCAN=re.compile(r'\b(?:\d{4}\.\d{4,5}|[a-z][a-z.-]+/\d{7})(?:v\d+)?\b',re.I)
DOI_SCAN=re.compile(r'\b10\.\d{4,9}/[^\s<>"{}]+',re.I)
PURPOSES={'unknown','method','data','comparison','background'}


def norm(value):
    value=unicodedata.normalize('NFKD',value).casefold()
    return re.sub(r'[^a-z0-9\u4e00-\u9fff]','',value)


def root_identifier(value):return re.sub(r'v\d+$','',value.casefold())


def doc_key(document):
    identifier=document.get('arxiv_id')
    if identifier and literature.ARXIV.fullmatch(identifier):return 'arxiv:'+root_identifier(identifier)
    for provenance in literature_store.imports(document['id']):
        if provenance['key'].startswith('arxiv:'):return 'arxiv:'+root_identifier(provenance['identifier'])
    return 'local:'+document['work_id']


def choose_sources(documents,selected_versions=None):
    groups=defaultdict(list)
    for document in documents:groups[doc_key(document)].append(document)
    chosen=[]
    if set(selected_versions or {})-set(groups):raise ValueError('unknown_source_group')
    for key,versions in groups.items():
        requested=(selected_versions or {}).get(key)
        if requested and requested not in {item['id'] for item in versions}:raise ValueError('invalid_source_version')
        def priority(item):
            version=re.search(r'v(\d+)',item.get('version_label') or '')
            return (int(version.group(1)) if version else 0,'img' not in item['filename'].lower(),item['created_at'])
        selected=next(item for item in versions if item['id']==requested) if requested else max(versions,key=priority)
        chosen.append({'key':key,'document':selected,'versions':[{'document_id':item['id'],'version_label':item.get('version_label'),'filename':item['filename'],'parse_run_id':item['parse_run_id']} for item in sorted(versions,key=priority,reverse=True)]})
    return chosen


def bibliography_units(structure):
    """Join continuation fragments, retaining every source block; never alter the PDF."""
    blocks={block['id']:block for block in structure['blocks']}
    reference_blocks={item['block_id'] for item in structure['references']}
    bounded=set();inside=False
    for block in structure['blocks']:
        text=re.sub(r'\s+',' ',block['text']).strip()
        if block['type']=='heading' and re.fullmatch(r'(?:\d+\.?\s*)?(?:references|bibliography|参考文献)',text,re.I):
            inside=True;continue
        # Misclassified year-leading bibliography lines stay inside; appendix/visualization do not.
        if inside and block['type']=='heading' and re.match(r'(?i:appendix|附录|attention visualizations|acknowledg)|[A-Z](?:\.\d+)?\s+(?:Additional|Details|Experiments)',text):inside=False
        if inside:bounded.add(block['id'])
    items=[];current=None;numbered=False
    for reference in structure['references']:
        block=blocks.get(reference['block_id'])
        if not block or reference['block_id'] not in reference_blocks or reference['block_id'] not in bounded:continue
        if block['type'] not in {'paragraph','heading','reference','reference_entry'}:continue
        text=reference['text'].strip();marker=reference.get('marker')
        # An unnumbered author line is a candidate boundary, not an identity assertion.
        author_start=bool(re.match(r'^[A-ZÀ-Ž][^\d]{2,100}(?:,|\band\b)',text) and not re.match(r'^(?:In |Proceedings|Advances|International|Conference|Journal|URL )',text))
        start=bool(marker) or current is None or (not numbered and author_start and re.search(r'\b(?:19|20)\d{2}\b',current['text']))
        if start:
            current={'id':'bib_'+reference['id'],'marker':marker,'text':'','reference_ids':[],'sources':[]};items.append(current)
        numbered=numbered or bool(marker)
        current['text']+=('\n' if current['text'] else '')+text
        current['reference_ids'].append(reference['id'])
        if not any(source['block_id']==block['id'] for source in current['sources']):
            metadata=block.get('metadata') or {}
            current['sources'].append({'block_id':block['id'],'page':block['page'],'bbox':block['bbox'],'text':block['text'],
                                       'extraction':metadata.get('extraction','native'),'confidence':metadata.get('confidence')})
    return items


def cached_records():
    with store.connection() as conn:
        return [json.loads(row['payload']) for row in conn.execute('SELECT payload FROM literature_records')]


def registry_index(records):
    index={}
    for record in records:
        key='arxiv:'+root_identifier(record['identifier']) if record['provider']=='arxiv' else 'doi:'+record['identifier'].casefold()
        if key not in index or (record.get('retrieved_at') or '')>(index[key].get('retrieved_at') or ''):index[key]=record
    return index


def identifiers(text):
    values=[]
    for match in ARXIV_SCAN.finditer(text):
        identity=match.group().lower();values.append(('arxiv:'+root_identifier(identity),identity))
    for match in DOI_SCAN.finditer(text):
        identity=match.group().rstrip('.,;)]').lower()
        alias=identity.removeprefix('10.48550/arxiv.')
        values.append(('arxiv:'+root_identifier(alias),identity) if alias!=identity and literature.ARXIV.fullmatch(alias) else ('doi:'+identity,identity))
    return list(dict.fromkeys(values))


def identity_alignment(reference,metadata):
    if not metadata:return {'status':'not_checked','title_overlap':None,'first_author_present':None}
    normalize=lambda value:''.join(char for char in unicodedata.normalize('NFKD',value).casefold() if not unicodedata.combining(char))
    title_words=literature.words(normalize(metadata['title']))
    reference_words=literature.words(normalize(reference))
    overlap=len(title_words & reference_words)/max(1,len(title_words))
    first=(metadata.get('authors') or [''])[0].split()
    surname=norm(first[-1]) if first else ''
    author=bool(surname and len(surname)>=3 and surname in norm(reference))
    # A changed preprint title is possible. Flag conflicts only when BOTH checks disagree.
    conflict=len(title_words)>=2 and len(reference_words)>=5 and overlap<.25 and bool(surname) and not author
    return {'status':'metadata_conflict' if conflict else 'literal_record_match','title_overlap':round(overlap,3),'first_author_present':author if surname else None}


def collect_sources(chosen):
    sources=[];units=[]
    for group in chosen:
        document=group['document'];run=document['parse_run_id']
        structure=store.get_structure(document['id'],parse_run_id=run)
        if not structure:raise ValueError('source_missing')
        mapping=reading.citation_map(document['id'],run)
        backlinks={reference['id']:reference['backlinks'] for reference in mapping['references']}
        source={'node_id':group['key'],'document_id':document['id'],'parse_run_id':run,'title':document['title'],'version_label':document.get('version_label'),'versions':group['versions'],
                'has_ocr':any((block.get('metadata') or {}).get('extraction')=='rapidocr' for block in structure['blocks'])}
        sources.append(source)
        by_block={block['id']:block for block in structure['blocks']}
        for unit in bibliography_units(structure):
            links=list({link['id']:link for identity in unit['reference_ids'] for link in backlinks.get(identity,[])}.values())
            for link in links:
                metadata=by_block[link['block_id']].get('metadata') or {}
                link.update(extraction=metadata.get('extraction','native'),confidence=metadata.get('confidence'))
            unit.update(source=source,identifiers=identifiers(unit['text']),backlinks=links)
            units.append(unit)
    return sources,units


async def resolve_identifiers(keys,budget):
    index=registry_index(cached_records());wanted=[key for key in dict.fromkeys(keys) if key not in index][:budget]
    warnings=[]
    arxiv=[key.split(':',1)[1] for key in wanted if key.startswith('arxiv:')]
    for start in range(0,len(arxiv),20):
        ids=arxiv[start:start+20]
        try:
            result=literature.parse_arxiv(await literature._fetch('arxiv',{'id_list':','.join(ids),'max_results':len(ids)}))
            accepted=[record for record in result['items'] if root_identifier(record['identifier']) in ids]
            literature_store.save_records(accepted)
        except (httpx.HTTPError,ValueError,TypeError,KeyError,ET.ParseError):warnings.append('部分 arXiv 注册记录请求失败，保留字面标识符并标记待核对。')
    # DOI lookup is deliberately bounded inside the total budget.
    for key in [key for key in wanted if key.startswith('doi:')][:8]:
        try:
            result=literature.parse_crossref(await literature._fetch('crossref',doi=key[4:]),True)
            literature_store.save_records([record for record in result['items'] if record['identifier']==key[4:]])
        except (httpx.HTTPError,ValueError,TypeError,KeyError):warnings.append('部分 DOI 注册记录请求失败，未替换为标题相似论文。')
    return registry_index(cached_records()),list(dict.fromkeys(warnings))


def pagerank(node_ids,edges,alpha=.85,tolerance=1e-10,max_iterations=200):
    """Uniform dangling redistribution; each unique source→target pair has weight one."""
    nodes=list(node_ids);n=len(nodes)
    if not n:return {},0,True
    outgoing={node:set() for node in nodes}
    for edge in edges:
        if edge['source'] in outgoing and edge['target'] in outgoing and edge['source']!=edge['target']:outgoing[edge['source']].add(edge['target'])
    rank={node:1/n for node in nodes}
    for iteration in range(1,max_iterations+1):
        dangling=sum(rank[node] for node in nodes if not outgoing[node])
        new={node:(1-alpha)/n+alpha*dangling/n for node in nodes}
        for node,targets in outgoing.items():
            if targets:
                for target in targets:new[target]+=alpha*rank[node]/len(targets)
        difference=sum(abs(new[node]-rank[node]) for node in nodes);rank=new
        if difference<tolerance:return rank,iteration,True
    return rank,max_iterations,False


def assemble(sources,units,index,topic,max_nodes):
    nodes={source['node_id']:{'id':source['node_id'],'title':source['title'],'kind':'local','identity_status':'local_source','year':None,'authors':[],
                           'document_id':source['document_id'],'parse_run_id':source['parse_run_id'],'metadata_only':False,'landing_url':None} for source in sources}
    for key,node in nodes.items():
        if key in index:
            metadata=index[key];node.update(year=metadata.get('year'),authors=metadata.get('authors',[]),landing_url=metadata['landing_url'],registry_source=metadata.get('source_url'))
    edges={};unresolved=[];truncated=0
    for unit in units:
        if not unit['identifiers']:
            unresolved.append({'id':unit['id'],'source':unit['source'],'text':unit['text'],'sources':unit['sources'],'reason':'没有可唯一解析的 DOI/arXiv 标识符'});continue
        # Two distinct identities in one grouped entry can indicate a bad continuation merge.
        distinct=list(dict.fromkeys(key for key,_ in unit['identifiers']))
        if len(distinct)>1:
            unresolved.append({'id':unit['id'],'source':unit['source'],'text':unit['text'],'sources':unit['sources'],'reason':'同一候选片段含多个标识符，需人工拆分，暂不建边'});continue
        key=distinct[0];raw_id=unit['identifiers'][0][1];metadata=index.get(key)
        if key not in nodes:
            if len(nodes)>=max_nodes:
                truncated+=1
                unresolved.append({'id':unit['id'],'source':unit['source'],'text':unit['text'],'sources':unit['sources'],'reason':'节点预算未展开；原书目保留供核对'})
                continue
            nodes[key]={'id':key,'title':metadata['title'] if metadata else raw_id,'kind':'external','identity_status':'registry_resolved' if metadata else 'identifier_unresolved',
                        'year':metadata.get('year') if metadata else None,'authors':metadata.get('authors',[]) if metadata else [],'metadata_only':True,
                        'landing_url':metadata['landing_url'] if metadata else None,'registry_source':metadata.get('source_url') if metadata else None,'registry_version':metadata.get('identifier') if metadata else None}
        elif metadata:
            nodes[key].update(year=metadata.get('year'),authors=metadata.get('authors',[]),landing_url=metadata['landing_url'],registry_source=metadata.get('source_url'))
        source=unit['source']['node_id']
        if key==source:continue
        alignment=identity_alignment(unit['text'],metadata)
        identity='edge_'+hashlib.sha256((source+'|'+key).encode()).hexdigest()[:20]
        status='identity_conflict' if alignment['status']=='metadata_conflict' else 'identifier_resolved' if metadata or nodes[key]['kind']=='local' else 'identifier_unresolved'
        edge=edges.setdefault(identity,{'id':identity,'source':source,'target':key,'status':status,
                                      'purpose':'unknown','purpose_status':'unclassified','evidence':[]})
        if status=='identity_conflict':edge['status']='identity_conflict'
        edge['evidence'].append({'reference_id':unit['id'],'reference_text':unit['text'],'cited_identifier':raw_id,'source_document_id':unit['source']['document_id'],
                                'parse_run_id':unit['source']['parse_run_id'],'identity_alignment':alignment,'reference_sources':unit['sources'],'body_citations':unit['backlinks']})
    resolved=[edge for edge in edges.values() if edge['status']=='identifier_resolved']
    eligible=[node['id'] for node in nodes.values() if node['kind']=='local' or node['identity_status']=='registry_resolved']
    rank,iterations,converged=pagerank(eligible,resolved)
    for node in nodes.values():
        node.update(in_degree=sum(edge['target']==node['id'] for edge in resolved),out_degree=sum(edge['source']==node['id'] for edge in resolved),pagerank=rank.get(node['id']))
    ranking=sorted([node for node in nodes.values() if node['id'] in rank],key=lambda node:(-node['pagerank'],-node['in_degree'],node['title']))
    shared=[{'node_id':node['id'],'cited_by':[edge['source'] for edge in resolved if edge['target']==node['id']]} for node in ranking if node['in_degree']>=2]
    return {'id':str(uuid.uuid4()),'format_version':'citation-graph-v2','created_at':store.now(),'topic':topic,'sources':sources,'nodes':list(nodes.values()),'edges':list(edges.values()),
            'unresolved_references':unresolved,'metrics':{'scope':'所选文献的书目引用子图；不是全球影响力','direction':'citing_to_cited','eligible_edges':len(resolved),
             'eligible_nodes':len(eligible),'alpha':.85,'iterations':iterations,'converged':converged,'ranking':[node['id'] for node in ranking[:10]],'shared_ancestors':shared},
            'warnings':['边来自 PDF 参考文献中的字面标识符；注册记录核对不等于语义、引用用途或研究质量核验。',
                        '只展开所选版本的直接书目引用。外部节点通常只有元数据，未读取其全文；PageRank 只反映此局部样本。',
                        f'未解析书目候选 {len(unresolved)} 组；因节点预算未展开 {truncated} 组。没有以主题相似度补造引用边。']+
                       (['部分来源含 OCR；字面标识符及数字需核对原图，识别分数不代表事实正确率。'] if any(source.get('has_ocr') for source in sources) else [])}


class GraphRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    document_ids:list[str]=Field(min_length=1,max_length=20)
    selected_versions:dict[str,str]=Field(default_factory=dict)
    topic:str=Field(default='研究方法与局限',min_length=2,max_length=200)
    resolve_metadata:bool=False
    resolution_budget:int=Field(default=60,ge=1,le=100)
    max_nodes:int=Field(default=100,ge=20,le=150)


@router.post('/graphs')
async def build_graph(body:GraphRequest):
    documents=[store.get_document(identity) for identity in dict.fromkeys(body.document_ids)]
    if any(document is None for document in documents):raise HTTPException(404,'文献不存在或已删除')
    if any(document['status'] not in {'ready','partial'} for document in documents):raise HTTPException(409,'请等待文献解析完成')
    try:
        chosen=choose_sources(documents,body.selected_versions)
        sources,units=await run_in_threadpool(collect_sources,chosen)
    except ValueError:raise HTTPException(422,'选定解析版本或来源不可用')
    keys=[key for unit in units for key,_ in unit['identifiers']]
    index,warnings=await resolve_identifiers(keys,body.resolution_budget) if body.resolve_metadata else (registry_index(cached_records()),['本次未请求外部注册源，只使用已缓存的元数据。'])
    result=assemble(sources,units,index,body.topic,body.max_nodes);result['warnings']+=warnings
    try:repo.save(result)
    except ValueError:raise HTTPException(409,'构图期间来源已删除')
    return result


@router.get('/graphs')
def graphs():return {'items':repo.list_graphs()}


@router.get('/graphs/{identity}')
def graph(identity:str):
    payload=repo.get(identity)
    if not payload:raise HTTPException(404,'图谱或来源已不可用')
    if payload.get('format_version')!='citation-graph-v2':payload['warnings'].append('此快照使用早期开发构图规则，建议重新构图后核对。')
    documents={source['document_id']:store.get_document(source['document_id']) for source in payload['sources']}
    if any(document is None for document in documents.values()):raise HTTPException(404,'图谱来源已删除')
    payload['newer_source_runs']=[source['document_id'] for source in payload['sources'] if documents[source['document_id']]['parse_run_id']!=source['parse_run_id']]
    return payload


class PurposeRequest(BaseModel):purpose:str


@router.patch('/graphs/{identity}/edges/{edge_id}')
def label_edge(identity:str,edge_id:str,body:PurposeRequest):
    if body.purpose not in PURPOSES:raise HTTPException(422,'不支持的引用用途')
    try:payload=repo.annotate(identity,edge_id,body.purpose)
    except ValueError:raise HTTPException(409,'来源已删除')
    if not payload:raise HTTPException(404,'图谱或边不存在')
    return payload


def source_evidence(source,block):
    return {'id':'ev_'+block['id'],'document_id':source['document_id'],'parse_run_id':source['parse_run_id'],'title':source['title'],'version_label':source.get('version_label'),
            'block_id':block['id'],'page':block['page'],'bbox':block['bbox'],'text':block['text'],'score':0,'extraction':block.get('metadata',{}).get('extraction','native')}


def outline_payload(payload):
    sections=[];future=[]
    for source in payload['sources']:
        document=store.get_document(source['document_id'])
        if not document:raise HTTPException(404,'图谱来源已删除')
        evidence=qa._retrieve(payload['topic']+' method architecture limitations future work',[{**document,'_retrieval_run':source['parse_run_id']}],3)
        sections.append({'title':source['title'],'source':source,'evidence':evidence,'status':'source_excerpts_only'})
        structure=store.get_structure(source['document_id'],parse_run_id=source['parse_run_id'])
        if not structure:raise HTTPException(404,'图谱来源已删除')
        for block in structure['blocks']:
            if not qa._is_reference(block) and re.search(r'\bfuture work\b|\bwe (?:plan|intend)\b|\bwill (?:investigate|explore)\b|\bremains? to be\b|未来工作|后续研究',re.sub(r'\s+',' ',block['text']),re.I):
                future.append({**source_evidence(source,block),'verification_status':'not_checked'})
    return {'graph_id':payload['id'],'mode':'evidence_outline','topic':payload['topic'],'sections':sections,'future_work':future[:20],
            'shared_ancestors':payload['metrics']['shared_ancestors'],'warnings':['这是来源摘录与写作提纲，不是模型生成的学术综述。元数据摘要不参与原文结论。','未来工作仅提取原文方向，尚未证明有无后续解决方案；检索候选不能证明创新或研究空白。']}


@router.get('/graphs/{identity}/outline')
def outline(identity:str):return outline_payload(graph(identity))


class FollowupRequest(BaseModel):
    block_id:str
    query:str=Field(min_length=4,max_length=300)


def screen_followup(payload,source,items):
    source_node=next(node for node in payload['nodes'] if node.get('document_id')==source['document_id'])
    year=source_node.get('year');kept=[];excluded=[]
    for original in items:
        candidate=dict(original);key=candidate.get('key','')
        candidate_root='arxiv:'+root_identifier(key[6:]) if key.startswith('arxiv:') else key
        if candidate_root==source_node['id'] or norm(candidate.get('title',''))==norm(source['title']):
            excluded.append({**candidate,'screening_reason':'来源本身或同名候选，保留记录供消歧，不算后续证据'});continue
        candidate_year=candidate.get('year')
        if year and candidate_year and candidate_year<year:
            excluded.append({**candidate,'screening_reason':'首次发表年份早于来源，不列为后续候选'});continue
        candidate['temporal_review']='later_year_candidate' if year and candidate_year and candidate_year>year else 'same_year_uncertain' if year and candidate_year==year else 'date_unknown'
        kept.append(candidate)
    return kept,{'source_year':year,'excluded':excluded,'rule':'仅按注册源首次发表年粗筛；同年、无日期及原方向所在修订版的先后顺序仍待核对。'}


@router.post('/graphs/{identity}/followup')
async def followup(identity:str,body:FollowupRequest):
    payload=graph(identity);outline=await run_in_threadpool(outline_payload,payload)
    source=next((item for item in outline['future_work'] if item['block_id']==body.block_id),None)
    if not source:raise HTTPException(422,'请选择当前图谱提取的未来工作原文块')
    candidates=await literature.search(literature.SearchRequest(query=body.query,mode='topic',limit=5,include_disliked=True))
    candidates['items'],screening=screen_followup(payload,source,candidates['items'])
    result={'id':str(uuid.uuid4()),'graph_id':identity,'created_at':store.now(),'source':source,'query':body.query,'status':'candidate_search_only','candidates':candidates,'screening':screening,
            'conclusion':'已尝试检索，返回记录仅为待核验候选。尚未对候选全文与原方向逐项比较，不能认定已解决、未解决或具有创新性。'}
    try:repo.save_followup(identity,result)
    except ValueError:raise HTTPException(409,'检索期间源文献已删除')
    return result


@router.get('/graphs/{identity}/followups')
def followups(identity:str):
    graph(identity)
    return {'items':repo.followups(identity)}


@router.get('/graphs/{identity}/export')
def export(identity:str,format:str=Query(default='json',pattern='^(json|markdown)$')):
    payload=graph(identity)
    if format=='json':return PlainTextResponse(json.dumps(payload,ensure_ascii=False,indent=2),media_type='application/json',headers={'Content-Disposition':'attachment; filename="citation-graph.json"'})
    outline=outline_payload(payload);titles={node['id']:node['title'] for node in payload['nodes']}
    lines=['# 引用网络与综述证据提纲','',payload['metrics']['scope'],'',f"图谱：{identity}",'','## 有原文锚点的引用关系','']
    for edge in payload['edges']:
        lines.append(f"- {titles[edge['source']]} → {titles[edge['target']]}（{edge['status']}；用途：{edge['purpose']}）")
        for evidence in edge['evidence']:
            lines.extend([f"  - 来源 {evidence['source_document_id']} / {evidence['parse_run_id']}；书目页 "+','.join(str(item['page']) for item in evidence['reference_sources']),'> '+evidence['reference_text'].replace('\n','\n> '),''])
    lines+=['## 各篇原文摘录','']
    for section in outline['sections']:
        lines+=['### '+section['title'],'']
        for evidence in section['evidence']:lines += [f"PDF第{evidence['page']}页 / {evidence['parse_run_id']} / {evidence['block_id']}",'> '+evidence['text'].replace('\n','\n> '),'']
    lines+=['## 未来工作（尚未核查后续结论）','']
    for evidence in outline['future_work']:lines += [f"- {evidence['title']} / PDF第{evidence['page']}页 / {evidence['block_id']}",'> '+evidence['text'].replace('\n','\n> '),'']
    lines+=['## 后续候选检索记录（不代表已核验结论）','']
    for check in repo.followups(identity):
        lines += [f"- 查询：{check['query']} / {check['created_at']} / 原方向块 {check['source']['block_id']}",check['conclusion'],'']
        if check.get('screening'):lines += [check['screening']['rule'],'']
        for candidate in check['candidates'].get('items',[]):lines.append(f"  - {candidate['title']} · {candidate.get('landing_url','')}")
        for candidate in check.get('screening',{}).get('excluded',[]):lines.append(f"  - 未列为后续证据：{candidate['title']} · {candidate['screening_reason']}")
    lines+=payload['warnings']+outline['warnings']
    return PlainTextResponse('\n'.join(lines),media_type='text/markdown',headers={'Content-Disposition':'attachment; filename="research-outline.md"'})
