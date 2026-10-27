import { useEffect, useState } from 'react';
import { Download, GitCompareArrows, LoaderCircle, Target, X } from 'lucide-react';
import { postJSON, request } from './api';
import type { Evidence, ResearchDocument } from './types';

interface Cell { document_id: string; parse_run_id: string; status: string; value: string | null; support_quote: string | null; conditions: string | null; evidence: Evidence[]; }
interface Result { id: string; mode: string; documents: ResearchDocument[]; rows: { key: string; label: string; cells: Cell[] }[]; warnings: string[]; }

export default function ComparisonPanel({ documents, initialIds, onClose, onSource }: { documents: ResearchDocument[]; initialIds: string[]; onClose: () => void; onSource: (source: Evidence) => void }) {
  const [ids, setIds] = useState(initialIds.slice(0, 5));
  const [dimensions, setDimensions] = useState<{ key: string; label: string }[]>([]);
  const [keys, setKeys] = useState(['architecture', 'objective', 'parameters', 'context', 'hardware', 'optimizer']);
  const [result, setResult] = useState<Result | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => { const controller = new AbortController(); void request<{ items: { key: string; label: string }[] }>('/comparison-dimensions', { signal: controller.signal }).then(value => setDimensions(value.items)).catch(() => { if (!controller.signal.aborted) setError('无法载入比较维度'); }); return () => controller.abort(); }, []);
  async function compare() {
    setBusy(true); setError(''); setResult(null);
    try { setResult(await postJSON<Result>('/comparisons', { document_ids: ids, dimensions: keys })); }
    catch (cause) { setError(cause instanceof Error ? cause.message : '比较失败'); }
    finally { setBusy(false); }
  }
  return <div className="modal-backdrop" role="presentation"><section className="comparison-dialog" role="dialog" aria-modal="true" aria-label="多文事实矩阵"><header><div><GitCompareArrows size={21} /><h2>多文事实矩阵</h2><p>逐篇检索，逐格保留证据。</p></div><button className="icon-button" onClick={onClose} aria-label="关闭多文对比"><X size={20} /></button></header>
    <div className="comparison-controls"><fieldset><legend>选择 2—5 篇文献</legend>{documents.filter(item => item.status === 'ready' || item.status === 'partial').map(item => <label key={item.id}><input type="checkbox" checked={ids.includes(item.id)} disabled={busy || (!ids.includes(item.id) && ids.length >= 5)} onChange={() => setIds(current => current.includes(item.id) ? current.filter(id => id !== item.id) : [...current, item.id])} /><span>{item.title}<small>{item.version_label ? `${item.version_label} · ${item.filename}` : item.filename}</small></span></label>)}</fieldset><fieldset><legend>比较维度</legend><div className="dimension-options">{dimensions.map(item => <label key={item.key}><input type="checkbox" checked={keys.includes(item.key)} disabled={busy} onChange={() => setKeys(current => current.includes(item.key) ? current.filter(key => key !== item.key) : [...current, item.key])} />{item.label}</label>)}</div><p>未配置模型时显示原文匹配与候选证据，不自动补全缺失事实。</p><button className="primary-button" disabled={busy || ids.length < 2 || keys.length === 0} onClick={() => void compare()}>{busy ? <LoaderCircle className="spin" size={15} /> : <GitCompareArrows size={15} />}{busy ? '正在逐篇检索…' : '生成对比表'}</button></fieldset></div>
    {error && <p role="alert" className="research-error">{error}</p>}
    {result && <div className="comparison-result"><div className="comparison-result-actions"><span>{result.mode === 'llm' ? '模型抽取 · 来源检查' : '原文证据模式'}</span><a className="secondary-button" href={`/api/comparisons/${result.id}/markdown`}><Download size={14} />导出 Markdown</a></div><div className="comparison-table-scroll"><table className="comparison-table"><thead><tr><th>比较项</th>{result.documents.map(item => <th key={item.id}>{item.title}<small>{item.version_label || '未标注版本'}</small></th>)}</tr></thead><tbody>{result.rows.map(row => <tr key={row.key}><th scope="row">{row.label}</th>{row.cells.map(cell => <td key={cell.document_id}><span className={`cell-state ${cell.status}`}>{cell.status === 'model_source_checked' ? '来源与数字已检查 · 待核验语义' : cell.status === 'source_excerpt' ? '原文匹配 · 待核验条件' : cell.status === 'insufficient_evidence' ? '未检索到证据' : '候选证据'}</span><strong className="cell-value">{cell.value || '尚未形成可核验的事实值'}</strong>{cell.conditions && <p className="cell-conditions">{cell.conditions}</p>}{cell.evidence.map((source, index) => <details className="cell-evidence" key={source.id}><summary>证据 {index + 1} · 第 {source.page} 页</summary><p>{source.text}</p><button className="text-button" onClick={() => { onSource(source); onClose(); }}><Target size={13} />定位原文</button></details>)}</td>)}</tr>)}</tbody></table></div><div className="research-warnings">{result.warnings.map((warning, index) => <p key={index}>{warning}</p>)}</div></div>}
  </section></div>;
}
