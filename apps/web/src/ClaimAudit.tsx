import type { AnswerResult } from './types';
import './claim-audit.css';

const reasons:Record<string,string>={novel_numeric_literal:'数字字面值不在所附原文中',invalid_claim:'候选结构无效',invalid_claim_text:'候选文字无效或超长',formula_fragment:'公式残片',invalid_evidence_ids:'来源ID无效',invalid_table_claim:'表格来源或值不匹配',invalid_document_or_field:'不是本次文献或所问字段',invalid_status_or_document_evidence:'状态无效或跨文献引用',source_or_structure_check:'来源或结构检查未通过',literal_checks_passed:'通过字面检查，语义未核验',compiled_table_candidate:'已按原生表格重建',provider_missing:'模型认为给定证据不足'};
type Props=Pick<AnswerResult,'model_proposal'|'claim_validation'|'field_validation'> & {onRecheck?:()=>void;replay?:AnswerResult['validation_replay']};
export default function ClaimAudit(value:Props){
  if(!value.model_proposal)return null;
  const audit=value.field_validation?.decisions?value.field_validation:value.claim_validation;
  const rejected=audit?.decisions?.filter(d=>d.status==='rejected')||[];
  const raw=Array.isArray(value.model_proposal.field_answers)?value.model_proposal.field_answers:Array.isArray(value.model_proposal.claims)?value.model_proposal.claims:[];
  return <details className="claim-audit"><summary>模型候选与检查记录 · {rejected.length} 条未采用</summary>
    <p>原始候选仅供追溯；未采用内容没有进入事实回答。数字出现不代表事实、单位和条件正确。</p>
    {value.replay&&<p>保存响应本地复核 · 新增模型请求0 · 原记录 {value.replay.source_run_id} 保留。</p>}
    {value.onRecheck&&<button type="button" onClick={value.onRecheck}>按当前规则复核（0模型请求）</button>}
    {!!audit?.truncated&&<p>另有 {audit.truncated} 条超出20条处理上限，保留在原始JSON，未检查。</p>}
    {rejected.map(d=><article key={d.index}><strong>候选 {d.index+1} · {reasons[d.reason]||'未通过检查'}</strong>
      {!!d.unknown_numbers?.length&&<p>未匹配数字：{d.unknown_numbers.map(n=>n.literal).join('、')}</p>}
      <pre>{JSON.stringify(raw[d.index],null,2)}</pre></article>)}
    <details><summary>完整模型候选 JSON（未经事实确认）</summary><pre>{JSON.stringify(value.model_proposal,null,2)}</pre></details>
    <details><summary>逐条检查记录</summary><pre>{JSON.stringify(audit,null,2)}</pre></details>
  </details>;
}
