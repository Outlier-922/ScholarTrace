import { useEffect, useRef, useState } from 'react';
import { postJSON, request } from './api';
import type { Handoff } from './LiteratureHandoff';
import './literature-batch.css';

interface Candidate { id: string; title: string; filename: string; status: string; parse_run_id: string; }
interface Item { key: string; metadata: { title: string; identifier: string; authors: string[] }; candidates: Candidate[]; status: string; attempts: number; error?: string; handoff?: Handoff; choice?: Choice; }
interface Choice { key: string; action: 'download' | 'local'; document_id: string | null; }
interface Batch { id: string; revision: number; confirmed: boolean; status: string; items: Item[]; warnings: string[]; }
interface Saved { id: string; created_at: string; titles: string[]; }
interface Props { searchId: string | null; parentTaskId: string | null; selectedKeys: string[]; onContinue: (value: Handoff) => void; onImported: () => void; }
const labels: Record<string, string> = { draft: '待核对确认', queued: '等待执行', running: '正在执行', blocked: '有项目需要处理', waiting_parse: '等待解析', completed: '全文已就绪', completed_with_gaps: '队列结束，含跳过项', skipped: '已跳过（记录保留）', unconfirmed: '尚未确认', ready: '全文可用', failed: '执行失败', interrupted: '执行中断', source_unavailable: '来源或解析不可用' };

