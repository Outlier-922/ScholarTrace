import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from 'react';
import { Download, Languages, LoaderCircle, Target } from 'lucide-react';
import { postJSON, request } from './api';
import type { DocumentBlock, DocumentStructure, ResearchDocument } from './types';
import './document-translation.css';

interface Unit {block_id:string;page:number;status:string;source:DocumentBlock;translation:string|null;reason?:string;source_sha256?:string;reviewed_translation?:string;edits?:{version:number;reason:string;origin:string}[];}
export interface DocumentTranslationJob {id:string;revision:number;document_id:string;source:{parse_run_id:string};status:string;total:number;page_count:number;pages_without_blocks:number[];counts:Record<string,number>;notice:string;items?:Unit[];spec:{scope:string;target_language:string;page_start?:number;page_end?:number};estimate:{cache_hits:number;uncached_requests:number;output_token_reserve:number};totals:{calls:number;reserved_output_tokens:number;input_tokens:number;output_tokens:number;batches:number};batch:{processed:number;usage:{calls:number};limits:{max_model_calls:number}}|null;}
interface LegacyJob {id:string;document_id:string;parse_run_id:string;target_language:string;status:string;total:number;completed:number;items:Unit[];}
export interface TranslationHandle { scrollToBlock: (id:string,ratio:number)=>void; }
const activeStates=['queued','running','pause_requested','cancel_requested'];
const labels:Record<string,string>={planned:'计划待确认',queued:'排队中',running:'本批处理中',pause_requested:'等待段落结束后暂停',cancel_requested:'等待段落结束后取消',paused:'已暂停',budget_exhausted:'本批到达预算',failed:'已停止，需明确重试',completed:'所选范围已处理',completed_with_gaps:'处理结束，有未支持块',cancelled:'已取消，保留进度'};

function TranslationEdit({job,unit,onSaved}:{job:DocumentTranslationJob;unit:Unit;onSaved:(value:DocumentTranslationJob)=>void}){
  const [text,setText]=useState(unit.reviewed_translation||unit.translation||'');const [reason,setReason]=useState('');const [busy,setBusy]=useState(false);const [error,setError]=useState('');
  useEffect(()=>{setText(unit.reviewed_translation||unit.translation||'');setReason('');setError('');},[job.id,unit.reviewed_translation,unit.translation]);
  async function save(){setBusy(true);setError('');try{await postJSON(`/document-translations/${job.id}/edits`,{expected_revision:job.revision,block_id:unit.block_id,source_sha256:unit.source_sha256,text,reason});onSaved(await request<DocumentTranslationJob>(`/document-translations/${job.id}?page=${unit.page}`));}catch(cause){setError(String(cause));}finally{setBusy(false);}}
  return <details className="document-translation-editor"><summary>校订译文（保留原候选）</summary><p>数字、公式、标识与单位需保持。校订另存记录，不覆盖模型原候选，也不自动标为语义正确。</p><textarea aria-label={'校订译文 '+unit.block_id} value={text} onChange={event=>setText(event.target.value)}/><input aria-label={'校订理由 '+unit.block_id} value={reason} maxLength={2000} onChange={event=>setReason(event.target.value)} placeholder="说明依据原文修改了什么"/><button disabled={busy||!reason.trim()||!text.trim()} onClick={()=>void save()}>保存校订记录</button>{error&&<p role="alert">{error}</p>}</details>;
}

