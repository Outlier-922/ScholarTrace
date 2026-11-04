import { useEffect, useRef, useState } from 'react';
import { Download, LoaderCircle, Target } from 'lucide-react';
import { postJSON, request } from './api';
import type { Evidence, ResearchDocument } from './types';
import './insights.css';
import ClaimCheckPanel from './ClaimCheckPanel';

interface Claim { text: string; kind: string; evidence_ids: string[]; quotes: { evidence_id: string; quote: string }[]; }
interface Assessment { document_id: string; title: string; relation: string; claim: Claim | null; bucket: string; temporal_status: string; source_date: { date: string | null }; candidate_date: { date: string | null }; notice: string; }
interface Insight { id: string; kind: 'review' | 'future'; topic: string; created_at: string; evidence: Evidence[]; warnings: string[]; rejected_rows?: number; review_rejections?: { number_outside_quotes?: number; paper_scope_omission?: number }; sections?: { title: string; claims: Claim[] }[]; coverage?: { document_id: string; title: string; status: string }[]; assessments?: Assessment[]; }
interface History { id: string; kind: 'review' | 'future'; topic: string; created_at: string; }
interface Props { graphId: string; sources: { document_id: string; title: string }[]; documents: ResearchDocument[]; onSource: (evidence: Evidence) => void; modelConfigured: boolean; readOnly: boolean; }
const relations: Record<string, string> = { addressed: '模型建议已有处理', partial: '模型建议部分涉及', not_addressed: '模型未找到对应处理', insufficient: '证据或输出不足' };
const times: Record<string, string> = { later_version_date: '确切版本日期晚于原方向版本', not_later: '候选版本日期不晚于原版本', same_day_uncertain: '同日，先后顺序不明', unknown: '缺少可核对的版本日期' };

