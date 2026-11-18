"""Opt-in native table cells for QA; manual revisions are a separate audit lane."""
import hashlib
import json
import math
from collections import Counter
from fastapi import HTTPException
from pydantic import BaseModel,ConfigDict,Field,StrictInt
from . import store,table_refine_store as repo
from .table_refine_scan import VERSION as SCAN_ALGORITHM

VERSION='pinned-native-table-qa-v1'


def is_scan(source):
    return source.get('method') == 'scan_ocr' or source.get('algorithm') in {'scan-ocr-grid-v1', SCAN_ALGORITHM}


class Selection(BaseModel):
    model_config=ConfigDict(extra='forbid')
    source_id:str=Field(min_length=36,max_length=36)
    revision_id:str|None=Field(default=None,min_length=36,max_length=36)
    row_indices:list[StrictInt]|None=Field(default=None,min_length=1,max_length=12)


def resolve(selections,documents,parse_runs):
    ids={d['id'] for d in documents};runs=dict(parse_runs);resolved=[];seen=set()
    for selection in selections:
        if selection.source_id in seen:raise HTTPException(422,'同一表格来源只选择一个版本。')
        seen.add(selection.source_id)
        source=repo.get(selection.source_id)
        if not source or source['kind']!='source':raise HTTPException(404,'所选表格来源不可用。')
        if is_scan(source):raise HTTPException(422,'扫描表格候选仅供原图核对，暂不能加入问答。')
        identity=source['document_id'];run=source['parse_run_id']
        if identity not in ids:raise HTTPException(422,'表格不属于本次所选文献。')
        if identity in runs and runs[identity]!=run:raise HTTPException(422,'同一文献的表格和问答必须使用同一个解析版本。')
        runs[identity]=run
        revision=repo.get(selection.revision_id) if selection.revision_id else None
        if selection.revision_id and (not revision or revision['kind']!='revision' or revision['source_id']!=source['id']):raise HTTPException(422,'手工版本不属于所选表格来源，或已失效。')
        rows=selection.row_indices
        if rows is not None and (len(set(rows))!=len(rows) or any(r<0 or r>=len(source['grid']['rows']) for r in rows)):raise HTTPException(422,'所选文字行不属于当前表格。')
        resolved.append((source,revision,rows))
    return resolved,runs


def digest(value):return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True).encode()).hexdigest()


def row_context(source,row):
    grid=source['grid'];columns=len(grid['columns']);parts=[];anchors=[]
    # The first two textual columns often identify a model and training condition.
    # A vertical group marker is contextualized through its original spanning cell,
    # never written into the covered data cells as an inferred value.
    for col in range(min(2,columns)):
        cell=grid['cells'][row*columns+col]
        if cell['covered_by']:
            i,j=cell['covered_by'];cell=grid['cells'][i*columns+j]
        if cell.get('col_span', 1) > 1:
            continue
        value=cell['text']
        if value and any(c.isalpha() for c in value):
            parts.append(grid['columns'][col]['label']+' = '+value)
            anchors.append({'role':'row_context','row':cell['row'],'column':cell['column'],'bbox':cell['bbox'],'word_ids':cell['word_ids']})
    return '; '.join(parts) or '未识别行标识（只使用所选格子的原生值）',anchors


def cell_evidence(source,document,row,column,*,allow_shared=False):
    if is_scan(source):return None
    grid=source['grid'];count=len(grid['columns']);cell=grid['cells'][row*count+column]
    if cell['covered_by'] or cell['text'] in (None,'') or not cell['word_ids']:return None
    span = cell.get('col_span', 1)
    header = grid['columns'][column]
    if span > 1:
        if not allow_shared:return None
        parents = [item for item in grid.get('header_spans', [])
                   if item['first_column'] == column and item['col_span'] == span and item['word_ids']]
        if len(parents) != 1:return None
        header = {**parents[0], 'label':parents[0]['text']}
    label=header['label'];context,anchors=row_context(source,row)
    scope = '共享表头' if span > 1 else '列'
    text=f"原生表格候选（未核验）。行上下文：{context}。{scope}：{label}。单元格值：{cell['text']}。"
    if span > 1:
        text += f"原表横跨 {span} 列，未给出各分列的独立值。"
    if len(text)>3000:return None
    content={'text':text,'bbox':cell['bbox'],'word_ids':cell['word_ids'],'context_anchors':anchors,'header_word_ids':header['word_ids']}
    if span > 1:
        content['col_span'] = span
    key=digest(content)
    return {'id':'ev_tbl_'+digest([source['id'],row,column,key])[:20],
        'document_id':source['document_id'],'title':document['title'],'version_label':document.get('version_label'),
        'parse_run_id':source['parse_run_id'],'page':source['page'],'block_id':f"tbl_{source['id']}_{row}_{column}",
        'bbox':cell['bbox'],'text':text,'score':0.,'extraction':'native_table_grid','block_type':'table_cell',
        'source_kind':'table_cell','section_path':[],'metadata':{'semantic_verified':False},
        'table_cell':{'source_id':source['id'],'revision_id':None,'row':row,'column':column,'column_label':label,
                      'value':cell['text'],'origin':'native_candidate','content_sha256':key,**{k:v for k,v in content.items() if k!='text'}}}


