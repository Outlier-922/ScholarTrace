"""Explicit table regions, immutable native/OCR candidates and manual edits."""
import asyncio
import csv
import hashlib
import io
import json
import math
import re
import uuid
from pathlib import Path
from typing import Literal
from fastapi import APIRouter,HTTPException,Query,Request
from fastapi.responses import FileResponse,PlainTextResponse,JSONResponse,Response
from pydantic import BaseModel,ConfigDict,Field
from . import ocr,store,table_refine_native as native,table_refine_scan as scan,table_refine_store as repo
from .formulas import page_input,render_region

router=APIRouter(prefix='/api',tags=['table-refinement'])


class SourceRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    document_id:str=Field(min_length=1,max_length=100)
    parse_run_id:str=Field(min_length=1,max_length=100)
    page:int=Field(ge=1,strict=True)
    bbox:list[float]=Field(min_length=4,max_length=4)
    header_bottom:float
    anchor_block_id:str|None=Field(default=None,max_length=100)
    column_cuts:list[float]|None=Field(default=None,min_length=1,max_length=39)
    method:Literal['native','scan_ocr']='native'


class RevisionRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    labels:list[str]=Field(min_length=2,max_length=40)
    rows:list[list[str|None]]=Field(min_length=1,max_length=150)
    note:str=Field(min_length=1,max_length=500)
    parent_id:str|None=Field(default=None,max_length=100)


def require(identity,kind=None):
    value=repo.get(identity)
    if not value:raise HTTPException(404,'表格记录或原文来源不可用。')
    if kind and value['kind']!=kind:raise HTTPException(422,'表格记录类型不符。')
    return value


@router.get('/documents/{identity}/table-options')
def options(identity:str,page:int=Query(ge=1),parse_run_id:str=Query(min_length=1,max_length=100)):
    structure=store.get_structure(identity,page=page,parse_run_id=parse_run_id)
    if not structure:raise HTTPException(404,'解析来源不可用。')
    result=[]
    for b in structure['blocks']:
        if b['type']!='table':continue
        meta=b.get('metadata',{});box=list(b['bbox']);header=meta.get('header',{})
        if header.get('external') and header.get('bbox'):
            h=header['bbox'];box=[min(box[0],h[0]),min(box[1],h[1]),max(box[2],h[2]),max(box[3],h[3])];end=h[3]
        else:end=max((c['bbox'][3] for c in meta.get('cells',[]) if c['row']==0 and c.get('bbox')),default=box[1]+(box[3]-box[1])*.15)
        result.append({'block_id':b['id'],'bbox':box,'header_bottom':end,'row_count':meta.get('row_count'),'col_count':meta.get('col_count')})
    return {'items':result,'notice':'原候选可能合并了多行/多列；也可手动输入同页区域及表头下界。'}


@router.post('/table-regions')
async def create(body:SourceRequest,request:Request):
    doc=store.get_internal_document(body.document_id)
    if not doc:raise HTTPException(404,'原文不存在。')
    structure=store.get_structure(doc['id'],page=body.page,parse_run_id=body.parse_run_id)
    page=next((p for p in (structure or {}).get('pages',[]) if p['number']==body.page),None)
    if not page:raise HTTPException(422,'指定解析版本/页码不存在。')
    box=body.bbox;values=box+[body.header_bottom]+(body.column_cuts or [])
    if not all(math.isfinite(v) and 0<=v<=1 for v in values) or box[2]-box[0]<.02 or not box[1]<body.header_bottom<box[3]:raise HTTPException(422,'请检查区域、表头下界和列分界百分比。')
    cuts=[box[0]]+body.column_cuts+[box[2]] if body.column_cuts else None
    if cuts and any(b-a<.002 for a,b in zip(cuts,cuts[1:])):raise HTTPException(422,'列分界必须从左到右且位于区域内。')
    anchor=next((b for b in structure['blocks'] if b['id']==body.anchor_block_id and b['type']=='table'),None)
    if body.anchor_block_id and not anchor:raise HTTPException(422,'原候选不属于指定页面/解析版本。')
    path=Path(doc['file_path']).resolve()
    if not path.is_relative_to((store.data_dir()/'uploads').resolve()):raise HTTPException(404,'原PDF不可用。')
    scanned=body.method=='scan_ocr'
    algorithm=scan.VERSION if scanned else native.VERSION
    key=hashlib.sha256(json.dumps([algorithm,doc['sha256'],body.model_dump(exclude={'method'})],sort_keys=True).encode()).hexdigest()
    for old in repo.history(doc['id'],'source'):
        if old['source_key']==key:return old
    try:
        extractor,args=(scan.extract,(cuts,)) if scanned else (native.extract,((anchor or {}).get('metadata',{}),cuts))
        result,png=await asyncio.get_running_loop().run_in_executor(request.app.state.parse_executor,extractor,str(path),doc['sha256'],body.page,box,body.header_bottom,*args)
    except ocr.OCRUnavailable:raise HTTPException(503,'本机 OCR 尚未准备。请在作品运行环境准备 OCR 后重试。') from None
    except (ValueError,RuntimeError,OSError,IndexError,ImportError):
        if scanned:raise HTTPException(422,'未能从所选区域重建扫描表格。请框住完整表头与数据，检查表头下缘；自动分列不清时可手填列界。旋转页面暂不支持。') from None
        raise HTTPException(422,'区域重建失败：请检查表头、列界与范围；旋转页或过大表格暂不支持。') from None
    identity=str(uuid.uuid4())
    value={'id':identity,'kind':'source','created_at':store.now(),'source_key':key,'document_id':doc['id'],'sha256':doc['sha256'],'parse_run_id':body.parse_run_id,'page':body.page,'page_geometry':{k:page[k] for k in ('width','height','rotation','cropbox')},'anchor':anchor,'bbox':box,'header_bottom':body.header_bottom,'requested_column_cuts':body.column_cuts,'algorithm':algorithm,'method':body.method,'semantic_verified':False,**result}
    try:repo.image_path(identity).write_bytes(png);repo.save(value)
    except ValueError:
        repo.image_path(identity).unlink(missing_ok=True)
        raise HTTPException(409,'读取期间来源已变化，未保存。') from None
    return value


