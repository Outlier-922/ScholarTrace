import { useEffect, useMemo, useRef, useState } from 'react';
import katex from 'katex';
import 'katex/dist/katex.min.css';
import { postJSON, request } from './api';
import type { DocumentBlock } from './types';
import './formulas.css';

interface Source { id: string; page: number; blocks: DocumentBlock[]; native_supported: boolean; glyphs: unknown[]; warnings: string[]; }
interface Revision { id: string; latex: string; origin: string; note: string; created_at: string; }
interface Props { documentId: string; runId: string; page: number; modelConfigured: boolean; readOnly: boolean; onSource: (block: DocumentBlock) => void; }
export default function FormulaPanel({ documentId, runId, page, modelConfigured, readOnly, onSource }: Props) {
  const [blocks, setBlocks] = useState<(DocumentBlock & { formula_hint: boolean })[]>([]);
  const [ids, setIds] = useState<string[]>([]); const [all, setAll] = useState(false);
  const [source, setSource] = useState<Source | null>(null);
  const [sources, setSources] = useState<{ id: string; created_at: string; block_ids: string[] }[]>([]);
  const [revisions, setRevisions] = useState<Revision[]>([]); const [current, setCurrent] = useState<Revision | null>(null);
  const [tex, setTex] = useState(''); const [note, setNote] = useState('');
  const [busy, setBusy] = useState(false); const [error, setError] = useState('');
  const operation = useRef<AbortController | null>(null);
  useEffect(() => {
    operation.current?.abort(); const abort = new AbortController(); operation.current = abort;
    setBlocks([]); setSources([]); setSource(null); setRevisions([]); setCurrent(null); setTex(''); setNote(''); setIds([]); setError(''); setBusy(true);
    void Promise.all([request<{ items: (DocumentBlock & { formula_hint: boolean })[] }>(`/documents/${documentId}/formula-blocks?page=${page}&parse_run_id=${encodeURIComponent(runId)}`, { signal: abort.signal }), request<{ items: typeof sources }>(`/documents/${documentId}/formula-sources?page=${page}&parse_run_id=${encodeURIComponent(runId)}`, { signal: abort.signal })]).then(([b,s]) => { if (!abort.signal.aborted) { setBlocks(b.items); setSources(s.items); } }).catch(cause => { if (!abort.signal.aborted) setError(String(cause)); }).finally(() => { if (!abort.signal.aborted) setBusy(false); });
    return () => { abort.abort(); operation.current?.abort(); };
  }, [documentId,runId,page]);
  const preview = useMemo(() => {
    if (!tex.trim()) return { html: '', error: '' };
    try { return { html: katex.renderToString(tex, { displayMode: true, throwOnError: true, strict: 'error', trust: false, maxSize: 8, maxExpand: 100, macros: {} }), error: '' }; }
    catch (cause) { return { html: '', error: String(cause).slice(0, 220) }; }
  }, [tex]);
  async function act(kind: 'prepare' | 'load' | 'model' | 'save', identity?: string) {
    if (busy) return;
    operation.current?.abort(); const abort = new AbortController(); operation.current = abort;
    setBusy(true); setError('');
    try {
      if (kind === 'prepare' || kind === 'load') {
        const next = kind === 'prepare' ? await postJSON<Source>('/formula-sources', { document_id: documentId, parse_run_id: runId, page, block_ids: ids }, abort.signal) : await request<Source>(`/formula-sources/${identity}`, { signal: abort.signal });
        const history = await request<{ items: Revision[] }>(`/formula-sources/${next.id}/revisions`, { signal: abort.signal });
        if (!abort.signal.aborted) { setSource(next); setIds(next.blocks.map(b => b.id)); setRevisions(history.items); setCurrent(history.items[0] || null); setTex(history.items[0]?.latex || ''); setNote(''); setSources(old => old.some(s => s.id === next.id) ? old : [{ id: next.id, created_at: new Date().toISOString(), block_ids: next.blocks.map(b => b.id) }, ...old]); }
      } else if (source) {
        const next = kind === 'model' ? await postJSON<Revision>(`/formula-sources/${source.id}/transcribe`, {}, abort.signal) : await postJSON<Revision>(`/formula-sources/${source.id}/revisions`, { latex: tex, note, parent_id: current?.id || null }, abort.signal);
        if (!abort.signal.aborted) { setCurrent(next); setTex(next.latex); setNote(''); setRevisions(old => old.some(r => r.id === next.id) ? old : [next, ...old]); }
      }
    } catch (cause) { if (!abort.signal.aborted) setError(cause instanceof Error ? cause.message : String(cause)); }
    finally { if (!abort.signal.aborted) setBusy(false); }
  }
  return <section className="formula-panel" aria-label="公式LaTeX候选"><h3>公式 LaTeX 候选</h3><p>第{page}页 · 勾选同页1—6个源块，补齐分母和上下标。公式线索可能误检；可展开其他块补选。</p><label><input type="checkbox" checked={all} onChange={e => setAll(e.target.checked)} />显示本页其他内容块</label><div className="formula-blocks">{blocks.filter(b => all || b.formula_hint || ids.includes(b.id)).map(b => <label key={b.id}><input type="checkbox" checked={ids.includes(b.id)} disabled={busy || readOnly || !ids.includes(b.id) && ids.length >= 6} onChange={() => { setIds(old => old.includes(b.id) ? old.filter(id => id !== b.id) : [...old,b.id]); setSource(null); setCurrent(null); setRevisions([]); setTex(''); }} /><span>{b.type === 'figure' ? '图片区域（原图/手工录入）' : b.formula_hint ? '候选' : '内容块'} · {b.text.slice(0,140)}</span></label>)}</div><button disabled={busy || readOnly || !ids.length} onClick={() => void act('prepare')}>查看所选原图与字形（不调用模型）</button>
    {sources.length > 0 && <select aria-label="本页公式来源历史" value={source?.id || ''} disabled={busy} onChange={e => { if (e.target.value) void act('load',e.target.value); }}><option value="">打开已保存公式来源</option>{sources.map(s => <option key={s.id} value={s.id}>{new Date(s.created_at).toLocaleString('zh-CN')} · {s.block_ids.length}个块</option>)}</select>}
    {error && <p role="alert" className="research-error">{error}</p>}{source && <><img className="formula-crop" src={`/api/formula-sources/${source.id}/image`} alt="所选公式区域的原PDF裁剪" /><div>{source.blocks.map((b,i) => <button key={b.id} onClick={() => onSource(b)}>原页核对 · 源块{i+1}</button>)}</div><small>已读取{source.glyphs.length}个原生字形。{!source.native_supported && '扫描/OCR公式请对照原图手工录入；没有可靠原生字形时不请求文字模型。'}</small>{source.warnings.map(w => <p className="formula-note" key={w}>{w}</p>)}<button disabled={busy || readOnly || !modelConfigured || !source.native_supported} onClick={() => void act('model')}>生成 LaTeX 候选</button><small>最多一次请求、1600输出预留token；相同来源与模型/提示版本可复用，公式正确性未确认。</small>
      <label>LaTeX 源码<textarea aria-label="公式LaTeX源码" value={tex} maxLength={4000} disabled={busy || readOnly} onChange={e => setTex(e.target.value)} placeholder="不含$定界符的数学源码" /></label>{preview.error ? <p role="alert" className="research-error">预览未通过：{preview.error}</p> : preview.html && <div className="formula-preview" aria-label="公式渲染预览" dangerouslySetInnerHTML={{ __html: preview.html }} />}
      <p className="formula-note">能渲染不等于与原公式数学等价，请逐个核对符号、上下标和分数范围。</p><label>本次修订说明<input aria-label="公式修订说明" value={note} maxLength={500} disabled={busy || readOnly} onChange={e => setNote(e.target.value)} /></label><button disabled={busy || readOnly || !tex.trim() || !note.trim() || !!preview.error} onClick={() => void act('save')}>另存手工修订（保留旧稿）</button>
      {revisions.length > 0 && <select aria-label="公式修订历史" value={current?.id || ''} onChange={e => { const r = revisions.find(r => r.id === e.target.value); if (r) { setCurrent(r); setTex(r.latex); setNote(''); } }}><option value="">选择记录</option>{revisions.map(r => <option key={r.id} value={r.id}>{new Date(r.created_at).toLocaleString('zh-CN')} · {r.origin === 'model_candidate' ? '模型候选' : '手工修订'}</option>)}</select>}{current && <><p>{current.origin === 'model_candidate' ? '模型候选' : '手工修订'} · 均未独立核验：{current.note}</p><a href={`/api/formula-revisions/${current.id}/export`}>导出带来源的TeX片段</a></>}
    </>}{busy && <p role="status">正在处理…</p>}</section>;
}