export default function LiteratureBatchPanel({ searchId, parentTaskId, selectedKeys, onContinue, onImported }: Props) {
  const [batch, setBatch] = useState<Batch | null>(null);
  const [history, setHistory] = useState<Saved[]>([]);
  const [choices, setChoices] = useState<Record<string, Choice>>({});
  const [confirmed, setConfirmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [automatic, setAutomatic] = useState(false);
  const [error, setError] = useState('');
  const alive = useRef(true);
  const stop = useRef(false);
  const pending = batch?.items.some(i => i.status === 'waiting_parse' || i.status === 'running') || false;
  useEffect(() => {
    alive.current = true;
    const controller = new AbortController();
    void request<{ items: Saved[] }>('/literature/batches', { signal: controller.signal }).then(v => setHistory(v.items)).catch(cause => { if (!controller.signal.aborted) setError(String(cause)); });
    return () => { alive.current = false; stop.current = true; controller.abort(); };
  }, []);
  useEffect(() => {
    if (!batch || busy || automatic || !pending) return;
    const id = batch.id; const controller = new AbortController();
    const timer = window.setInterval(() => {
      void request<Batch>(`/literature/batches/${id}`, { signal: controller.signal }).then(next => {
        if (!controller.signal.aborted) setBatch(current => current?.id === id && next.revision >= current.revision ? next : current);
      }).catch(cause => { if (!controller.signal.aborted) setError(String(cause)); });
    }, 5000);
    return () => { window.clearInterval(timer); controller.abort(); };
  }, [batch?.id, pending, busy, automatic]);
  async function load(id: string) {
    setBusy(true); setError(''); stop.current = true;
    try {
      const next = await request<Batch>(`/literature/batches/${id}`);
      if (alive.current) { setBatch(next); setChoices(Object.fromEntries(next.items.filter(i => i.choice).map(i => [i.key, i.choice!]))); setConfirmed(false); }
    } catch (cause) { if (alive.current) setError(String(cause)); }
    finally { if (alive.current) setBusy(false); }
  }
  async function preview() {
    setBusy(true); setError('');
    try {
      const next = await postJSON<Batch>('/literature/batches', { search_id: searchId, parent_task_id: parentTaskId, keys: selectedKeys });
      if (alive.current) { setBatch(next); setChoices({}); setConfirmed(false); }
      const saved = await request<{ items: Saved[] }>('/literature/batches');
      if (alive.current) setHistory(saved.items);
    } catch (cause) { if (alive.current) setError(String(cause)); }
    finally { if (alive.current) setBusy(false); }
  }
  async function confirm() {
    if (!batch) return;
    setBusy(true); setError('');
    try {
      const next = await postJSON<Batch>(`/literature/batches/${batch.id}/confirm`, { revision: batch.revision, confirm_identity: confirmed, choices: batch.items.map(i => choices[i.key]) });
      if (alive.current) setBatch(next);
    } catch (cause) { if (alive.current) setError(String(cause)); }
    finally { if (alive.current) setBusy(false); }
  }
  async function run() {
    if (!batch) return;
    setBusy(true); setAutomatic(true); setError(''); stop.current = false;
    let current = batch;
    try {
      while (alive.current && !stop.current && current.items.some(i => i.status === 'queued')) {
        current = await postJSON<Batch>(`/literature/batches/${current.id}/advance`, { revision: current.revision });
        if (alive.current) { setBatch(current); onImported(); }
      }
    } catch (cause) { if (alive.current) setError(`${String(cause)}；请重新读取保存进度后继续。`); }
    finally { if (alive.current) { setBusy(false); setAutomatic(false); } }
  }
  async function retry(key: string, action: 'retry' | 'skip' = 'retry') {
    if (!batch) return;
    setBusy(true); setError('');
    try {
      const next = await postJSON<Batch>(`/literature/batches/${batch.id}/${action}`, { revision: batch.revision, key });
      if (alive.current) setBatch(next);
    } catch (cause) { if (alive.current) setError(String(cause)); }
    finally { if (alive.current) setBusy(false); }
  }
  return <section className="literature-batch" aria-label="批量获取全文">
    <h3>批量获取全文</h3>
    <p>先在“检索论文”逐项核对身份并勾选，再预览处理方式。一次最多5篇确切版本的arXiv文献。</p>
    <button disabled={busy || !searchId || !selectedKeys.length || selectedKeys.length > 5} onClick={() => void preview()}>预览已核对的 {selectedKeys.length} 篇（不下载）</button>
    {selectedKeys.length > 5 && <p role="alert">请选择不超过5篇文献。</p>}
    <label className="batch-history">读取已保存队列<select aria-label="读取已保存批量入库队列" value={batch?.id || ''} disabled={busy} onChange={e => { if (e.target.value) void load(e.target.value); }}><option value="">选择队列，继续上次进度</option>{history.map(h => <option key={h.id} value={h.id}>{new Date(h.created_at).toLocaleString('zh-CN')} · {h.titles[0]}（{h.titles.length}篇）</option>)}</select></label>
    {error && <p className="research-error" role="alert">{error}</p>}
    {batch && <><a href={`/api/literature/batches/${batch.id}/export`}>导出队列与来源记录</a><p role="status">{labels[batch.status] || batch.status} · {batch.items.filter(i => i.status === 'ready').length}/{batch.items.length} 篇全文可用</p>
      {batch.items.map(item => <article className="batch-item" key={item.key}><h4>{item.metadata.title}</h4><p>{item.metadata.authors.slice(0, 8).join(' · ')}</p><small>{item.metadata.identifier} · {labels[item.status] || item.status} · 已执行{item.attempts}次</small>
        {!batch.confirmed && <label>处理方式<select aria-label={`处理方式 ${item.metadata.identifier}`} disabled={busy} value={choices[item.key]?.action === 'local' ? choices[item.key].document_id! : choices[item.key]?.action || ''} onChange={e => {
          const value = e.target.value;
          setChoices(current => { const next = { ...current }; if (!value) delete next[item.key]; else next[item.key] = { key: item.key, action: value === 'download' ? 'download' : 'local', document_id: value === 'download' ? null : value }; return next; }); setConfirmed(false);
        }}><option value="">请选择</option><option value="download">获取此确切版本PDF</option>{item.candidates.map(c => <option key={c.id} value={c.id}>使用已核对的本地文件：{c.filename}（{c.status}）</option>)}</select></label>}
        {item.candidates.filter(c => choices[item.key]?.document_id === c.id).map(c => <aside className="batch-local-source" key={c.id}><strong>所选本地全文：{c.title}</strong><p>{c.filename}</p><small>解析版本：{c.parse_run_id}</small><p><a href={`/api/documents/${c.id}/pdf`} target="_blank" rel="noreferrer">打开此原始 PDF，核对标题、作者与版本</a></p></aside>)}
        {item.error && <p className="research-error">{item.error}</p>}
        {['failed', 'interrupted'].includes(item.status) && item.attempts < 2 && <button disabled={busy} onClick={() => void retry(item.key)}>将此项重新排队（最多再执行1次）</button>}
        {['failed', 'interrupted', 'source_unavailable'].includes(item.status) && <button disabled={busy} onClick={() => void retry(item.key, 'skip')}>跳过此项，保留失败记录</button>}
        {item.status !== 'skipped' && item.handoff?.can_plan && <button disabled={busy} onClick={() => onContinue(item.handoff!)}>使用此全文新建研究任务</button>}
      </article>)}
      {!batch.confirmed ? <><label className="batch-confirm"><input type="checkbox" disabled={busy} checked={confirmed} onChange={e => setConfirmed(e.target.checked)} />我已逐项核对标题、作者、版本和所选本地文件；确认这些处理方式。</label><button disabled={busy || !confirmed || batch.items.some(i => !choices[i.key])} onClick={() => void confirm()}>保存确认，建立队列</button></> : <div className="batch-actions"><button disabled={busy || batch.items.some(i => i.status === 'interrupted') || !batch.items.some(i => i.status === 'queued')} onClick={() => void run()}>继续执行队列</button>{automatic && <button onClick={() => { stop.current = true; }}>本篇结束后暂停</button>}<button disabled={busy} onClick={() => void load(batch.id)}>重新读取进度</button></div>}
      <p>每项最多执行2次，失败不自动重试。关闭页面或切换标签后停止启动下一篇，已开始的请求可能继续。已入库项等待解析，不重复获取。</p>
      {batch.warnings.map(w => <p className="graph-scope-note" key={w}>{w}</p>)}
    </>}
  </section>;
}
