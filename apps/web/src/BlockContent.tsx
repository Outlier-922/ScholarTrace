import { AlertTriangle, ScanLine, Table2, Target } from 'lucide-react';
import type { DocumentBlock } from './types';

export function isOcrBlock(block: DocumentBlock) {
  return block.metadata?.extraction === 'rapidocr' || block.metadata?.extraction === 'ocr' || block.metadata?.ocr_performed === true;
}

export function confidenceValue(block: DocumentBlock) {
  const value = block.metadata?.confidence;
  return typeof value === 'number' && Number.isFinite(value) ? Math.max(0, Math.min(1, value)) : null;
}

export function SourceBadge({ block }: { block: DocumentBlock }) {
  const confidence = confidenceValue(block);
  const low = block.metadata?.low_confidence === true || (confidence !== null && confidence < 0.85);
  if (isOcrBlock(block)) return <span className={`source-badge ocr ${low ? 'low-confidence' : ''}`} title="OCR 引擎识别分数（0—1），非准确率，也不代表事实正确性"><ScanLine size={11} />OCR{confidence !== null ? ` · 识别分数 ${confidence.toFixed(2)}` : ''}{low && <AlertTriangle size={11} />}</span>;
  if (block.type === 'table') return <span className="source-badge"><Table2 size={11} />表格提取 · 待核验</span>;
  if (block.type === 'figure') return <span className="source-badge">图片区域</span>;
  return <span className="source-badge">原生文字</span>;
}

export default function BlockContent({ block, selected, onSelect }: { block: DocumentBlock; selected: boolean; onSelect: () => void }) {
  const rows = block.metadata?.rows;
  const isTable = block.type === 'table' && Array.isArray(rows) && rows.length > 0;
  const header = block.metadata?.header && typeof block.metadata.header === 'object' ? block.metadata.header as { names?: unknown[]; labels?: unknown[]; external?: boolean } : null;
  const headerLabels = header?.labels || header?.names;
  const hasHeader = Array.isArray(headerLabels) && headerLabels.length > 0;
  const repeatsHeader = isTable && hasHeader && header?.external === false && Array.isArray(header.names) && rows[0].length === header.names.length && rows[0].every((cell, index) => String(cell ?? '') === String(header.names?.[index] ?? ''));
  const bodyRows = isTable ? repeatsHeader ? rows.slice(1) : rows : [];
  const confidence = confidenceValue(block);
  const blockLabel = ({ title: '标题', heading: '标题', reference: '参考文献', table: '表格', table_candidate: '表格候选', table_caption: '表题', formula_candidate: '公式候选', figure_caption: '图注', figure: '图片区域' } as Record<string, string>)[block.type] || '正文';
  return <article className={`structured-block ${selected ? 'active' : ''} ${block.type === 'heading' || block.type === 'title' ? 'heading' : ''}`}>
    <div className="structured-block-header"><span>{blockLabel}</span><SourceBadge block={block} /><button onClick={onSelect} title="定位到原文位置"><Target size={12} />定位</button></div>
    {isTable ? <><div className="extracted-table-scroll"><table className="extracted-table">{hasHeader && <thead><tr>{headerLabels.map((cell, index) => <th key={index} scope="col">{cell === null || cell === '' ? <span className="empty-cell">空值</span> : String(cell)}</th>)}</tr></thead>}<tbody>{bodyRows.map((row, rowIndex) => <tr key={rowIndex}>{row.map((cell, cellIndex) => <td key={cellIndex}>{cell === null || cell === '' ? <span className="empty-cell">空值</span> : String(cell)}</td>)}</tr>)}</tbody></table></div><p className="table-verification-note">结构由解析工具提取，列名、数字与空值请对照原表核验。</p><details className="table-raw-text"><summary>查看检索文本</summary><p data-selection-block={block.id}>{block.text}</p></details></> : <p data-selection-block={block.id} className="structured-block-text">{block.text || '此区域仅含图像，尚无可用识别文字。'}</p>}
    {isOcrBlock(block) && (block.metadata?.low_confidence === true || (confidence !== null && confidence < 0.85)) && <p className="ocr-quality-note"><AlertTriangle size={12} />识别分数较低，请核对原图，尤其是数字、上下标和公式。分数不是准确率。</p>}
  </article>;
}
