import { useEffect, useMemo, useRef, useState } from 'react';
import katex from 'katex';
import 'katex/dist/katex.min.css';
import { postJSON, request } from './api';
import type { DocumentBlock } from './types';
import RegionPicker from './RegionPicker';
import './formulas.css';

type Region = DocumentBlock['bbox'];
interface ScanLayout {
  status: 'ocr_ready' | 'needs_ocr' | 'rotated' | 'native';
  items: { bbox: Region; hint: string; label: string | null }[];
}
interface Source { id: string; page: number; blocks: DocumentBlock[]; native_supported: boolean; glyphs: unknown[]; warnings: string[]; region?: Region; }
interface Revision { id: string; latex: string; origin: string; note: string; created_at: string; }
interface Props { documentId: string; runId: string; page: number; modelConfigured: boolean; readOnly: boolean; onSource: (block: DocumentBlock) => void; onRegion: (bbox: Region) => void; }
const originLabel = (origin: string) => origin === 'scan_ocr_candidate' ? '扫描公式候选' : origin === 'model_candidate' ? '原生字形候选' : '手工修订';

export default function FormulaPanel({ documentId, runId, page, modelConfigured, readOnly, onSource, onRegion }: Props) {
  const [blocks, setBlocks] = useState<(DocumentBlock & { formula_hint: boolean })[]>([]);
  const [ids, setIds] = useState<string[]>([]); const [all, setAll] = useState(false);
  const [source, setSource] = useState<Source | null>(null);
  const [sources, setSources] = useState<{ id: string; created_at: string; block_ids: string[]; region?: Region }[]>([]);
  const [scan, setScan] = useState(false);
  const [scanReady, setScanReady] = useState(false);
  const [layout, setLayout] = useState<ScanLayout | null>(null);
  const [region, setRegion] = useState<Region | null>(null);
  const [revisions, setRevisions] = useState<Revision[]>([]); const [current, setCurrent] = useState<Revision | null>(null);
  const [tex, setTex] = useState(''); const [note, setNote] = useState('');
  const [busy, setBusy] = useState(false); const [error, setError] = useState('');
  const operation = useRef<AbortController | null>(null);
  useEffect(() => {
    operation.current?.abort(); const abort = new AbortController(); operation.current = abort;
    setBlocks([]); setSources([]); setSource(null); setRevisions([]); setCurrent(null); setTex(''); setNote(''); setIds([]); setRegion(null); setScan(false); setScanReady(false); setLayout(null); setError(''); setBusy(true);
    void Promise.all([
      request<{ items: typeof blocks; scan_ocr_ready: boolean; scan_layout: ScanLayout }>(`/documents/${documentId}/formula-blocks?page=${page}&parse_run_id=${encodeURIComponent(runId)}`, { signal: abort.signal }),
      request<{ items: typeof sources }>(`/documents/${documentId}/formula-sources?page=${page}&parse_run_id=${encodeURIComponent(runId)}`, { signal: abort.signal }),
    ]).then(([b,s]) => {
      if (!abort.signal.aborted) {
        setBlocks(b.items); setSources(s.items); setScanReady(b.scan_ocr_ready); setLayout(b.scan_layout);
      }
    }).catch(cause => { if (!abort.signal.aborted) setError(String(cause)); }).finally(() => { if (!abort.signal.aborted) setBusy(false); });
    return () => { abort.abort(); operation.current?.abort(); };
  }, [documentId,runId,page]);
  const preview = useMemo(() => {
    if (!tex.trim()) return { html: '', error: '' };
    try { return { html: katex.renderToString(tex, { displayMode: true, throwOnError: true, strict: 'error', trust: false, maxSize: 8, maxExpand: 100, macros: {} }), error: '' }; }
    catch (cause) { return { html: '', error: String(cause).slice(0, 220) }; }
  }, [tex]);
  async function act(kind: 'prepare' | 'load' | 'model' | 'recognize' | 'save', identity?: string, suggested?: Region) {
    if (busy) return;
    operation.current?.abort(); const abort = new AbortController(); operation.current = abort;
    setBusy(true); setError('');
    try {
      if (kind === 'prepare' || kind === 'load') {
        const selection = suggested || scan ? { region: suggested ?? region } : { block_ids: ids };
        const next = kind === 'prepare' ? await postJSON<Source>('/formula-sources', { document_id: documentId, parse_run_id: runId, page, ...selection }, abort.signal) : await request<Source>(`/formula-sources/${identity}`, { signal: abort.signal });
        const history = await request<{ items: Revision[] }>(`/formula-sources/${next.id}/revisions`, { signal: abort.signal });
        if (!abort.signal.aborted) { setSource(next); setIds(next.blocks.map(b => b.id)); setRegion(next.region ?? null); setScan(!!next.region); setRevisions(history.items); setCurrent(history.items[0] || null); setTex(history.items[0]?.latex || ''); setNote(''); setSources(old => old.some(s => s.id === next.id) ? old : [{ id: next.id, created_at: new Date().toISOString(), block_ids: next.blocks.map(b => b.id), region: next.region }, ...old]); }
      } else if (source) {
        const next = kind === 'model' || kind === 'recognize'
          ? await postJSON<Revision>(`/formula-sources/${source.id}/${kind === 'recognize' ? 'recognize-scan' : 'transcribe'}`, {}, abort.signal)
          : await postJSON<Revision>(`/formula-sources/${source.id}/revisions`, { latex: tex, note, parent_id: current?.id || null }, abort.signal);
        if (!abort.signal.aborted) { setCurrent(next); setTex(next.latex); setNote(''); setRevisions(old => old.some(r => r.id === next.id) ? old : [next, ...old]); }
      }
    } catch (cause) { if (!abort.signal.aborted) setError(cause instanceof Error ? cause.message : String(cause)); }
    finally { if (!abort.signal.aborted) setBusy(false); }
  }
  function changeSelection() {
    setSource(null); setCurrent(null); setRevisions([]); setTex(''); setNote(''); setError('');
  }
  const hasRegion = region && region[2]-region[0] >= .015 && region[3]-region[1] >= .008;
  const scanPage = layout && layout.status !== 'native';
  return <section className="formula-panel" aria-label="公式LaTeX候选">
    <h3>公式 LaTeX 候选</h3>
    <p>第{page}页 · {scanPage ? '查看公式裁图后，可在本机识别。' : '选择正文中的公式块，也可从原页图像框选。'}</p>
    {layout?.status === 'ocr_ready' && (layout.items.length ? <div className="formula-layout" aria-label="扫描页公式位置">
      {layout.items.map(item => <button key={item.bbox.join(',')} disabled={busy}
        onClick={() => readOnly ? onRegion(item.bbox) : void act('prepare', undefined, item.bbox)}>
        {item.label && <strong>{item.label} </strong>}{item.hint}
      </button>)}
    </div> : <p>本页未定位到可推荐的公式区域，可手工框选。</p>)}
    {layout?.status === 'needs_ocr' && <p>重新解析并启用 OCR 可定位公式，也可直接手工框选。</p>}
    {layout?.status === 'rotated' && <p>旋转扫描页暂不支持公式裁图。</p>}
    <label><input type="checkbox" checked={scan} disabled={busy || layout?.status === 'rotated'} onChange={e => { setScan(e.target.checked); changeSelection(); }} />框选原页公式</label>
    {scan && source ? <button disabled={busy || readOnly} onClick={() => {
      setRegion(null);
      changeSelection();
    }}>重新框选</button> : scan ? <><p>在原页图像上拖动，框住一条完整公式。分子、分母和上下标都应在框内。</p><RegionPicker
      url={`/api/documents/${documentId}/formula-page-image?page=${page}&parse_run_id=${encodeURIComponent(runId)}`}
      key={`${documentId}-${runId}-${page}`} region={region} disabled={busy || readOnly} onChange={box => { changeSelection(); setRegion(box); }}
    /></> : !scanPage && <>
      <label><input type="checkbox" checked={all} onChange={e => setAll(e.target.checked)} />显示本页其他内容块</label>
      <div className="formula-blocks">{blocks.filter(b => all || b.formula_hint || ids.includes(b.id)).map(b => (
        <label key={b.id}><input type="checkbox" checked={ids.includes(b.id)}
          disabled={busy || readOnly || !ids.includes(b.id) && ids.length >= 6}
          onChange={() => { setIds(old => old.includes(b.id) ? old.filter(id => id !== b.id) : [...old,b.id]); changeSelection(); }} />
          <span>{b.type === 'figure' ? '图片区域' : b.formula_hint ? '候选' : '内容块'} · {b.text.slice(0,140)}</span>
        </label>
      ))}</div>
    </>}
    {((scan && !source) || (!scan && !scanPage)) && <button disabled={busy || readOnly || (scan ? !hasRegion : !ids.length)} onClick={() => void act('prepare')}>
      {scan ? '保存选区并预览裁图' : '查看所选原图与字形（不调用模型）'}
    </button>}
    {sources.length > 0 && <select aria-label="本页公式来源历史" value={source?.id || ''} disabled={busy} onChange={e => { if (e.target.value) void act('load',e.target.value); }}><option value="">打开已保存公式来源</option>{sources.map(s => <option key={s.id} value={s.id}>{new Date(s.created_at).toLocaleString('zh-CN')} · {s.region ? '原页选区' : s.block_ids.length + '个块'}</option>)}</select>}
    {error && <p role="alert" className="research-error">{error}</p>}
    {source && <>
      <img className="formula-crop" src={`/api/formula-sources/${source.id}/image`} alt="所选公式区域的原PDF裁剪" />
      {source.region ? <>
        <button onClick={() => onRegion(source.region!)}>在原页定位选区</button>
        <button disabled={busy || readOnly || !scanReady} onClick={() => void act('recognize')}>识别扫描公式</button>
        <small>{scanReady ? '仅在本机处理裁图。生成后请核对原图。' : '本地公式模型尚未准备，可在下方手工录入。'}</small>
      </> : <>
        <div>{source.blocks.map((b,i) => <button key={b.id} onClick={() => onSource(b)}>原页核对 · 源块{i+1}</button>)}</div>
        <small>已读取{source.glyphs.length}个原生字形。</small>
        {source.native_supported ? <button disabled={busy || readOnly || !modelConfigured} onClick={() => void act('model')}>生成 LaTeX 候选</button>
          : <small>可在原页框选公式做本地识别，也可手工录入。</small>}
      </>}
      {source.warnings.map(w => <p className="formula-note" key={w}>{w}</p>)}
      <label>LaTeX 源码<textarea aria-label="公式LaTeX源码" value={tex} maxLength={4000} disabled={busy || readOnly} onChange={e => setTex(e.target.value)} placeholder="不含$定界符的数学源码" /></label>{preview.error ? <p role="alert" className="research-error">预览未通过：{preview.error}</p> : preview.html && <div className="formula-preview" aria-label="公式渲染预览" dangerouslySetInnerHTML={{ __html: preview.html }} />}
      <p className="formula-note">能渲染不等于与原公式数学等价，请逐个核对符号、上下标和分数范围。</p><label>本次修订说明<input aria-label="公式修订说明" value={note} maxLength={500} disabled={busy || readOnly} onChange={e => setNote(e.target.value)} /></label><button disabled={busy || readOnly || !tex.trim() || !note.trim() || !!preview.error} onClick={() => void act('save')}>另存手工修订（保留旧稿）</button>
      {revisions.length > 0 && <select aria-label="公式修订历史" value={current?.id || ''} onChange={e => { const r = revisions.find(r => r.id === e.target.value); if (r) { setCurrent(r); setTex(r.latex); setNote(''); } }}><option value="">选择记录</option>{revisions.map(r => <option key={r.id} value={r.id}>{new Date(r.created_at).toLocaleString('zh-CN')} · {originLabel(r.origin)}</option>)}</select>}{current && <><p>{originLabel(current.origin)} · 均未独立核验：{current.note}</p><a href={`/api/formula-revisions/${current.id}/export`}>导出带来源的TeX片段</a></>}
    </>}{busy && <p role="status">正在处理…</p>}</section>;
}
