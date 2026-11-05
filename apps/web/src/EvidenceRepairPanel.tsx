import { useEffect, useRef, useState } from 'react';
import { postJSON, request } from './api';
import type { Evidence } from './types';

interface Claim { claim_id: string; text: string; }
interface Check { id: string; claims: Claim[]; checks: { claim_id: string; status: string }[]; }
interface Plan { id: string; selected_claim_ids: string[]; evidence: (Evidence & { model_excerpt: string })[]; scopes: { claim_id: string; original: Claim; evidence_ids: string[] }[]; warnings: string[]; }
interface Revision { id: string; repair: { items: { claim_id: string; original: Claim; revised: { text: string } | null; status: string; reason: string; quote_adjustments?: string[] }[] }; }
interface Props { check: Check; readOnly: boolean; modelConfigured: boolean; onSource: (source: Evidence) => void; onOpen: (id: string) => void; }

export default function EvidenceRepairPanel({ check, readOnly, modelConfigured, onSource, onOpen }: Props) {
  const [ids, setIds] = useState(check.checks.filter(row => row.status !== 'supported').slice(0, 3).map(row => row.claim_id));
  const [plan, setPlan] = useState<Plan | null>(null);
  const [revision, setRevision] = useState<Revision | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [reuseId, setReuseId] = useState('');
  const operation = useRef<AbortController | null>(null);
  useEffect(() => {
    const abort = new AbortController(); operation.current = abort;
    void request<{ items: { id: string }[] }>(`/insight-checks/${check.id}/repair-plans`, { signal: abort.signal }).then(async history => {
      if (history.items[0]) {
        const saved = await request<Plan>(`/repair-plans/${history.items[0].id}`, { signal: abort.signal });
        const revisions = await request<{ items: { id: string }[] }>(`/repair-plans/${saved.id}/revisions`, { signal: abort.signal });
        if (!abort.signal.aborted) { setPlan(saved); setIds(saved.selected_claim_ids); setReuseId(revisions.items[0]?.id || ''); }
      }
    }).catch(cause => { if (!abort.signal.aborted) setError(String(cause)); });
    return () => { abort.abort(); operation.current?.abort(); };
  }, [check.id]);
  async function act(kind: 'prepare' | 'revise' | 'replay') {
    if (busy) return;
    operation.current?.abort();
    const abort = new AbortController(); operation.current = abort;
    setBusy(true); setError('');
    try {
      if (kind === 'prepare') {
        const next = await postJSON<Plan>(`/insight-checks/${check.id}/repair-plans`, { claim_ids: ids }, abort.signal);
        const revisions = await request<{ items: { id: string }[] }>(`/repair-plans/${next.id}/revisions`, { signal: abort.signal });
        if (!abort.signal.aborted) { setPlan(next); setRevision(null); setReuseId(revisions.items[0]?.id || ''); }
      } else if (plan) {
        const next = await postJSON<Revision>(`/repair-plans/${plan.id}/revise`, kind === 'replay' ? { reuse_revision_id: reuseId } : {}, abort.signal);
        if (!abort.signal.aborted) { setRevision(next); if (!reuseId) setReuseId(next.id); }
      }
    } catch (cause) { if (!abort.signal.aborted) setError(cause instanceof Error ? cause.message : String(cause)); }
    finally { if (!abort.signal.aborted) setBusy(false); }
  }
  return <section className="evidence-repair-panel" aria-label="补充原文与修订"><h4>补充原文与修订</h4><p>选择1—3条陈述，先查找原论文、原解析版本中的候选段落。也可检查已获支持建议但上下文不完整的陈述。</p><div className="repair-claims">{check.claims.map(claim => <label key={claim.claim_id}><input type="checkbox" checked={ids.includes(claim.claim_id)} disabled={busy || readOnly || !ids.includes(claim.claim_id) && ids.length >= 3} onChange={() => { operation.current?.abort(); setIds(current => current.includes(claim.claim_id) ? current.filter(id => id !== claim.claim_id) : [...current, claim.claim_id]); setPlan(null); setRevision(null); }} />{claim.text}</label>)}</div><button disabled={busy || readOnly || !ids.length} onClick={() => void act('prepare')}>查找补充原文（不调用模型）</button>
    {error && <p className="research-error" role="alert">{error}</p>}
    {plan && <><p>已保存候选预览 · {plan.scopes.length}条陈述 · {plan.evidence.length}个证据片段</p><a href={`/api/repair-plans/${plan.id}/export`}>导出候选预览</a>{plan.scopes.map(scope => <details key={scope.claim_id}><summary>候选原文：{scope.original.text.slice(0, 65)}</summary>{plan.evidence.filter(source => scope.evidence_ids.includes(source.id)).map(source => <article className="insight-claim" key={source.id}><blockquote>{source.model_excerpt}</blockquote><button onClick={() => onSource(source)}>{source.title} · PDF第{source.page}页</button></article>)}</details>)}{plan.warnings.map(warning => <p className="graph-scope-note" key={warning}>{warning}</p>)}<button disabled={busy || readOnly || !modelConfigured} onClick={() => void act('revise')}>{busy ? '正在处理…' : '按候选原文生成修订稿'}</button><small>最多一次模型请求、2400输出预留token；模型会收到所选陈述的核验意见；修订稿需要重新核验。</small></>}
    {plan && reuseId && <><button disabled={busy || readOnly} onClick={() => void act('replay')}>重新校验已保存响应的引文（不调用模型）</button><small>复用原响应，只处理连续引文范围与唯一跨页断词候选；仍需语义核验。</small></>}
    {revision && <section aria-label="修订前后对照"><h4>修订前后对照 · 待核验</h4>{revision.repair.items.map(item => <article className="insight-claim" key={item.claim_id}><p>原句：{item.original.text}</p><p>修订：{item.revised?.text || '未形成可保留修订，继续待查。'}</p><small>理由（模型建议）：{item.reason}</small>{item.quote_adjustments?.map((note, index) => <small key={index}>引文处理：{note}</small>)}</article>)}<button onClick={() => onOpen(revision.id)}>打开修订稿，重新核验</button></section>}
  </section>;
}
