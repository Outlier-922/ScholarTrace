import { useEffect, useState } from 'react';
import { postJSON, request } from './api';
interface Receipt { id: string; can_plan: boolean; source: {title: string}; }
interface Node { id: string; depth: number; title: string; document_id: string; parse_run_id: string; status: string; attempts: number; unit_ids?: string[]; error?: string; search_id?: string; attachable?: {id: string; title: string}[]; }
interface Plan { id: string; revision: number; limits: {max_depth: number; max_nodes: number; max_requests: number}; requests_used: number; nodes: Node[]; notice: string; }
export default function ReferenceTraversal({ onOpen }: {onOpen: (id: string) => void}) {
  const [receipts,setReceipts]=useState<Receipt[]>([]),[history,setHistory]=useState<{id: string; title: string}[]>([]);
  const [seed,setSeed]=useState(''),[depth,setDepth]=useState(2),[nodes,setNodes]=useState(5),[budget,setBudget]=useState(0);
  const [plan,setPlan]=useState<Plan|null>(null),[units,setUnits]=useState<Record<string,{id: string; text: string}[]>>({}),[selection,setSelection]=useState<Record<string,string[]>>({}),[children,setChildren]=useState<Record<string,string>>({});
  const [busy,setBusy]=useState(false),[error,setError]=useState('');
  useEffect(()=>{const c=new AbortController();void Promise.all([request<{items:Receipt[]}>('/literature/handoffs',{signal:c.signal}),request<{items:{id:string;title:string}[]}>('/literature/traversals',{signal:c.signal})]).then(([r,h])=>{if(!c.signal.aborted){setReceipts(r.items);setHistory(h.items);}}).catch(e=>{if(!c.signal.aborted)setError(String(e));});return()=>c.abort();},[]);
  async function act(kind:string,node?:Node) {
    setBusy(true);setError('');
    try {
      if(kind==='create') {const p=await postJSON<Plan>('/literature/traversals',{handoff_id:seed,max_depth:depth,max_nodes:nodes,max_requests:budget});setPlan(p);setHistory(h=>[{id:p.id,title:p.nodes[0].title},...h]);setUnits({});setSelection({});}
      else if(kind==='units' && node) {const r=await request<{units:{id:string;text:string}[]}>(`/documents/${node.document_id}/reference-discovery?parse_run_id=${encodeURIComponent(node.parse_run_id)}`);setUnits(u=>({...u,[node.id]:r.units}));}
      else if(plan && node) {const p=await postJSON<Plan>(`/literature/traversals/${plan.id}/${kind}`,kind==='expand'?{revision:plan.revision,node_id:node.id,unit_ids:node.unit_ids||selection[node.id]||[]}:{revision:plan.revision,parent_node_id:node.id,handoff_id:children[node.id]});setPlan(p);}
    } catch(e){setError(String(e));}finally{setBusy(false);}
  }
  async function load(id:string) {setBusy(true);setError('');try{setPlan(await request<Plan>(`/literature/traversals/${id}`));setUnits({});setSelection({});}catch(e){setError(String(e));}finally{setBusy(false);}}
  const labels:Record<string,string>={ready:'等待选条目',running:'正在展开',done:'候选已保存',failed:'执行失败，可明确重试',interrupted:'服务执行中断，可明确重试'};
  return <section className="literature-batch"><h3>有界多层展开计划</h3><p>从已确认全文开始。下一层先核对候选并关联全文，再回来加入计划；计划不自动获取PDF或调用模型。联网预算大于0表示允许将选中的书目条目发送给官方书目接口。按原文顺序选择条目不代表引用价值排名。</p>
    <label>起始全文<select value={seed} disabled={busy} onChange={e=>setSeed(e.target.value)}><option value="">选择已确认且可用的全文</option>{receipts.filter(r=>r.can_plan).map(r=><option key={r.id} value={r.id}>{r.source.title}</option>)}</select></label>
    <label>最多层数<select value={depth} onChange={e=>setDepth(Number(e.target.value))}>{[1,2].map(n=><option key={n} value={n}>{n}层</option>)}</select></label><label>最多全文数（含起点）<select value={nodes} onChange={e=>setNodes(Number(e.target.value))}>{[1,2,3,4,5].map(n=><option key={n} value={n}>{n}篇</option>)}</select></label><label>整份计划最多联网请求<select value={budget} onChange={e=>setBudget(Number(e.target.value))}>{[0,1,2,3].map(n=><option key={n} value={n}>{n}次{!n?'（仅缓存）':''}</option>)}</select></label>
    <button disabled={busy||!seed} onClick={()=>void act('create')}>保存计划与总预算</button><label>读取已有计划<select value={plan?.id||''} disabled={busy} onChange={e=>{if(e.target.value)void load(e.target.value);}}><option value="">选择计划</option>{history.map(h=><option key={h.id} value={h.id}>{h.title} · {h.id.slice(0,8)}</option>)}</select></label>
    {error&&<p role="alert">{error}</p>}{plan&&<><p>已用联网请求 {plan.requests_used}/{plan.limits.max_requests} · 全文 {plan.nodes.length}/{plan.limits.max_nodes}</p><button disabled={busy} onClick={()=>void load(plan.id)}>重新读取进度</button><a href={`/api/literature/traversals/${plan.id}/export`}>导出全部层级与来源</a>{plan.nodes.map(n=><article className="batch-item" key={n.id}><h4>第{n.depth}层 · {n.title}</h4><p>{labels[n.status]||n.status} · 已执行{n.attempts}次{n.depth>=plan.limits.max_depth?' · 已到层数上限':''}</p>{n.error&&<p role="alert">{n.error}</p>}
      {n.depth<plan.limits.max_depth&&n.status!=='done'&&<><button disabled={busy||n.status==='running'} onClick={()=>void act('units',n)}>读取此版本参考文献</button><div style={{maxHeight:220,overflow:'auto'}}>{units[n.id]?.map(u=><label key={u.id} style={{display:'flex',gap:8,margin:'10px 0'}}><input type="checkbox" disabled={busy||!!(n.attempts)||!(selection[n.id]||[]).includes(u.id)&&(selection[n.id]||[]).length>=5} checked={(n.unit_ids||selection[n.id]||[]).includes(u.id)} onChange={()=>setSelection(s=>({...s,[n.id]:(s[n.id]||[]).includes(u.id)?s[n.id].filter(i=>i!==u.id):[...(s[n.id]||[]),u.id]}))}/>{u.text}</label>)}</div><button disabled={busy||n.status==='running'||n.attempts>=2||(!n.attempts&&!selection[n.id]?.length)} onClick={()=>void act('expand',n)}>展开所选条目（共享计划预算）</button></>}
      {n.search_id&&<><button onClick={()=>onOpen(n.search_id!)}>打开本层候选，核对与关联全文</button><select value={children[n.id]||''} disabled={busy} onChange={e=>setChildren(c=>({...c,[n.id]:e.target.value}))}><option value="">选择从本层候选已确认的全文</option>{n.attachable?.map(r=><option key={r.id} value={r.id}>{r.title}</option>)}</select><button disabled={busy||!children[n.id]||plan.nodes.length>=plan.limits.max_nodes||n.depth>=plan.limits.max_depth} onClick={()=>void act('attach',n)}>加入下一层（循环作品会拦截）</button></>}
    </article>)}<p>{plan.notice}</p><p>每节点最多执行两次，失败不自动重试。关闭后从历史恢复；服务运行方式沿用本项目单API worker。</p></>}
  </section>;
}
