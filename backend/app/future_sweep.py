"""Bounded discovery and full-text screening of graph future directions."""
import asyncio
from collections import Counter
import math
import re
import time
import uuid

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, model_validator
from starlette.concurrency import run_in_threadpool

from . import claim_checks, graph, insights, insight_store, literature, model_client, qa, store

router = APIRouter(prefix='/api', tags=['future-directions'])
ACTION = re.compile(
    r'\b(?:we (?:plan|intend|aim)|we will (?:investigate|explore)|(?:one|another) (?:is to|challenge is)|'
    r'future work (?:will|should|could|includes)|direction of future work|remains? to be)\b'
    r'|未来工作|后续研究|拟进一步|计划研究', re.I,
)
COMMON = set('apply approach attention future further investigate explore study tasks work plan paper'
             ' our this these such those we more may open another one research can use using based goal'
             ' analysis computer direction exciting figure other overall promising properties results seem'
             ' challenge continue methods'.split())


class SweepRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    limit: int = Field(default=3, ge=1, le=5)
    use_model: bool = True
    discover_online: bool = False
    import_open_pdfs: bool = False

    @model_validator(mode='after')
    def check_permissions(self):
        if self.import_open_pdfs and not self.discover_online:
            raise ValueError('自动入库需要明确允许联网发现')
        return self


def directions(outline):
    selected = []
    for evidence in outline['future_work']:
        printed = evidence['text']
        parts = re.split(r'(?<=[.!?。！？])\s+', printed)
        matches = [part.strip() for part in parts if ACTION.search(re.sub(r'\s+', ' ', part)) and len(part.strip()) >= 25]
        if not matches and len(printed.strip()) >= 25:
            matches = [printed.strip()]
        for text in matches:
            text = text[:900]
            words = set(qa._tokens(text)) - COMMON
            if len(words) < 3:
                continue
            if any(words == seen or len(words & seen) >= 6 and len(words & seen) / len(words | seen) >= .88
                   for _, _, seen in selected):
                continue
            selected.append((evidence, text, words))
    return selected


def library_candidates(pinned):
    available = [doc for doc in store.list_documents() if doc['status'] in {'ready','partial'}]
    defaults = {doc['work_id']:doc['id'] for doc in available if doc.get('is_work_default')}
    documents = sorted(available, key=lambda doc: doc['id'] not in pinned)
    rows, seen = [], set()
    for document in documents:
        if len(rows) >= 80:
            break
        key = graph.doc_key(document)
        if key in seen or (document['id'] not in pinned and defaults.get(document['work_id'], document['id']) != document['id']):
            continue
        run = pinned.get(document['id'], document['parse_run_id'])
        structure = store.get_structure(document['id'], page=1, parse_run_id=run)
        if not structure:
            continue
        seen.add(key)
        body = ' '.join(block['text'] for block in (structure or {}).get('blocks', [])
                        if block['type'] in {'abstract','paragraph'} and len(block['text']) >= 30)[:2200]
        title = set(qa._tokens(document['title']))
        rows.append(({**document, 'parse_run_id':run}, key, title, set(qa._tokens(body))))
    return rows


def local_ranking(rows, original_key, words, direction):
    frequency = Counter(term for _, _, title, body in rows for term in words & (title | body))
    prior = insights.version_date(direction)
    ranked = []
    for doc, key, title, body in rows:
        if key == original_key or doc['work_id'] == direction['work_id']:
            continue
        recent = insights.chronology(prior, insights.version_date(doc))
        if recent in {'not_later','later_revision_only'}:
            continue
        overlap = words & (title | body)
        if len(overlap) < 2:
            continue
        score = sum(math.log1p(len(rows) / max(1, frequency[word])) * (2 if word in title else 1) for word in overlap)
        ranked.append((score, doc))
    return [doc for _, doc in sorted(ranked, key=lambda item:(-item[0],item[1]['id']))[:3]]


async def find_online(payload, direction, text, words):
    # The arXiv topic endpoint requires every term. A short focused query
    # avoids intersecting introductory wording and figure identifiers.
    terms = list(dict.fromkeys(term for term in qa._tokens(text, query=True) if term in words and not term.isdigit()))
    names = {token.casefold() for token in re.findall(r'\b(?:[A-Z][a-z]+[A-Z]\w*|[A-Z]{2,8}\d*)\b', text)}
    terms.sort(key=lambda term:term not in names)
    keyword_query = ' '.join(terms[:4])
    if len(keyword_query) < 4:
        return None, []
    result = await literature.search(literature.SearchRequest(
        query=keyword_query, provider='arxiv', limit=5, mode='topic', include_disliked=True,
    ))
    kept, _ = graph.screen_followup(payload,direction,result['items'])
    return result, kept


