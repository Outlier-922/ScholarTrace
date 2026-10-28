import { useEffect, useState } from 'react';
import { postJSON, request } from './api';
import './semantic-index.css';

type Needed = { document_id: string; parse_run_id: string; missing_chunks: number };
type Job = { id: string; status: string; done: number; total: number; error?: string };

export default function SemanticIndexPanel({ items, titles }: { items: Needed[]; titles: Record<string, string> }) {
  const [jobs, setJobs] = useState<Record<string, Job>>({});
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState('');
  const active = Object.entries(jobs).filter(([, job]) => ['queued', 'running'].includes(job.status));
  useEffect(() => {
    if (!active.length) return;
    let alive = true;
    const poll = async () => {
      for (const [key, job] of active) {
        try {
          const next = await request<Job>(`/semantic-index-jobs/${job.id}`);
          if (alive) setJobs(current => ({ ...current, [key]: next }));
        } catch (cause) {
          if (alive) {
            setError(cause instanceof Error ? cause.message : '索引状态暂时不可用');
            setJobs(current => ({ ...current, [key]: { ...job, status: 'interrupted' } }));
          }
        }
      }
    };
    const timer = window.setInterval(() => void poll(), 1200);
    return () => { alive = false; window.clearInterval(timer); };
  }, [jobs]);
  async function prepare(item: Needed) {
    setStarting(true); setError('');
    try {
      const job = await postJSON<Job>('/semantic-index-jobs', { document_id: item.document_id, parse_run_id: item.parse_run_id });
      setJobs(current => ({ ...current, [item.document_id + item.parse_run_id]: job }));
    } catch (cause) { setError(cause instanceof Error ? cause.message : '索引准备未启动'); }
    finally { setStarting(false); }
  }
  return <section className="semantic-index-panel" aria-label="语义索引准备">
    <strong>部分文献尚未准备语义索引</strong>
    <p>本次保留 BM25 结果。可在本机准备索引，完成后重新提问；不会下载模型或调用付费大模型。</p>
    {items.map(item => {
      const key = item.document_id + item.parse_run_id, job = jobs[key];
      const running = job && ['queued', 'running'].includes(job.status);
      return <div className="semantic-index-item" key={key}>
        <span title={titles[item.document_id]}>{titles[item.document_id] || '所选文献'}<small>{job ? job.status === 'completed' ? '准备完成，请重新提问' : running ? `准备中 ${job.done}/${job.total || '…'}` : job.error || '已中断，可重试' : `待准备 ${item.missing_chunks} 个片段`}</small></span>
        <button disabled={starting || active.length > 0 || job?.status === 'completed'} onClick={() => void prepare(item)}>{running ? '准备中…' : job?.status === 'completed' ? '已完成' : '准备索引'}</button>
      </div>;
    })}
    {error && <p role="alert">{error}</p>}
  </section>;
}
