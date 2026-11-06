import { useEffect, useState } from 'react';
import { postJSON, request } from './api';
interface Unit { id: string; text: string; sources: { page: number }[]; }
export default function ReferenceDiscovery({ documentId, runId, readOnly, onOpen }: { documentId: string; runId: string; readOnly: boolean; onOpen: (id: string) => void }) {
  const [units, setUnits] = useState<Unit[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [network, setNetwork] = useState(false);
  const [budget, setBudget] = useState(1);
  const [queries, setQueries] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => {
    const controller = new AbortController(); setUnits([]); setSelected([]); setError('');
    void request<{ units: Unit[] }>(`/documents/${documentId}/reference-discovery?parse_run_id=${encodeURIComponent(runId)}`, { signal: controller.signal }).then(v => { if (!controller.signal.aborted) setUnits(v.units); }).catch(e => { if (!controller.signal.aborted) setError(String(e)); });
    return () => controller.abort();
  }, [documentId, runId]);
  async function discover() {
    setBusy(true); setError('');
    try { const result = await postJSON<{ search_id: string }>(`/documents/${documentId}/reference-discovery`, { parse_run_id: runId, unit_ids: selected, network: network ? 'allow' : 'cache_only', max_requests: network ? budget : 0, queries: network ? Object.fromEntries(Object.entries(queries).filter(([id, text]) => selected.includes(id) && text.trim()).map(([id, text]) => [id, text.trim()])) : {} }); onOpen(result.search_id); }
    catch (e) { setError(String(e)); } finally { setBusy(false); }
  }
  return <details><summary>从原文书目批量发现候选</summary><p>选择至多5条，按原文标识符匹配已保存注册记录。默认只查缓存，不下载；启用联网后仅查无命中或明确改词的条目，无结果不代表不存在。候选仍须核对身份与版本。</p>
    <div style={{ maxHeight: 300, overflow: 'auto' }}>{units.map(u => <div key={u.id}><label style={{ display: 'flex', gap: 8, margin: '12px 0' }}><input type="checkbox" checked={selected.includes(u.id)} disabled={busy || !selected.includes(u.id) && selected.length >= 5} onChange={() => setSelected(v => v.includes(u.id) ? v.filter(i => i !== u.id) : [...v, u.id])} /><span>第{u.sources.map(s => s.page).join('、')}页 · {u.text}</span></label>{network && selected.includes(u.id) && <input aria-label={`书目检索词 ${u.id}`} style={{width: "95%"}} placeholder="可选：核对后填写标题或标识符；留空使用原文" maxLength={500} value={queries[u.id] || ""} onChange={e => setQueries(q => ({...q, [u.id]: e.target.value}))} />}</div>)}</div>
    <label><input type="checkbox" checked={network} disabled={busy} onChange={e => setNetwork(e.target.checked)} />允许把所选参考文献条目或改词发送给官方书目接口（不发送正文、不获取PDF）</label>{network && <label>本次最多请求<select value={budget} disabled={busy} onChange={e => setBudget(Number(e.target.value))}>{[1,2,3].map(n => <option key={n} value={n}>{n}次</option>)}</select>；失败不自动重试，每条最多3个候选</label>}
    {error && <p role="alert">{error}</p>}<button disabled={readOnly || busy || !selected.length} onClick={() => void discover()}>{busy ? '正在保存候选…' : `展开 ${selected.length} 条并核对书目`}</button>{!units.length && <p>当前没有可展开的结构化书目。</p>}</details>;
}