async def get_pdf(candidate, search_id, request):
    from . import literature_handoff
    for document in literature_handoff.candidates(candidate):
        if document['status'] in {'ready','partial'}:
            return document
    receipt = await literature.fetch_paper(
        literature_handoff.Context(key=candidate['key'], search_id=search_id), request,
        identity_basis='registered_version_download',
    )
    identity = receipt['document']['id']
    while True:
        item = await run_in_threadpool(store.get_document, identity)
        if not item or item['status'] == 'failed':
            return None
        if item['status'] in {'ready','partial'}:
            return item
        await asyncio.sleep(.3)


@router.post('/graphs/{identity}/future-sweep')
async def sweep(identity: str, body: SweepRequest, request: Request):
    payload = graph.graph(identity)
    outline = await run_in_threadpool(graph.outline_payload, payload)
    available = directions(outline)
    pinned = {source['document_id']:source['parse_run_id'] for source in payload['sources']}
    local = await run_in_threadpool(library_candidates, pinned)
    pinned.update({doc['id']:doc['parse_run_id'] for doc, _, _, _ in local})
    usage = model_client.CallBudget(body.limit * 2, body.limit * 4200)
    ready = body.use_model and model_client.configuration()['ready']
    directions_report, evidence, sources = [], {}, {}
    downloads = 0
    deadline = time.monotonic() + 110

    def window(seconds):
        return asyncio.timeout(max(0, min(seconds, deadline - time.monotonic())))

    for direction, text, words in available[:body.limit]:
        if time.monotonic() >= deadline:
            break
        evidence[direction['id']] = direction
        original = store.get_document(direction['document_id'])
        if not original:
            raise HTTPException(409, '方向原文已删除。')
        row = {'source':direction, 'direction':text, 'status':'retained', 'issues':[],
               'candidates':[], 'assessment_id':None, 'check_id':None}
        candidates = await run_in_threadpool(local_ranking, local, graph.doc_key(original), words, original)
        if body.discover_online:
            try:
                async with window(20):
                    search, online = await find_online(payload, direction, text, words)
                row['search'] = {'query':search['query'], 'id':search['search_id'], 'providers':search['providers'],
                                 'items':[{k:item.get(k) for k in ('key','title','year','landing_url')} for item in online]} if search else None
                downloadable = next((candidate for candidate in online if candidate.get('pdf_url')), None)
                if body.import_open_pdfs and search and downloadable and downloads < 2:
                    downloads += 1
                    async with window(30):
                        found = await get_pdf(downloadable, search['search_id'], request)
                    if found and graph.doc_key(found) != graph.doc_key(original) and found['work_id'] != original['work_id']:
                        found = {**found, 'parse_run_id':pinned.setdefault(found['id'],found['parse_run_id'])}
                        candidates = [found, *[doc for doc in candidates if graph.doc_key(doc) != graph.doc_key(found)]][:3]
            except (TimeoutError, HTTPException, ValueError):
                row['issues'].append('联网搜索或全文读取未完成，继续用现有全文核对。')
        row['candidates'] = [{k:doc[k] for k in ('id','title','parse_run_id')} for doc in candidates]
        if not candidates:
            row['issues'].append('没有匹配且可核对的本地全文。')
        elif not ready:
            row['issues'].append('当前只显示原文方向及本地候选。')
        else:
            assessment = None
            try:
                async with window(38):
                    with model_client.metered(usage):
                        assessment = await insights.assess_direction(payload, direction, insights.FutureRequest(
                            block_id=direction['block_id'], direction_text=text,
                            candidate_document_ids=[doc['id'] for doc in candidates],
                            parse_runs={doc['id']:doc['parse_run_id'] for doc in candidates},
                        ))
                        row['assessment_id'] = assessment['id']
                        for item in assessment['sources']:
                            sources.setdefault(item['document_id'], item)
                        for item in assessment['evidence']:
                            evidence.setdefault(item['id'], item)
                        eligible = {f'a{index}' for index,item in enumerate(assessment['assessments'])
                                    if item['bucket'] == 'provisionally_addressed'}
                        if eligible:
                            check = await claim_checks.execute(assessment['id'])
                            row['check_id'] = check['id']
                            if any(item['claim_id'] in eligible and item['status'] == 'supported'
                                   for item in check['checks']):
                                row['status'] = 'addressed_candidate'
            except (TimeoutError, HTTPException, ValueError):
                row['issues'].append('方向核对未完成，仍保留原文方向。')
        directions_report.append(row)
    extras = list(sources.values())
    return insights.persist({
        'id':str(uuid.uuid4()), 'graph_id':identity, 'kind':'future_sweep','topic':payload['topic'],
        'created_at':store.now(), 'sources':insights.snapshots(payload,extras), 'evidence':list(evidence.values()),
        'directions':directions_report, 'remaining':sum(row['status']=='retained' for row in directions_report),
        'unprocessed':len(available)-len(directions_report), 'usage':usage.snapshot(), 'fulltext_attempts':downloads,
        'semantic_verified':False,
    })


@router.get('/graphs/{identity}/future-sweeps')
def history(identity: str):
    graph.graph(identity)
    return {'items':[{k:item[k] for k in ('id','created_at','topic','remaining','unprocessed')}
                     for item in insight_store.history(identity,'future_sweep')]}
