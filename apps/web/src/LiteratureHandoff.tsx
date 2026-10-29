import { useEffect, useState } from 'react';
import { postJSON, request } from './api';
import type { ResearchDocument } from './types';
import './literature-handoff.css';

export type HandoffRecord={id:string;key:string;search_id:string|null;parent_task_id:string|null;created_at:string;origin:string;source:{id:string;title:string;filename:string;sha256:string;parse_run_id:string};metadata:{title:string;identifier:string;authors:string[];source_url:string}};
export type Handoff=HandoffRecord&{status:string;notice:string;can_plan:boolean;document:ResearchDocument|null};
const labels:Record<string,string>={ready:'全文可用于新任务',pending:'等待解析',parse_failed:'已绑定版本解析失败',source_unavailable:'来源不可用',source_changed:'文件已变化',parse_unavailable:'解析内容不可用'};

export function LocalPaperHandoff({recordKey,searchId,parentTaskId,onConfirmed}:{recordKey:string;searchId:string;parentTaskId?:string|null;onConfirmed:(value:Handoff)=>void}) {
  const [items,setItems]=useState<ResearchDocument[]|null>(null);
  const [selected,setSelected]=useState('');
  const [confirmed,setConfirmed]=useState(false);
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState('');
  const context={key:recordKey,search_id:searchId,parent_task_id:parentTaskId||null};
  async function find(){setBusy(true);setError('');setSelected('');setConfirmed(false);try{const query=new URLSearchParams({key:recordKey,search_id:searchId,...(parentTaskId?{parent_task_id:parentTaskId}:{})});const value=await request<{items:ResearchDocument[]}>('/literature/local-candidates?'+query);setItems(value.items);}catch(cause){setError(String(cause));}finally{setBusy(false);}}
  const chosen=items?.find(item=>item.id===selected);
  return <div className="local-paper-handoff"><button className="text-button" disabled={busy} onClick={()=>void find()}>查找本地全文（不联网）</button>{items&&<>{items.length?<><label className="studio-field">匹配的本地全文<select aria-label={'本地全文候选 '+recordKey} value={selected} disabled={busy} onChange={event=>{setSelected(event.target.value);setConfirmed(false);}}><option value="">选择要核对的确切文件</option>{items.map(item=><option key={item.id} value={item.id}>{item.filename} · {item.version_label||'本地版本'} · {item.status}</option>)}</select></label>{chosen&&<div className="local-paper-choice"><strong>{chosen.title}</strong><p>{chosen.authors.join('、')||'作者未完整解析，请查看原PDF核对'}</p><code>{chosen.parse_run_id}</code><label><input aria-label={'确认本地全文身份 '+recordKey} type="checkbox" checked={confirmed} disabled={busy} onChange={event=>setConfirmed(event.target.checked)}/>我已核对所选全文的标题、作者与确切版本</label><button className="secondary-button" disabled={busy||!confirmed||!chosen.parse_run_id||!['ready','partial'].includes(chosen.status)} onClick={()=>{setBusy(true);setError('');void postJSON<Handoff>('/literature/handoffs',{...context,document_id:chosen.id,parse_run_id:chosen.parse_run_id,sha256:chosen.sha256,confirm_identity:true}).then(onConfirmed).catch(cause=>setError(String(cause))).finally(()=>setBusy(false));}}>关联本地全文并准备后续任务</button></div>}</>:<p>没有匹配的本地全文。arXiv需确切版本一致；其他书目仅列出标题字面一致的文件。</p>}<p>匹配只用于缩小核对范围，不自动证明论文身份。关联操作不重新下载、不修改原PDF。</p></>}{error&&<p className="research-error" role="alert">{error}</p>}</div>;
}

export function LiteratureHandoffs({onContinue}:{onContinue:(value:Handoff)=>void}) {
  const [items,setItems]=useState<Handoff[]>([]);
  const [error,setError]=useState('');
  async function refresh(){try{setItems((await request<{items:Handoff[]}>('/literature/handoffs')).items);setError('');}catch(cause){setError(String(cause));}}
  useEffect(()=>{void refresh();},[]);
  const pending=items.some(item=>item.status==='pending');
  useEffect(()=>{if(!pending)return;const timer=window.setInterval(()=>void refresh(),2000);return()=>window.clearInterval(timer);},[pending]);
  return <section className="literature-handoffs"><div className="handoff-heading"><h3>从已核对全文，继续研究</h3><button className="text-button" onClick={()=>void refresh()}>刷新交接状态</button></div><p>记录会保留，可关闭页面后回来继续。新任务从已绑定的文件与解析版本开始，原任务的来源和结果保持原样。</p>{error&&<p className="research-error" role="alert">{error}</p>}{items.map(item=><article className="handoff-card" key={item.id}><small>{labels[item.status]||item.status} · {item.origin==='confirmed_local'?'已确认本地全文':'PDF获取入库'}</small><h4>{item.metadata.title}</h4><p>{item.metadata.identifier} · {item.source.filename}</p><code>{item.source.parse_run_id}</code><p>{item.notice}</p><details><summary>交接来源与文件哈希</summary><code>交接：{item.id}</code><code>检索：{item.search_id||'独立书目'}</code><code>原任务：{item.parent_task_id||'独立发现'}</code><code>SHA-256：{item.source.sha256}</code><a href={item.metadata.source_url} target="_blank" rel="noreferrer">原书目来源</a></details><button className="primary-button" disabled={!item.can_plan} onClick={()=>onContinue(item)}>以此全文新建研究任务</button></article>)}{!items.length&&!error&&<p>暂无交接。可先在检索候选中核对并获取PDF，或确认关联已有本地全文。</p>}</section>;
}

export function HandoffPreview({value,onClear}:{value:HandoffRecord;onClear?:()=>void}) {
  return <aside className="handoff-preview"><strong>已承接核对后的全文</strong><p>{value.source.title} · {value.source.filename}</p><code>{value.source.parse_run_id}</code><p>此来源固定包含在新任务中；可以另选文献对比。不会重新检索或下载，也不自动执行模型。</p>{onClear&&<button className="text-button" onClick={onClear}>取消交接，改为普通任务</button>}</aside>;
}
