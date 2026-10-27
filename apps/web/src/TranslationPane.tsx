import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from 'react';
import { Download, Languages, LoaderCircle, RefreshCw, Target } from 'lucide-react';
import { postJSON, request } from './api';
import type { DocumentBlock, DocumentStructure, ResearchDocument } from './types';

interface Unit { block_id: string; page: number; status: string; source: DocumentBlock; translation: string | null; }
interface Job { id: string; document_id: string; parse_run_id: string; target_language: string; status: string; total: number; completed: number; error: string | null; items: Unit[]; }
export interface TranslationHandle { scrollToBlock: (id: string, ratio: number) => void; }

const TranslationPane = forwardRef<TranslationHandle, { document: ResearchDocument; structure: DocumentStructure | null; page: number; selectedBlock: string | null; modelConfigured: boolean; linked: boolean; onAnchor: (id: string, ratio: number) => void; onSource: (block: DocumentBlock) => void }>(function TranslationPane({ document, structure, page, selectedBlock, modelConfigured, linked, onAnchor, onSource }, ref) {
  const [job, setJob] = useState<Job | null>(null);
  const [history, setHistory] = useState<Job[]>([]);
  const [language, setLanguage] = useState('zh');
  const [terms, setTerms] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const scrollRef = useRef<HTMLDivElement>(null);
  const suppressUntil = useRef(0);
  useEffect(() => {
    setJob(null); setHistory([]); setError('');
    if (!structure?.parse_run_id) return;
    const controller = new AbortController();
    void request<{ items: Job[] }>(`/documents/${document.id}/translations?parse_run_id=${encodeURIComponent(structure.parse_run_id)}`, { signal: controller.signal }).then(async value => {
      setHistory(value.items);
      if (value.items[0]) setJob(await request<Job>(`/translations/${value.items[0].id}?page=${page}`, { signal: controller.signal }));
    }).catch(() => { if (!controller.signal.aborted) setError('翻译历史加载失败'); });
    return () => controller.abort();
  }, [document.id, structure?.parse_run_id]);
  useEffect(() => {
    if (!job?.id) return;
    const controller = new AbortController();
    const load = () => void request<Job>(`/translations/${job.id}?page=${page}`, { signal: controller.signal }).then(setJob).catch(cause => { if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : '翻译状态加载失败'); });
    load();
    const timer = ['queued', 'processing'].includes(job.status) ? window.setInterval(load, 1800) : undefined;
    return () => { controller.abort(); if (timer) window.clearInterval(timer); };
  }, [job?.id, job?.status, page]);
  useImperativeHandle(ref, () => ({ scrollToBlock(id, ratio) { const root = scrollRef.current; const element = root?.querySelector<HTMLElement>(`[data-source-block="${CSS.escape(id)}"]`); if (root && element) { suppressUntil.current = performance.now() + 250; root.scrollTo({ top: root.scrollTop + element.getBoundingClientRect().top - root.getBoundingClientRect().top + element.clientHeight * ratio - root.clientHeight * .25, behavior: 'auto' }); } } }));
  async function start(scope: 'page' | 'document' | 'selected') {
    if (!structure) return;
    setBusy(true); setError('');
    const glossary: Record<string, string> = {};
    for (const line of terms.split('\n').filter(line => line.trim())) { const split = line.indexOf('='); if (split <= 0 || !line.slice(split + 1).trim()) { setError('术语表每行使用 原词=译法'); setBusy(false); return; } glossary[line.slice(0, split).trim()] = line.slice(split + 1).trim(); }
    try { const created = await postJSON<Job>('/translations', { document_id: document.id, parse_run_id: structure.parse_run_id, scope: scope === 'selected' ? 'blocks' : 'document', block_ids: scope === 'selected' && selectedBlock ? [selectedBlock] : [], target_language: language, glossary, ...(scope === 'page' ? { page_start: page, page_end: page } : {}) }); setJob(created); setHistory(current => [created, ...current].slice(0, 50)); }
    catch (cause) { setError(cause instanceof Error ? cause.message : '无法开始翻译'); }
    finally { setBusy(false); }
  }
  function syncScroll() {
    if (!linked || performance.now() < suppressUntil.current || !scrollRef.current) return;
    const root = scrollRef.current; const y = root.getBoundingClientRect().top + root.clientHeight * .25;
    const elements = [...root.querySelectorAll<HTMLElement>('[data-source-block]')];
    const selected = elements.find(element => element.getBoundingClientRect().bottom >= y) || elements.at(-1);
    if (selected) { const box = selected.getBoundingClientRect(); onAnchor(selected.dataset.sourceBlock!, Math.max(0, Math.min(1, (y - box.top) / Math.max(1, box.height)))); }
  }
  const blocks = structure?.blocks.filter(block => block.page === page && block.text.trim()) || [];
  const translated = new Map(job?.items.filter(item => item.source).map(item => [item.block_id, item]));
  return <aside className="translation-pane" aria-label="译文对照"><div className="translation-controls"><div><Languages size={15} /><strong>段落对照</strong><select value={language} onChange={event => setLanguage(event.target.value)} aria-label="翻译目标语言"><option value="zh">中文</option><option value="en">English</option></select></div>{!modelConfigured && <p className="translation-config-note">尚未配置大模型。原文与对齐结构可查看，配置后启用翻译。</p>}<div className="translation-history">{history.length > 0 && <select aria-label="翻译任务历史" value={job?.id || ""} onChange={event => { void request<Job>(`/translations/${event.target.value}?page=${page}`).then(setJob).catch(cause => setError(String(cause))); }}>{history.map((item, index) => <option key={item.id} value={item.id}>任务 {history.length - index} · {item.total} 段 · {item.target_language === "en" ? "英文" : "中文"}</option>)}</select>}</div><details><summary>术语表</summary><textarea value={terms} onChange={event => setTerms(event.target.value)} placeholder={'attention=注意力\nencoder=编码器'} aria-label="翻译术语表" /></details><div className="translation-buttons"><button disabled={!modelConfigured || busy || !structure} onClick={() => void start('page')}>{busy ? <LoaderCircle size={12} className="spin" /> : <Languages size={12} />}翻译本页</button><button disabled={!modelConfigured || busy || !selectedBlock} onClick={() => void start('selected')}>选中段落</button><button disabled={!modelConfigured || busy || !structure} onClick={() => void start('document')}>全文任务</button></div><small>长文按任务预算处理；代码、公式与引用保留。</small>{job && <div className="translation-progress"><span>{job.status === 'ready' ? '翻译完成' : job.status === 'failed' ? '任务失败' : '翻译处理中'} · {job.completed}/{job.total}</span>{job.status === 'ready' && <><a href={`/api/translations/${job.id}/export?format=html`} title="导出对照HTML"><Download size={13} />HTML</a><a href={`/api/translations/${job.id}/export?format=markdown`}>MD</a></>}{job.status === 'failed' && <button onClick={() => void postJSON<Job>(`/translations/${job.id}/retry`, {}).then(setJob).catch(cause => setError(String(cause)))}><RefreshCw size={12} />重试</button>}</div>}{(error || job?.error) && <p className="research-error" role="alert">{error || job?.error}</p>}</div><div className="translation-scroll" ref={scrollRef} onScroll={syncScroll} onWheel={() => { suppressUntil.current = 0; }} onPointerDown={() => { suppressUntil.current = 0; }}>{blocks.map(block => { const unit = translated.get(block.id); return <article key={block.id} className={`translation-unit ${block.id === selectedBlock ? 'active' : ''}`} data-source-block={block.id}><header><span>{block.type === 'heading' ? '标题' : '原文段落'} · 第 {page} 页</span><button onClick={() => onSource(block)}><Target size={12} />原文</button></header><p className="translation-original">{block.text}</p><p className={`translation-text ${unit?.translation ? '' : 'pending'}`}>{unit?.translation || (job && ['processing', 'queued'].includes(job.status) ? '等待本段翻译…' : '此段尚未翻译')}{unit?.status === 'preserved' && <small>（公式、表格、代码或引用保持原文）</small>}</p></article>; })}</div></aside>;
});

export default TranslationPane;