const TranslationPane=forwardRef<TranslationHandle,{document:ResearchDocument;structure:DocumentStructure|null;page:number;selectedBlock:string|null;modelConfigured:boolean;linked:boolean;onAnchor:(id:string,ratio:number)=>void;onSource:(block:DocumentBlock)=>void;initialJobId?:string;onUpdate?:(job:DocumentTranslationJob)=>void}>(function TranslationPane({document,structure,page,selectedBlock,modelConfigured,linked,onAnchor,onSource,initialJobId,onUpdate},ref){
  const [job,setJob]=useState<DocumentTranslationJob|null>(null);
  const [history,setHistory]=useState<DocumentTranslationJob[]>([]);
  const [legacyHistory,setLegacyHistory]=useState<LegacyJob[]>([]);
  const [legacy,setLegacy]=useState<LegacyJob|null>(null);
  const [language,setLanguage]=useState('zh');const [terms,setTerms]=useState('');
  const [pageStart,setPageStart]=useState(1);const [pageEnd,setPageEnd]=useState(document.page_count);
  const [allowModel,setAllowModel]=useState(false);
  const [limits,setLimits]=useState({max_model_calls:3,max_output_tokens:10500,max_units:50,max_active_seconds:180});
  const [busy,setBusy]=useState(false);const [error,setError]=useState('');
  const scrollRef=useRef<HTMLDivElement>(null);const suppressUntil=useRef(0);
  function update(value:DocumentTranslationJob){setJob(value);onUpdate?.(value);}
  useEffect(()=>{
    setJob(null);setLegacy(null);setError('');setHistory([]);setLegacyHistory([]);setPageStart(1);setPageEnd(document.page_count);setAllowModel(false);
    if(!structure?.parse_run_id)return;
    const controller=new AbortController();const signal=controller.signal;
    void Promise.all([request<{items:DocumentTranslationJob[]}>(`/document-translations?document_id=${document.id}&parse_run_id=${encodeURIComponent(structure.parse_run_id)}`,{signal}),request<{items:LegacyJob[]}>(`/documents/${document.id}/translations?parse_run_id=${encodeURIComponent(structure.parse_run_id)}`,{signal})]).then(async([current,old])=>{
      if(signal.aborted)return;setHistory(current.items);setLegacyHistory(old.items);
      const id=initialJobId||current.items[0]?.id;
      if(id){const value=await request<DocumentTranslationJob>(`/document-translations/${id}?page=${page}`,{signal});if(!signal.aborted)update(value);}
      else if(old.items[0]){const value=await request<LegacyJob>(`/translations/${old.items[0].id}?page=${page}`,{signal});if(!signal.aborted)setLegacy(value);}
    }).catch(cause=>{if(!signal.aborted)setError(String(cause));});
    return()=>controller.abort();
  },[document.id,structure?.parse_run_id,initialJobId]);
  useEffect(()=>{
    if(!job?.id)return;const controller=new AbortController();
    const load=()=>void request<DocumentTranslationJob>(`/document-translations/${job.id}?page=${page}`,{signal:controller.signal}).then(value=>{if(!controller.signal.aborted)update(value);}).catch(cause=>{if(!controller.signal.aborted)setError(String(cause));});
    load();const timer=activeStates.includes(job.status)?window.setInterval(load,1400):undefined;
    return()=>{controller.abort();if(timer)window.clearInterval(timer);};
  },[job?.id,job?.status,page]);
  useEffect(()=>{if(legacy?.id)void request<LegacyJob>(`/translations/${legacy.id}?page=${page}`).then(setLegacy).catch(cause=>setError(String(cause)));},[legacy?.id,page]);
  useImperativeHandle(ref,()=>({scrollToBlock(id,ratio){const root=scrollRef.current;const element=root?.querySelector<HTMLElement>(`[data-source-block="${CSS.escape(id)}"]`);if(root&&element){suppressUntil.current=performance.now()+250;root.scrollTo({top:root.scrollTop+element.getBoundingClientRect().top-root.getBoundingClientRect().top+element.clientHeight*ratio-root.clientHeight*.25,behavior:'auto'});}}}));
  async function plan(scope:'page'|'document'|'selected'|'pages'){
    if(!structure?.parse_run_id)return;setBusy(true);setError('');
    const glossary:Record<string,string>={};
    for(const line of terms.split('\n').filter(line=>line.trim())){const split=line.indexOf('=');if(split<=0||!line.slice(split+1).trim()){setError('术语表每行使用 原词=译法');setBusy(false);return;}glossary[line.slice(0,split).trim()]=line.slice(split+1).trim();}
    try{const value=await postJSON<DocumentTranslationJob>('/document-translations',{document_id:document.id,parse_run_id:structure.parse_run_id,target_language:language,glossary,scope:scope==='selected'?'blocks':scope==='page'||scope==='pages'?'pages':'document',...(scope==='selected'?{block_ids:[selectedBlock]}:{}),...(scope==='page'?{page_start:page,page_end:page}:scope==='pages'?{page_start:pageStart,page_end:pageEnd}:{})});setLegacy(null);setAllowModel(false);update(value);setHistory(current=>[value,...current.filter(item=>item.id!==value.id)]);}
    catch(cause){setError(String(cause));}finally{setBusy(false);}
  }
  async function control(action:string){if(!job)return;setBusy(true);setError('');try{update(await postJSON<DocumentTranslationJob>(`/document-translations/${job.id}/control`,{action,expected_revision:job.revision,allow_model:allowModel,...limits}));}catch(cause){setError(String(cause));}finally{setBusy(false);}}
  function syncScroll(){
    if(!linked||performance.now()<suppressUntil.current||!scrollRef.current)return;
    const root=scrollRef.current;const y=root.getBoundingClientRect().top+root.clientHeight*.25;
    const elements=[...root.querySelectorAll<HTMLElement>('[data-source-block]')];const selected=elements.find(element=>element.getBoundingClientRect().bottom>=y)||elements.at(-1);
    if(selected){const box=selected.getBoundingClientRect();onAnchor(selected.dataset.sourceBlock!,Math.max(0,Math.min(1,(y-box.top)/Math.max(1,box.height))));}
  }
  const blocks=structure?.blocks.filter(block=>block.page===page&&block.text.trim())||[];
  const units=new Map((job?.items||legacy?.items||[]).map(item=>[item.block_id,item]));
  const active=!!job&&activeStates.includes(job.status);
  const translated=(job?.counts.translated||0)+(job?.counts.cached||0);
  return <aside className="translation-pane" aria-label="译文对照"><div className="translation-controls"><div><Languages size={15}/><strong>全文计划与段落对照</strong><select aria-label="翻译目标语言" disabled={!!initialJobId} value={language} onChange={event=>setLanguage(event.target.value)}><option value="zh">中文</option><option value="en">English</option></select></div>
    {!modelConfigured&&<p className="translation-config-note">未配置生成模型，仍可规划范围、使用已有缓存和查看原文。</p>}
    {!initialJobId&&(history.length>0||legacyHistory.length>0)&&<select aria-label="翻译任务历史" value={job?'bounded:'+job.id:legacy?'legacy:'+legacy.id:''} onChange={event=>{const [kind,id]=event.target.value.split(':');setError('');setAllowModel(false);if(kind==='bounded'){setLegacy(null);void request<DocumentTranslationJob>(`/document-translations/${id}?page=${page}`).then(update).catch(cause=>setError(String(cause)));}else{setJob(null);void request<LegacyJob>(`/translations/${id}?page=${page}`).then(setLegacy).catch(cause=>setError(String(cause)));}}}><option value="">选择已有计划</option>{history.map(item=><option key={item.id} value={'bounded:'+item.id}>{item.spec.scope==='document'?'全文':'局部'} · {item.total} 块 · {labels[item.status]||item.status} · {item.id.slice(0,8)}</option>)}{legacyHistory.map(item=><option key={item.id} value={'legacy:'+item.id}>旧版只读 · {item.total} 块 · {item.status}</option>)}</select>}
    {!initialJobId&&<><details><summary>新计划的术语与页范围</summary><textarea aria-label="翻译术语表" value={terms} onChange={event=>setTerms(event.target.value)} placeholder={'attention=注意力\nencoder=编码器'}/><div className="document-translation-fields"><label>起始页<input aria-label="全文计划起始页" type="number" min={1} max={document.page_count} value={pageStart} onChange={event=>setPageStart(Number(event.target.value))}/></label><label>结束页<input aria-label="全文计划结束页" type="number" min={1} max={document.page_count} value={pageEnd} onChange={event=>setPageEnd(Number(event.target.value))}/></label></div><button disabled={busy||active||!structure} onClick={()=>void plan('pages')}>规划页范围</button></details>
    <div className="translation-buttons"><button disabled={busy||active||!structure} onClick={()=>void plan('page')}>{busy?<LoaderCircle size={12} className="spin"/>:<Languages size={12}/>}规划本页</button><button disabled={busy||active||!selectedBlock} onClick={()=>void plan('selected')}>规划选中段落</button><button disabled={busy||active||!structure} onClick={()=>void plan('document')}>规划全文</button></div><small>规划不调用模型。图像、公式、表格、代码和书目原样保留；超长段落单列缺口。</small></>}
    {job&&<section className="document-translation-plan"><strong>{labels[job.status]||job.status} · {job.spec.scope==='document'?'全文':job.spec.scope==='pages'?`第${job.spec.page_start}—${job.spec.page_end}页`:'选中段落'}</strong><p>计划 {job.page_count} 页；无解析内容页：{job.pages_without_blocks.join('、')||'无'}。</p><p>译文 {translated}/{job.total} · 原样保留 {job.counts.preserved||0} · 未支持 {job.counts.blocked||0} · 失败 {job.counts.failed||0}</p><p>建计划时：缓存 {job.estimate.cache_hits} 块；至多 {job.estimate.uncached_requests} 次新请求，输出预留上界 {job.estimate.output_token_reserve} token。不是费用报价。</p><p>{job.notice}</p><code>{job.source.parse_run_id}</code><details open={job.status==='planned'}><summary>确认本批权限与预算</summary><p>此全文计划独立记录请求与预算。每次确认只执行一批，结束或重启后不自动追加。</p><label className="document-translation-allow"><input aria-label="本批允许调用模型" type="checkbox" disabled={active||!modelConfigured} checked={allowModel} onChange={event=>setAllowModel(event.target.checked)}/>本批允许调用已配置模型（不勾选则仅使用缓存）</label><div className="document-translation-fields">{([['max_model_calls','本批模型请求上限',0,20],['max_output_tokens','本批输出预留上限',0,70000],['max_units','本批处理块上限',1,1000],['max_active_seconds','本批活动秒数上限',5,900]] as const).map(([key,label,min,max])=><label key={key}>{label}<input type="number" aria-label={label} min={min} max={max} disabled={active} value={limits[key]} onChange={event=>setLimits({...limits,[key]:Number(event.target.value)})}/></label>)}</div><small>每次未缓存翻译预留3500输出token；暂停/取消等待当前段落结束，时间上限为段落边界检查。</small></details>
    <div className="translation-buttons">{['planned','paused','budget_exhausted','failed'].includes(job.status)&&<button disabled={busy||allowModel&&!modelConfigured} onClick={()=>void control(job.status==='planned'?'start':job.status==='failed'?'retry':'resume')}>{job.status==='failed'?'明确重试失败段落':allowModel?'确认预算并执行本批':'仅使用缓存执行本批'}</button>}{active&&<button disabled={busy} onClick={()=>void control('pause')}>段落结束后暂停</button>}{!['completed','completed_with_gaps','cancelled'].includes(job.status)&&<button disabled={busy} onClick={()=>void control('cancel')}>取消全文计划</button>}</div><p>累计 {job.totals.batches} 批 · 模型请求 {job.totals.calls} · 实际输入/输出 {job.totals.input_tokens}/{job.totals.output_tokens} token。{job.batch&&`本批处理 ${job.batch.processed} 块，请求 ${job.batch.usage.calls}/${job.batch.limits.max_model_calls}。`}</p><div className="translation-progress">{(['html','markdown','bundle'] as const).map(format=><a key={format} href={`/api/document-translations/${job.id}/export?format=${format}`} download><Download size={12}/>{format==='bundle'?'对照包 ZIP':format==='html'?'HTML':'MD'}</a>)}</div><small>可导出部分结果；无译文、原样保留与失败均明确标注。学术含义仍需核验。</small></section>}
    {legacy&&<div className="translation-progress"><span>旧版任务只读 · {legacy.completed}/{legacy.total} · {legacy.status}</span>{legacy.status==='ready'&&<><a href={`/api/translations/${legacy.id}/export?format=html`}>HTML</a><a href={`/api/translations/${legacy.id}/export?format=markdown`}>MD</a></>}</div>}{error&&<p className="research-error" role="alert">{error}</p>}</div>
    <div className="translation-scroll" ref={scrollRef} onScroll={syncScroll} onWheel={()=>{suppressUntil.current=0;}} onPointerDown={()=>{suppressUntil.current=0;}}>{blocks.map(block=>{const unit=units.get(block.id);return <article key={block.id} className={`translation-unit ${block.id===selectedBlock?'active':''}`} data-source-block={block.id}><header><span>{block.type==='heading'?'标题':'原文内容'} · 第{page}页 · {unit?.status||'尚未规划'}</span><button onClick={()=>onSource(block)}><Target size={12}/>原文</button></header><details className="translation-source-text"><summary>原文段落</summary><p className="translation-original">{block.text}</p></details><p className={`translation-text ${unit?.translation?'':'pending'}`}>{unit?.status==='preserved'?`原样保留：${unit.reason||'公式、表格、代码或书目'}；参看原文。`:(unit?.reviewed_translation||unit?.translation)|| (unit?.status==='blocked'?unit.reason:unit?.status==='failed'?'本段翻译失败，未生成译文。':'本段尚未翻译。')}</p>{!!unit?.edits?.length&&<details><summary>校订 {unit.edits.length} 次 · 原始模型候选与理由</summary><p className="translation-original">{unit.translation}</p>{unit.edits.map(edit=><p key={edit.version}>v{edit.version} · {edit.origin==='assistant_source_review'?'开发助手原文复核':'用户校订'} · {edit.reason}</p>)}</details>}{job&&unit?.translation&&unit.source_sha256&&!active&&<TranslationEdit job={job} unit={unit} onSaved={update}/>}</article>;})}</div></aside>;
});
export default TranslationPane;
