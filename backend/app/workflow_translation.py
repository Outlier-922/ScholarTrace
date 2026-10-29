"""Explicit, immutable paragraph translations inside the workflow's own budget."""
import hashlib
import json

import httpx
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator
from typing import Literal

from . import model_client, research_store, store, translation, document_translation
from .workflow_policy import ModelStepFailure


class Passage(BaseModel):
    model_config = ConfigDict(extra='forbid')
    document_id: str
    parse_run_id: str
    block_id: str
    page: int = Field(ge=1)


class DocumentScope(BaseModel):
    model_config=ConfigDict(extra='forbid')
    document_id:str
    parse_run_id:str
    scope:Literal['document','pages']='document'
    page_start:int|None=Field(default=None,ge=1)
    page_end:int|None=Field(default=None,ge=1)


class TranslationSpec(BaseModel):
    model_config = ConfigDict(extra='forbid')
    passages: list[Passage] = Field(default_factory=list, max_length=4)
    document:DocumentScope|None=None
    target_language: Literal['zh', 'en'] = 'zh'
    glossary: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode='after')
    def explicit_scope(self):
        if bool(self.passages)==bool(self.document):raise ValueError('请选择明确段落或全文/页范围，两种范围不可同时使用')
        return self


class DocumentPending(Exception):
    def __init__(self,result):self.result=result


def prepare_document(spec,documents):
    if not spec or not spec.document:return None
    choice=next((document for document in documents if document['id']==spec.document.document_id),None)
    if not choice or choice['parse_run_id']!=spec.document.parse_run_id:raise HTTPException(409,'全文翻译范围不属于最终选定文献版本')
    body=document_translation.PlanRequest(**spec.document.model_dump(),target_language=spec.target_language,glossary=spec.glossary)
    preview=document_translation.build_plan(body,persist=False)
    return {'request':body.model_dump(),'manifest_digest':preview['manifest_digest'],'estimate':preview['estimate'],'total':preview['total'],'counts':preview['counts'],'source':preview['source']}


def prepare(spec, documents):
    translation._validate_request(translation.TranslationRequest(document_id=documents[0]['id'], target_language=spec.target_language, glossary=spec.glossary))
    choices = {document['id']: document for document in documents}
    result, seen = [], set()
    for passage in spec.passages:
        key = (passage.document_id, passage.parse_run_id, passage.block_id)
        if key in seen:
            raise HTTPException(422, '待译段落重复，请保留一份')
        seen.add(key)
        document = choices.get(passage.document_id)
        if not document or passage.parse_run_id != document['parse_run_id']:
            raise HTTPException(409, '待译段落不属于任务最终选定的文献版本，请重新选择')
        structure = store.get_structure(passage.document_id, page=passage.page, parse_run_id=passage.parse_run_id)
        block = next((item for item in (structure or {}).get('blocks', []) if item['id'] == passage.block_id), None)
        if not block or block['type'] not in {'paragraph', 'text', 'list_item'} or not (len(block['text'].strip()) >= 10 and len(block['text']) <= 12000):
            raise HTTPException(422, '请选择10—12000字符的有效正文段落；图表、公式和书目不作为本步骤译文')
        if any(word in str(block.get('section_path', '')).casefold() for word in ('references', 'bibliography', '参考文献')):
            raise HTTPException(422, '参考文献请使用原文核对，不加入段落翻译')
        result.append({'id': 'ev_' + hashlib.sha256(f"{document['id']}:{block['id']}".encode()).hexdigest()[:20],
                       'document_id': document['id'], 'parse_run_id': passage.parse_run_id, 'block_id': block['id'],
                       'page': block['page'], 'bbox': block['bbox'], 'text': block['text'], 'title': document['title'],
                       'version_label': document.get('version_label'), 'score': 0, 'block_type': block['type'],
                       'section_path': block.get('section_path', []), 'metadata': block.get('metadata', {})})
    return result


