import { useEffect, useState } from 'react';
import { postJSON, request } from './api';
interface Citation { id: string; page: number; block_type: string; context: string; text: string; }
interface IdentityClues { identifier: string; source_identifiers: string[]; candidate_identifier: string; matched_literals: string[]; revision_conflicts: string[]; title_literal_match: boolean | null; first_author_surname: string | null; first_author_surname_match: boolean | null; }
interface Row { unit: { id: string; text: string }; candidate_keys: string[]; body_citations: Citation[]; identity_clues?: Record<string, IdentityClues>; }
interface Candidate { key: string; title: string; }
interface Decision { key: string; unit_id: string; identity: string; decision: string; purpose: string; reason: string; citation_id: string | null; quote: string; }
interface Context { document_id: string; rows: Row[]; items: Candidate[]; notice: string; }
const identifierLabels: Record<string,string>={matched:'原文标识符有对应',multiple_literals:'有对应，但原文含多个标识符或版本线索',revision_differs:'同一 arXiv 作品，明确版本未对应',not_aligned:'可比标识符未对应',unavailable:'没有可直接比较的标识符'};
function IdentityHints({value}:{value:IdentityClues}) {
  return <aside className="batch-local-source"><strong>身份核对线索</strong><p>{identifierLabels[value.identifier] || value.identifier}。原文：{value.source_identifiers.join('、')||'未提取到'}；候选：{value.candidate_identifier}。</p>
    {!!value.matched_literals.length && <p>对应的原文字样：{value.matched_literals.join('、')}。</p>}
    {!!value.revision_conflicts.length && <p>原文明确版本未对应：{value.revision_conflicts.join('、')}。</p>}
    <p>{value.title_literal_match===null?'题名线索不足':value.title_literal_match?'原文中出现完整规范化题名':'原文中未出现完整规范化题名'}；{value.first_author_surname_match===null?'首作者姓氏线索不足':`原文${value.first_author_surname_match?'出现':'未出现'}候选首作者姓氏 ${value.first_author_surname}`}。</p>
    <p>这些是字面线索。版本差异、缩写、断行或同姓都需要人工核对。</p></aside>;
}
export default function ReferenceScreening({ searchId, onSaved }: { searchId: string; onSaved: (id: string) => void }) {
  const [context, setContext] = useState<Context | null>(null);
  const [decisions, setDecisions] = useState<Record<string, Decision>>({});
  const [goal, setGoal] = useState(''); const [busy, setBusy] = useState(false); const [error, setError] = useState('');
  const [history, setHistory] = useState<{id: string; goal: string; created_at: string}[]>([]);
  useEffect(() => {
    const c = new AbortController();
    void request<{items: {id: string; goal: string; created_at: string}[]}>(`/literature/searches/${searchId}/screenings`, {signal: c.signal}).then(v => {if (!c.signal.aborted) setHistory(v.items);}).catch(e => {if (!c.signal.aborted) setError(String(e));});
    void request<Context>(`/literature/searches/${searchId}/screening-context`, { signal: c.signal }).then(v => {
      if (c.signal.aborted) return;
      setContext(v); setDecisions(Object.fromEntries(v.items.map(i => [i.key, { key: i.key, unit_id: v.rows.find(r => r.candidate_keys.includes(i.key))?.unit.id || '', identity: 'uncertain', decision: 'hold', purpose: 'unknown', reason: '', citation_id: null, quote: '' }])));
    }).catch(e => { if (!c.signal.aborted) setError(String(e)); });
    return () => c.abort();
  }, [searchId]);
  function patch(key: string, value: Partial<Decision>) { setDecisions(d => ({ ...d, [key]: { ...d[key], ...value } })); }
  async function save() {
    setBusy(true); setError('');
    try { const v = await postJSON<{ search_id: string }>(`/literature/searches/${searchId}/screen`, { goal, decisions: Object.values(decisions) }); onSaved(v.search_id); }
    catch (e) { setError(String(e)); } finally { setBusy(false); }
  }
  return <details className="discovery-guidance"><summary>对照正文，筛选本研究要采用的候选</summary><p>填写研究目标，逐项区分身份核对与引用用途。采用需选择真实引用位置和原句；没有正文证据时保留待查。不调用模型或外网。</p>
    {history.length > 0 && <label>打开已保存筛选<select value="" onChange={e => {if(e.target.value) onSaved(e.target.value);}}><option value="">选择历史记录</option>{history.map(h => <option key={h.id} value={h.id}>{new Date(h.created_at).toLocaleString('zh-CN')} · {h.goal}</option>)}</select></label>}
    <label>本次研究目标<input style={{ width: '95%' }} maxLength={800} value={goal} onChange={e => setGoal(e.target.value)} /></label>
    {context?.items.map(item => {
      const d = decisions[item.key]; if (!d) return null;
      const rows = context.rows.filter(r => r.candidate_keys.includes(item.key)); const row = rows.find(r => r.unit.id === d.unit_id);
      const hints = row?.identity_clues?.[item.key];
      return <fieldset key={item.key} disabled={busy} style={{ margin: '12px 0', minWidth: 0 }}><legend>{item.title}</legend>
        <label>对应原文书目<select value={d.unit_id} onChange={e => patch(item.key, { unit_id: e.target.value, citation_id: null, quote: '' })}>{rows.map(r => <option value={r.unit.id} key={r.unit.id}>{r.unit.text.slice(0, 100)}</option>)}</select></label>
        <p>{row?.unit.text}</p>{hints && <IdentityHints value={hints}/>}<label>候选身份<select value={d.identity} onChange={e => patch(item.key, { identity: e.target.value })}><option value="uncertain">尚不确定</option><option value="confirmed">已核对标题、作者与版本</option><option value="mismatch">与原文书目不符</option></select></label>
        <label>本研究的处理<select value={d.decision} onChange={e => patch(item.key, { decision: e.target.value })}><option value="hold">待查</option><option value="include">采用</option><option value="exclude">排除</option></select></label>
        <label>引用用途<select value={d.purpose} onChange={e => patch(item.key, { purpose: e.target.value })}>{Object.entries({ unknown: '未确定', method: '方法依据', data: '数据来源', comparison: '比较对象', background: '研究背景' }).map(([k,v]) => <option key={k} value={k}>{v}</option>)}</select></label>
        <label>源论文引用位置（含图表）<select value={d.citation_id || ''} onChange={e => { const l = row?.body_citations.find(l => l.id === e.target.value); patch(item.key, { citation_id: l?.id || null, quote: l?.context || '' }); }}><option value="">选择引用位置（没有解析到时保留待查）</option>{row?.body_citations.map(l => <option key={l.id} value={l.id}>第{l.page}页 · {l.block_type === "table_candidate" ? "表格候选" : "原文块"} · {l.context}</option>)}</select></label>
        {d.citation_id && <><a href={`/api/documents/${context.document_id}/pdf#page=${row?.body_citations.find(l => l.id === d.citation_id)?.page || 1}`} target="_blank" rel="noreferrer">打开引用原页核对</a><textarea aria-label={`引用原句 ${item.key}`} style={{ width: '95%' }} maxLength={800} value={d.quote} onChange={e => patch(item.key, { quote: e.target.value })} /></>}
        <label>采用、排除或待查依据（至少8字）<textarea style={{ width: '95%' }} maxLength={800} value={d.reason} onChange={e => patch(item.key, { reason: e.target.value })} /></label>
      </fieldset>;
    })}
    {context && <p>{context.notice}</p>}{error && <p role="alert">{error}</p>}<button disabled={busy || !context || goal.trim().length < 4 || Object.values(decisions).some(d => d.reason.trim().length < 8)} onClick={() => void save()}>另存筛选结果，仅将采用项送入后续候选</button>
  </details>;
}
