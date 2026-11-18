import { useEffect, useRef, useState } from 'react';
import { LoaderCircle, Target } from 'lucide-react';
import { postJSON, request } from './api';
import type { Evidence } from './types';

type Direction = {
  source: Evidence; direction: string; status: 'retained' | 'addressed_candidate'; issues: string[];
  candidates: { id: string; title: string }[];
  assessment_id: string | null; check_id: string | null;
  search?: { query: string; providers: { provider: string; status: string }[];
    items: { key: string; title: string }[] } | null;
};
type Sweep = { id: string; created_at: string; directions: Direction[]; remaining: number; unprocessed: number;
  usage: { calls: number }; fulltext_attempts: number };
type History = Pick<Sweep,'id' | 'created_at' | 'remaining' | 'unprocessed'>;
type Props = { graphId: string; modelConfigured: boolean; readOnly: boolean;
  onSource: (value: Evidence) => void; onAssessment: (id: string) => void };

export default function FutureSweep({graphId, modelConfigured, readOnly, onSource, onAssessment}: Props) {
  const [value, setValue] = useState<Sweep | null>(null);
  const [history, setHistory] = useState<History[]>([]);
  const [limit, setLimit] = useState(3);
  const [online, setOnline] = useState(false);
  const [importPdf, setImportPdf] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const operation = useRef<AbortController | null>(null);
  const path = `/graphs/${graphId}/future-sweep`;
  useEffect(() => {
    const controller = new AbortController();
    void request<{ items: History[] }>(path+'s',{signal:controller.signal})
      .then(result => setHistory(result.items))
      .catch(cause => { if (!controller.signal.aborted) setError(String(cause)); });
    return () => { controller.abort(); operation.current?.abort(); };
  },[path]);

  async function run(id?: string) {
    if (busy) return;
    const controller = new AbortController();
    operation.current = controller;
    setBusy(true); setError('');
    try {
      const next = id
        ? await request<Sweep>(`/research-insights/${id}`,{signal:controller.signal})
        : await postJSON<Sweep>(path,{limit, use_model:modelConfigured, discover_online:online, import_open_pdfs:online&&importPdf},controller.signal);
      if (!controller.signal.aborted) {
        setValue(next);
        setHistory(old => old.some(row => row.id === next.id) ? old : [next,...old]);
      }
    } catch (cause) { if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : '方向探索中断'); }
    finally { if (!controller.signal.aborted) setBusy(false); }
  }

  return <section aria-label="批量探索未来方向">
    <h3>批量探索未来方向</h3>
    <label>处理方向数 <select value={limit} disabled={busy} onChange={event => setLimit(Number(event.target.value))}>
      {[1,3,5].map(n => <option key={n} value={n}>{n} 项</option>)}
    </select></label>
    <div className="insight-papers">
      <label><input type="checkbox" checked={online} disabled={busy||readOnly} onChange={e=>setOnline(e.target.checked)} />搜索 arXiv 后续工作</label>
      <label><input type="checkbox" checked={importPdf&&online} disabled={!online||busy||readOnly} onChange={e=>setImportPdf(e.target.checked)} />允许下载入库最多 2 篇开放全文</label>
    </div>
    <p>{modelConfigured ? `自动选候选全文，最多 ${limit*2} 次模型请求。已有处理的方向保留原文和二次核对记录。` : '当前显示原文方向与全文候选。'}</p>
    <button disabled={busy||readOnly} onClick={()=>void run()}>{busy ? <LoaderCircle size={13} className="spin" /> : null}批量核对</button>
    {busy && <span role="status"> 正在整理方向、检索并核对证据…</span>}
    {history.length > 0 && <label>批量核对记录 <select value={value?.id??''} disabled={busy} onChange={e=>{ if(e.target.value) void run(e.target.value); }}>
      <option value="">选择记录</option>{history.map(row=><option key={row.id} value={row.id}>{new Date(row.created_at).toLocaleString('zh-CN')} · {row.remaining} 项保留</option>)}
    </select></label>}
    {error && <p role="alert" className="research-error">{error}</p>}
    {value && <>
      <p>本轮 {value.directions.length} 项 · 待核对 {value.remaining} 项{value.unprocessed > 0 && ` · 未处理 ${value.unprocessed} 项`}</p>
      <small>模型请求 {value.usage.calls} 次 · 全文获取尝试 {value.fulltext_attempts} 次</small>
      <a href={`/api/research-insights/${value.id}/export`} download="future-directions.json">导出证据记录</a>
      {(['retained','addressed_candidate'] as const).map(group=>{
        const subset = value.directions.filter(item=>item.status===group);
        return <details key={group} open={group==='retained'}>
          <summary>{group==='retained'?'待核对方向':'已有处理证据的候选方向'}（{subset.length}）</summary>
          {subset.map(item=><article key={item.source.id+item.direction} className="insight-claim">
            <p>{item.direction}</p>
            <button onClick={()=>onSource(item.source)}><Target size={12} />{item.source.title} · 第 {item.source.page} 页</button>
            {item.assessment_id && <button onClick={()=>onAssessment(item.assessment_id!)}>查看全文比较及核对</button>}
            {item.issues.map(issue=><p key={issue}>{issue}</p>)}
            {item.candidates.length>0&&<small>核对范围 {item.candidates.map(candidate=>candidate.title).join('、')}</small>}
            {item.search && <details><summary>联网书目 {item.search.items.length} 条</summary>
              <p>{item.search.query}</p>{item.search.providers.some(provider=>provider.status!=='ok')&&<p>部分书目服务本次未返回结果。</p>}
              {item.search.items.map(record=><p key={record.key}>{record.title}</p>)}
            </details>}
          </article>)}
        </details>;
      })}
    </>}
  </section>;
}