def source_block(evidence):
    structure = store.get_structure(evidence['document_id'], page=evidence['page'], parse_run_id=evidence['parse_run_id'])
    block = next((item for item in (structure or {}).get('blocks', []) if item['id'] == evidence['block_id']), None)
    if not block or any(block[field] != evidence[field] for field in ('text', 'page', 'bbox')) or block['type'] != evidence['block_type']:
        raise ValueError('translation_source_changed')
    return block


async def execute(payload, step):
    if frozen:=payload.get('translation_document'):
        job=document_translation.build_plan(document_translation.PlanRequest(**frozen['request']),artifact_id=step['artifact_id'])
        if job['manifest_digest']!=frozen['manifest_digest']:raise ValueError('translation_manifest_changed')
        result={'id':job['id'],'mode':'document_translation','translation_status':job['status'],'counts':job['counts'],'total':job['total'],'totals':job['totals'],
                'document_source':job['source'],'semantic_verified':False,'answer':'全文计划独立按批确认预算；完成后继续编排，不会自动增加模型调用。'}
        if job['status'] not in {'completed','completed_with_gaps'}:raise DocumentPending(result)
        document_translation.source(job)
        return result
    if payload.get('translation_model_identity') != model_client.identity():
        raise ValueError('translation_model_changed')
    evidence = payload['translation_passages'][step['translation_index']]
    document = store.get_document(evidence['document_id'])
    frozen = next(source for source in payload['sources'] if source['document_id'] == evidence['document_id'])
    if not document or document['sha256'] != frozen['sha256']:
        raise ValueError('translation_source_changed')
    block = source_block(evidence)  # Validate before cache access or a paid request.
    spec = payload['translation_spec']
    result = {'id': step['artifact_id'], 'mode': 'source_only', 'evidence': [evidence],
              'translation_status': 'not_translated', 'translation': None,
              'target_language': spec['target_language'], 'glossary': spec['glossary'],
              'semantic_verified': False, 'answer': '保留待译原文，尚未生成译文。'}
    key = translation.cache_key(evidence['document_id'], evidence['parse_run_id'], block, spec['target_language'], spec['glossary'])
    cached = research_store.get_translation(key)
    if cached:
        text, status = cached['translated_text'], 'cached'
    elif not payload['use_model']:
        return result
    else:
        try:
            key, text, status = await translation.translate_block(evidence['document_id'], evidence['parse_run_id'], block, spec['target_language'], spec['glossary'])
        except ValueError as error:
            if str(error) == 'model_budget_exhausted':
                raise  # Keep this step resumable; do not finish a missing translation.
            raise ModelStepFailure(result, error) from error
        except httpx.HTTPError as error:
            raise ModelStepFailure(result, error) from error
    source_block(evidence)
    return {**result, 'mode': 'translation', 'translation_status': status, 'translation': text,
            'cache_key': key, 'model_identity': model_client.identity(), 'prompt_version': translation.PROMPT_VERSION,
            'answer': '逐段译文候选；数字与占位符保护已检查，学术含义仍需核对原文。'}


def markdown(result):
    if result.get('mode')=='document_translation':
        return '\n\n'.join(['# 全文翻译计划',result['document_source']['title'],result['answer'],
            '状态：'+result['translation_status']+'；语义未核验。',json.dumps({'counts':result['counts'],'totals':result['totals'],'source':result['document_source']},ensure_ascii=False,indent=2)])
    evidence = result['evidence'][0]
    return '\n\n'.join(['# 段落翻译候选',
                         f"{evidence['title']} / PDF第{evidence['page']}页 / {evidence['parse_run_id']} / {evidence['block_id']}",
                         '状态：' + result['translation_status'] + '；语义未核验。',
                         '## 原文', evidence['text'], '## 译文', result.get('translation') or '尚未生成译文。',
                         '## 术语表', json.dumps(result.get('glossary', {}), ensure_ascii=False)])