export default function InsightPanel({ graphId, sources, documents, onSource, modelConfigured, readOnly }: Props) {
  const [ids, setIds] = useState(sources.slice(0, 8).map(source => source.document_id));
  const [directions, setDirections] = useState<Evidence[]>([]);
  const [directionId, setDirectionId] = useState('');
  const [focus, setFocus] = useState('');
  const [candidates, setCandidates] = useState<string[]>([]);
  const [value, setValue] = useState<Insight | null>(null);
  const [history, setHistory] = useState<History[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [screened, setScreened] = useState(false);
  const operation = useRef<AbortController | null>(null);
  useEffect(() => {
    const abort = new AbortController();
    void Promise.all([request<{ future_work: Evidence[] }>(`/graphs/${graphId}/outline`, { signal: abort.signal }), request<{ items: History[] }>(`/graphs/${graphId}/insights?kind=review`, { signal: abort.signal }), request<{ items: History[] }>(`/graphs/${graphId}/insights?kind=future`, { signal: abort.signal })]).then(([outline, reviews, checks]) => {
      if (!abort.signal.aborted) { setDirections(outline.future_work); setHistory([...reviews.items, ...checks.items].sort((a, b) => b.created_at.localeCompare(a.created_at))); }
    }).catch(cause => { if (!abort.signal.aborted) setError(String(cause)); });
    return () => { abort.abort(); operation.current?.abort(); };
  }, [graphId]);
  const direction = directions.find(item => item.block_id === directionId);
  async function load(identity: string) {
    operation.current?.abort();
    const abort = new AbortController(); operation.current = abort;
    setBusy(true); setError('');
    try {
      const result = await request<Insight>(`/research-insights/${identity}`, { signal: abort.signal });
      if (!abort.signal.aborted) { setValue(result); setHistory(current => current.some(item => item.id === result.id) ? current : [{ id: result.id, kind: result.kind, topic: result.topic, created_at: result.created_at }, ...current].slice(0, 60)); }
    } catch (cause) { if (!abort.signal.aborted) setError(String(cause)); }
    finally { if (!abort.signal.aborted) setBusy(false); }
  }
  async function create(kind: 'review' | 'future') {
    if (busy) return;
    setBusy(true); setError(''); const abort = new AbortController(); operation.current = abort;
    try {
      const result = await postJSON<Insight>(`/graphs/${graphId}/${kind === 'review' ? 'reviews' : 'future-assessments'}`, kind === 'review' ? { document_ids: ids } : { block_id: directionId, direction_text: focus, candidate_document_ids: candidates }, abort.signal);
      if (!abort.signal.aborted) { setValue(result); setHistory(current => [{ id: result.id, kind, topic: result.topic, created_at: result.created_at }, ...current].slice(0, 60)); }
    } catch (cause) { if (!abort.signal.aborted) setError(cause instanceof Error ? cause.message : '生成失败'); }
    finally { if (!abort.signal.aborted) setBusy(false); }
  }
  function renderClaim(claim: Claim, key: string) {
    return <article className="insight-claim" key={key}><p>{claim.text}</p><details><summary>逐字引文与原文位置</summary>{claim.quotes.map(quote => { const evidence = value?.evidence.find(item => item.id === quote.evidence_id); return <div key={quote.evidence_id}><blockquote>{quote.quote}</blockquote>{evidence && <button onClick={() => onSource(evidence)}><Target size={12} />{evidence.title} · PDF第{evidence.page}页</button>}</div>; })}</details></article>;
  }
  return <div className="insight-panel"><p className="graph-scope-note">生成只使用所选全文片段。引文和数字检查不能证明语义正确；请核对条件、遗漏和模型判断。每次操作最多一次模型请求。</p>
    {readOnly ? <p>只读演示可以查看历史，不能提交生成。</p> : !modelConfigured && <p>尚未配置模型；原有证据提纲仍可使用。</p>}
    <section><h3>自动综述草稿</h3><p>本次选择1-8篇图谱来源；图谱共有{sources.length}篇，超过8篇请分批整理。</p><div className="insight-papers">{sources.map(source => <label key={source.document_id}><input type="checkbox" checked={ids.includes(source.document_id)} disabled={busy || !ids.includes(source.document_id) && ids.length >= 8} onChange={() => setIds(current => current.includes(source.document_id) ? current.filter(id => id !== source.document_id) : [...current, source.document_id])} />{source.title}</label>)}</div><button disabled={busy || readOnly || !modelConfigured || !ids.length} onClick={() => void create('review')}>{busy ? <LoaderCircle size={13} className="spin" /> : null}生成带引用的综述草稿</button></section>
    <section><h3>未来工作全文核对</h3><p>先选确切方向，再勾选已入库候选全文。元数据候选需先核对并导入，不能只凭标题判定已解决。</p><select aria-label="待核对未来工作" value={directionId} onChange={event => { const next = directions.find(item => item.block_id === event.target.value); setDirectionId(event.target.value); setFocus(next?.text.slice(0, 1400) || ''); setCandidates([]); }}><option value="">选择未来工作原文</option>{directions.map(item => <option key={item.block_id} value={item.block_id}>{item.title} · PDF第{item.page}页 · {item.text.slice(0, 65)}</option>)}</select>
      {direction && <><textarea aria-label="确切未来工作片段" maxLength={1400} value={focus} onChange={event => setFocus(event.target.value)} /><small>保留原文的连续子串，最多1400字符；缩小到一个具体方向。</small><button onClick={() => onSource(direction)}><Target size={12} />核对方向原文</button><div className="insight-papers">{documents.filter(doc => ['ready', 'partial'].includes(doc.status) && doc.id !== direction.document_id).map(doc => <label key={doc.id}><input type="checkbox" checked={candidates.includes(doc.id)} disabled={busy || !candidates.includes(doc.id) && candidates.length >= 3} onChange={() => setCandidates(current => current.includes(doc.id) ? current.filter(id => id !== doc.id) : [...current, doc.id])} />候选全文：{doc.title}</label>)}</div><button disabled={busy || readOnly || !modelConfigured || !candidates.length || focus.trim().length < 4} onClick={() => void create('future')}>核对所选候选全文（最多3篇）</button></>}
    </section>
    {history.length > 0 && <label>已保存的生成记录<select aria-label="综述与方向核对历史" value={value?.id || ''} disabled={busy} onChange={event => { if (event.target.value) void load(event.target.value); }}><option value="">选择历史记录</option>{history.map(item => <option key={item.id} value={item.id}>{item.kind === 'review' ? '综述' : '方向核对'} · {new Date(item.created_at).toLocaleString('zh-CN')} · {item.topic.slice(0, 35)}</option>)}</select></label>}
    {error && <p className="research-error" role="alert">{error}</p>}{value && <section className="insight-result" aria-label="生成与核对结果"><header><h3>{value.kind === 'review' ? '自动综述草稿 · 待核验' : '未来方向核对 · 模型建议'}</h3><a href={`/api/research-insights/${value.id}/export`}><Download size={13} />导出证据Markdown</a></header>
      <ClaimCheckPanel insightId={value.id} readOnly={readOnly || busy} modelConfigured={modelConfigured} onSource={onSource} onScreened={setScreened} onOpenRevision={id => void load(id)} />
      <details open={!screened}><summary>原始草稿与原模型建议（保留生成记录）</summary>
      {value.kind === 'review' ? <>{value.sections?.length ? value.sections.map(section => <section key={section.title}><h4>{section.title}</h4>{section.claims.map((claim, index) => renderClaim(claim, `${section.title}-${index}`))}</section>) : <p>没有生成可保留的综述陈述，请查看证据提纲或缩小主题。</p>}<h4>论文覆盖</h4>{value.coverage?.map(source => <p key={source.document_id}>{source.title}：{source.status === 'has_candidate_claim' ? '有候选陈述，仍需核对' : '未覆盖，保留缺口'}</p>)}</> : <>{['retain_for_review', 'provisionally_addressed'].map(bucket => <details key={bucket} open={bucket === 'retain_for_review'}><summary>{bucket === 'retain_for_review' ? '保留核对的候选' : '模型建议已有处理的候选 · 可展开复核'}（{value.assessments?.filter(row => row.bucket === bucket).length || 0}）</summary>{value.assessments?.filter(row => row.bucket === bucket).map(row => <section key={row.document_id}><h4>{row.title}</h4><p>{relations[row.relation]} · {times[row.temporal_status]}</p><small>原版本日期：{row.source_date.date || '未知'}；候选版本日期：{row.candidate_date.date || '未知'}</small>{row.claim ? renderClaim(row.claim, row.document_id) : <p>没有通过来源/引文检查的核对理由。</p>}<small>{row.notice}</small></section>)}</details>)}</>}
      {value.kind === 'review' && <p className="graph-scope-note">未保留的输出条目：{value.rejected_rows || 0}。已显示陈述仍需核对语义支持。</p>}
      {value.review_rejections?.number_outside_quotes ? <p className="graph-scope-note">有 {value.review_rejections.number_outside_quotes} 条陈述中的数字没有出现在所附引文中。</p> : null}
      {value.review_rejections?.paper_scope_omission ? <p className="graph-scope-note">有 {value.review_rejections.paper_scope_omission} 条陈述从片段推断了整篇论文的讨论范围。</p> : null}
      {value.warnings.map(warning => <p className="graph-scope-note" key={warning}>{warning}</p>)}</details></section>}
  </div>;
}
