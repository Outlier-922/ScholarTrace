import { useEffect, useRef, useState } from 'react';
import { postJSON, request } from './api';
import type { Evidence } from './types';
import EvidenceRepairPanel from './EvidenceRepairPanel';

interface Claim { claim_id: string; text: string; kind: string; section: string; quotes: { evidence_id: string; quote: string }[]; }
interface Check { claim_id: string; status: string; reason: string; unsupported_parts: string[]; output_valid: boolean; }
interface Result { id: string; created_at: string; prompt_version: string; claims: Claim[]; checks: Check[]; evidence: Evidence[]; warnings: string[]; counts: Record<string, number>; }
interface Props { insightId: string; readOnly: boolean; modelConfigured: boolean; onSource: (evidence: Evidence) => void; onScreened: (active: boolean) => void; onOpenRevision: (id: string) => void; }
const labels: Record<string, string> = { supported: '模型建议引文支持', partial: '仅部分支持', contradicted: '与引文矛盾', insufficient: '证据或核验输出不足' };

export default function ClaimCheckPanel({ insightId, readOnly, modelConfigured, onSource, onScreened, onOpenRevision }: Props) {
  const [value, setValue] = useState<Result | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const operation = useRef<AbortController | null>(null);
  useEffect(() => {
    operation.current?.abort();
    const abort = new AbortController(); operation.current = abort;
    setValue(null); setBusy(true); setError(''); onScreened(false);
    void request<{ items: Result[] }>(`/research-insights/${insightId}/checks`, { signal: abort.signal }).then(result => {
      if (!abort.signal.aborted) { setValue(result.items[0] || null); onScreened(!!result.items.length); }
    }).catch(cause => { if (!abort.signal.aborted) setError(String(cause)); }).finally(() => { if (!abort.signal.aborted) setBusy(false); });
    return () => { abort.abort(); operation.current?.abort(); };
  }, [insightId, onScreened]);
  async function check() {
    if (busy) return;
    const abort = new AbortController(); operation.current = abort;
    setBusy(true); setError('');
    try {
      const result = await postJSON<Result>(`/research-insights/${insightId}/checks`, {}, abort.signal);
      if (!abort.signal.aborted) { setValue(result); onScreened(true); }
    } catch (cause) { if (!abort.signal.aborted) setError(cause instanceof Error ? cause.message : String(cause)); }
    finally { if (!abort.signal.aborted) setBusy(false); }
  }
  function render(claim: Claim) {
    const row = value!.checks.find(item => item.claim_id === claim.claim_id)!;
    return <article className={`insight-claim support-${row.status}`} key={claim.claim_id}><small>{claim.kind === 'synthesis' ? '跨文陈述' : '单文陈述'} · {labels[row.status]}</small><p>{claim.text}</p><p className="support-reason">核验理由：{row.reason}</p>{row.unsupported_parts.length > 0 && <div>缺乏支持的原句片段：{row.unsupported_parts.map((part, index) => <blockquote key={index}><mark>{part}</mark></blockquote>)}</div>}<details><summary>核对所附短引文与原文</summary>{claim.quotes.map(quote => { const source = value!.evidence.find(item => item.id === quote.evidence_id); return <div key={quote.evidence_id}><blockquote>{quote.quote}</blockquote>{source && <button onClick={() => onSource(source)}>{source.title} · PDF第{source.page}页</button>}</div>; })}</details></article>;
  }
  return <section className="claim-check-panel" aria-label="陈述与所附引文核验"><h4>陈述是否被所附引文支持</h4><p>逐条检查实际引用范围。核验可能使用同一模型，仍会出错；不代表人工确认或全文结论。</p><button disabled={busy || readOnly || !modelConfigured} onClick={() => void check()}>{busy ? '正在读取或核验…' : value ? '复用或更新引文支持核验' : '核验陈述与所附引文'}</button><small>另一次模型请求，最多8条、2400输出预留token；相同原记录、模型与核验版本复用保存结果。</small>
    {error && <p role="alert" className="research-error">核验未完成，原始草稿仍可查看。{error}</p>}
    {value && <><p>保存时间：{new Date(value.created_at).toLocaleString('zh-CN')} · {value.prompt_version}</p><a href={`/api/insight-checks/${value.id}/export`}>导出核验建议与待修订原句</a><h4>模型建议引文支持的陈述（{value.counts.supported || 0}）</h4>{value.claims.filter(claim => value.checks.find(row => row.claim_id === claim.claim_id)?.status === 'supported').map(render)}{!value.counts.supported && <p>没有陈述获得完整支持建议，请完善原文证据并修订草稿。</p>}<details className="support-withheld"><summary>待修订原陈述（{value.claims.length - (value.counts.supported || 0)}）</summary>{value.claims.filter(claim => value.checks.find(row => row.claim_id === claim.claim_id)?.status !== 'supported').map(render)}</details>{value.warnings.map(warning => <p className="graph-scope-note" key={warning}>{warning}</p>)}</>}
    {value && <EvidenceRepairPanel key={value.id} check={value} readOnly={readOnly || busy} modelConfigured={modelConfigured} onSource={onSource} onOpen={onOpenRevision} />}
  </section>;
}
