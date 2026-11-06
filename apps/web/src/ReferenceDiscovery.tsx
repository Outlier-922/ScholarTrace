import { useEffect, useState } from 'react';
import { postJSON, request } from './api';
interface Unit { id: string; text: string; sources: { page: number }[]; }
export default function ReferenceDiscovery({ documentId, runId, readOnly, onOpen }: { documentId: string; runId: string; readOnly: boolean; onOpen: (id: string) => void }) {
  const [units, setUnits] = useState<Unit[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => {
    const controller = new AbortController(); setUnits([]); setSelected([]); setError('');
    void request<{ units: Unit[] }>(`/documents/${documentId}/reference-discovery?parse_run_id=${encodeURIComponent(runId)}`, { signal: controller.signal }).then(v => { if (!controller.signal.aborted) setUnits(v.units); }).catch(e => { if (!controller.signal.aborted) setError(String(e)); });
    return () => controller.abort();
  }, [documentId, runId]);
  async function discover() {
    setBusy(true); setError('');
    try { const result = await postJSON<{ search_id: string }>(`/documents/${documentId}/reference-discovery`, { parse_run_id: runId, unit_ids: selected }); onOpen(result.search_id); }
    catch (e) { setError(String(e)); } finally { setBusy(false); }
  }
  return <details><summary>从原文书目批量发现候选（本地缓存）</summary><p>选择至多5条，按原文标识符匹配已保存注册记录。不联网、不下载；无命中不代表不存在。候选仍须核对身份与版本。</p>
    <div style={{ maxHeight: 300, overflow: 'auto' }}>{units.map(u => <label key={u.id} style={{ display: 'flex', gap: 8, margin: '12px 0' }}><input type="checkbox" checked={selected.includes(u.id)} disabled={busy || !selected.includes(u.id) && selected.length >= 5} onChange={() => setSelected(v => v.includes(u.id) ? v.filter(i => i !== u.id) : [...v, u.id])} /><span>第{u.sources.map(s => s.page).join('、')}页 · {u.text}</span></label>)}</div>
    {error && <p role="alert">{error}</p>}<button disabled={readOnly || busy || !selected.length} onClick={() => void discover()}>{busy ? '正在保存候选…' : `展开 ${selected.length} 条并核对书目`}</button>{!units.length && <p>当前没有可展开的结构化书目。</p>}</details>;
}