def rank(question,items,tokenize,limit):
    query=set(tokenize(question,query=True))
    if not query or not items:return []
    terms=[tokenize(item['text']) for item in items];frequency=Counter(t for row in terms for t in set(row));average=sum(map(len,terms))/max(1,len(terms));scored=[]
    for item,words in zip(items,terms):
        counts=Counter(words);score=0.
        for term in query:
            f=counts[term]
            if f:score+=math.log(1+(len(items)-frequency[term]+.5)/(frequency[term]+.5))*f*2.2/(f+1.2*(.25+.75*len(words)/max(average,1)))
        # Column matching is a stronger hint than the repeated row identifier.
        column_terms=set(tokenize(item.get('table_cell',{}).get('column_label','')))
        score+=sum(.8 for term in query if term in column_terms)
        if score>0:scored.append({**item,'score':score})
    return sorted(scored,key=lambda v:(-v['score'],v['id']))[:limit]


def draft_rows(source,revision,document):
    if not revision:return []
    grid=source['grid'];items=[];columns=len(grid['columns'])
    header_changes=[{'column':j,'before':c['label'],'after':revision['labels'][j]} for j,c in enumerate(grid['columns']) if revision['labels'][j]!=c['label']]
    for row,values in enumerate(revision['rows']):
        changes=[{'column':col,'label':revision['labels'][col],'before':grid['rows'][row][col],'after':value,
                  'bbox':grid['cells'][row*columns+col]['bbox']} for col,value in enumerate(values) if value!=grid['rows'][row][col]]
        if not changes and not header_changes:continue
        context,_=row_context(source,row)
        # Draft text is retrieved and displayed separately; it is never a model source.
        manual='; '.join(revision['labels'][col]+' = '+value for col,value in enumerate(values) if value is not None)
        text=f'手工修订（未核验）：{manual}。原生行上下文：{context}。'
        if len(text)>4000:text=text[:4000]+'（其余见完整修订）'
        bbox=[source['bbox'][0],grid['row_edges'][row],source['bbox'][2],grid['row_edges'][row+1]]
        value={'id':'tdraft_'+digest([revision['id'],row])[:20],'document_id':source['document_id'],'title':document['title'],
               'version_label':document.get('version_label'),'parse_run_id':source['parse_run_id'],'page':source['page'],
               'block_id':f"draft_{revision['id']}_{row}",'bbox':bbox,'text':text,'score':0.,'source_kind':'table_draft',
               'table_draft':{'source_id':source['id'],'revision_id':revision['id'],'row':row,'changes':changes[:8],
                              'changes_truncated':len(changes)>8,'header_changes':header_changes,'note':revision['note'],
                              'semantic_verified':False,'sent_to_model':False}}
        value['table_draft']['content_sha256']=digest({k:v for k,v in value.items() if k not in {'title','version_label'}})
        items.append(value)
    return items


def retrieve(question,resolved,documents,limit,tokenize):
    by_id={d['id']:d for d in documents};cells=[];drafts=[]
    for source,revision,selected_rows in resolved:
        grid=source['grid'];document=by_id[source['document_id']]
        for row in selected_rows if selected_rows is not None else range(len(grid['rows'])):
            for col in range(len(grid['columns'])):
                cell=cell_evidence(source,document,row,col,allow_shared=True)
                if cell:cells.append(cell)
        drafts.extend(v for v in draft_rows(source,revision,document) if selected_rows is None or v['table_draft']['row'] in selected_rows)
    # Deduplicate source cells even when an upstream consumer sends repeated refs.
    cells=list({v['id']:v for v in cells}.values())
    return rank(question,cells,tokenize,limit),rank(question,drafts,tokenize,min(limit,6)),{
        'mode':'table_bm25','used':False,'table_candidates':len(cells),'table_sources':len(resolved),
        'table_algorithm':VERSION,'manual_rows_sent_to_model':0,'semantic_verified':False}


def valid_evidence(evidence):
    kind=evidence.get('source_kind')
    if kind not in {'table_cell','table_draft'}:return True
    reference=evidence.get('table_cell') if kind=='table_cell' else evidence.get('table_draft')
    if not reference:return False
    source=repo.get(reference.get('source_id',''));doc=store.get_document(evidence['document_id'])
    if not source or source.get('kind')!='source' or not doc or source['document_id']!=doc['id']:return False
    try:
        if kind=='table_cell':fresh=cell_evidence(source,doc,reference['row'],reference['column'],allow_shared=True)
        else:
            revision=repo.get(reference.get('revision_id',''))
            if not revision or revision.get('kind')!='revision' or revision['source_id']!=source['id']:return False
            fresh=next((v for v in draft_rows(source,revision,doc) if v['id']==evidence['id']),None)
        return bool(fresh) and all(fresh.get(k)==evidence.get(k) for k in ['id','document_id','parse_run_id','page','bbox','text']) and fresh[kind]==reference
    except (IndexError,KeyError,TypeError):return False