@router.get('/documents/{identity}/table-page-image')
async def table_page_image(identity:str,request:Request,page:int=Query(ge=1),parse_run_id:str=Query(min_length=1,max_length=100)):
    doc,_,path=page_input(identity,page,parse_run_id)
    png=await render_region(request,doc,path,page)
    return Response(png,media_type='image/png',headers={'Cache-Control':'no-store'})


@router.get('/documents/{identity}/table-regions')
def sources(identity:str,page:int=Query(ge=1),parse_run_id:str=Query(min_length=1,max_length=100)):
    if not store.get_structure(identity,page=page,parse_run_id=parse_run_id):raise HTTPException(404,'来源不可用。')
    return {'items':[{'id':v['id'],'created_at':v['created_at']} for v in repo.history(identity,'source') if v['page']==page and v['parse_run_id']==parse_run_id]}


@router.get('/table-regions/{identity}')
def detail(identity:str):return require(identity,'source')


@router.get('/table-regions/{identity}/image')
def image(identity:str):
    require(identity,'source');path=repo.image_path(identity)
    if not path.is_file():raise HTTPException(404,'原图预览不可用。')
    return FileResponse(path,media_type='image/png',headers={'Cache-Control':'no-store'})


@router.get('/table-regions/{identity}/revisions')
def history(identity:str):
    value=require(identity,'source')
    return {'items':[r for r in repo.history(value['document_id'],'revision') if r['source_id']==identity]}


@router.post('/table-regions/{identity}/revisions')
def revise(identity:str,body:RevisionRequest):
    source=require(identity,'source');grid=source['grid'];rows=body.rows;columns=grid['columns']
    if not body.note.strip():raise HTTPException(422,'请填写修订说明。')
    if not grid['rows'] or len(rows)!=len(grid['rows']) or len(body.labels)!=len(columns) or any(len(r)!=len(columns) for r in rows) or any(not s.strip() or len(s)>250 for s in body.labels):raise HTTPException(422,'修订须保留原候选行列尺寸，列名不得为空。')
    if sum(len(v or '') for r in rows for v in r)>100000 or any(len(v or '')>1000 for r in rows for v in r):raise HTTPException(422,'单元格或表格文字过长。')
    for c in grid['cells']:
        if c['covered_by'] and rows[c['row']][c['column']] is not None:raise HTTPException(422,'跨行覆盖的位置须保留为空，不能自动传播数值。')
    if body.parent_id and require(body.parent_id,'revision')['source_id']!=identity:raise HTTPException(422,'父版本不是当前来源。')
    value={k:source[k] for k in ('document_id','sha256','parse_run_id','page','page_geometry','anchor','bbox')}
    changed=[{'row':i,'column':j,'before':grid['rows'][i][j],'after':v} for i,row in enumerate(rows) for j,v in enumerate(row) if v!=grid['rows'][i][j]]
    value.update(id=str(uuid.uuid4()),kind='revision',source_id=identity,created_at=store.now(),labels=body.labels,rows=rows,note=body.note,parent_id=body.parent_id,changed_cells=changed,origin='user_edited',semantic_verified=False)
    try:repo.save(value)
    except ValueError:raise HTTPException(409,'保存期间来源失效，未保存。') from None
    return value


def safe_csv(value):
    if value is None:return ''
    trimmed=value.lstrip()
    if trimmed.startswith(('=','+','@','-')) and not re.fullmatch(r'-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?',trimmed):return "'"+value
    return value


@router.get('/table-regions/{identity}/export')
def export(identity:str,format:str=Query(pattern='^(json|csv)$'),revision_id:str|None=None):
    source=require(identity,'source');revision=require(revision_id,'revision') if revision_id else None
    if revision and revision['source_id']!=identity:raise HTTPException(422,'修订不属于当前来源。')
    if format=='json':return JSONResponse({'source':source,'revision':revision,'notice':'未独立核验；人工改值不因保留原单元格坐标而成为原文证据。'},headers={'Content-Disposition':'attachment; filename="source-bound-table.json"'})
    rows=revision['rows'] if revision else source['grid']['rows'];labels=revision['labels'] if revision else [c['label'] for c in source['grid']['columns']]
    output=io.StringIO(newline='');writer=csv.writer(output);writer.writerow([safe_csv(v) for v in labels]);writer.writerows([[safe_csv(v) for v in row] for row in rows])
    return PlainTextResponse('\ufeff'+output.getvalue(),media_type='text/csv',headers={'Content-Disposition':'attachment; filename="unverified-table.csv"'})
