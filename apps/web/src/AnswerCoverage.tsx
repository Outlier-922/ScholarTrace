import type { AnswerResult } from './types';
import './answer-coverage.css';

const labels: Record<string, string> = {
  has_candidate_answer: '有候选回答', missing_in_evidence: '候选证据不足',
  not_assessed: '本次未完成', needs_review: '冲突或检查未通过', source_unavailable: '来源已失效',
};

export default function AnswerCoverage({ rows, onEvidence }: { rows: NonNullable<AnswerResult['answer_coverage']>; onEvidence: (identity: string) => void }) {
  const answered = rows.filter(row => row.status === 'has_candidate_answer').length;
  const documents = [...new Set(rows.map(row => row.document_id))];
  return <details className="answer-coverage" open>
    <summary>已识别字段的覆盖 <span>{answered}/{rows.length} 有候选回答</span></summary>
    <p>仅列出已识别字段，请检查是否漏项；有候选回答仍需核对事实和条件。</p>
    {documents.map(identity => <section key={identity}>
      <h4 title={rows.find(row => row.document_id === identity)?.title}>{rows.find(row => row.document_id === identity)?.title}</h4>
      <ul>{rows.filter(row => row.document_id === identity).map(row => <li key={row.field}>
        <strong>{row.label}</strong><em className={row.status}>{labels[row.status] || '待核对'}</em>
        {!!row.evidence_ids.length && <button aria-label={`查看${row.label}的候选原文`} onClick={() => onEvidence(row.evidence_ids[0])}>原文</button>}
      </li>)}</ul>
    </section>)}
  </details>;
}
