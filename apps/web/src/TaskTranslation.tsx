import { useEffect, useState } from 'react';
import { request } from './api';
import type { Evidence, ResearchDocument } from './types';
import './tasks-translation.css';

export type TranslationPassage = { document_id: string; parse_run_id: string; block_id: string; page: number };
export type TranslationSpec = { passages: TranslationPassage[]; target_language: 'zh' | 'en'; glossary: Record<string, string> };
type Block = { id: string; type: string; page: number; text: string; section_path?: string[] };
export type TranslationResult = { translation?: string | null; translation_status?: string; target_language?: string; glossary?: Record<string, string> };

export function TaskTranslationPicker({ documents, value, onChange, onValidityChange }: { documents: ResearchDocument[]; value: TranslationSpec; onChange: (value: TranslationSpec) => void; onValidityChange: (valid: boolean) => void }) {
  const [documentId, setDocumentId] = useState('');
  const [page, setPage] = useState(1);
  const [blocks, setBlocks] = useState<Block[]>([]);
  const [loaded, setLoaded] = useState<{ document_id: string; parse_run_id: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [terms, setTerms] = useState(Object.entries(value.glossary).map(([key, target]) => key + '=' + target).join('\n'));
  useEffect(() => { onValidityChange(true); }, [onValidityChange]);
  const document = documents.find(item => item.id === documentId) || documents[0];
  async function readPage() {
    if (!document?.parse_run_id) { setError('文献解析版本尚未就绪，请刷新后选择。'); return; }
    setBusy(true); setError(''); setBlocks([]); setLoaded(null);
    try {
      const structure = await request<{ parse_run_id: string; blocks: Block[] }>('/documents/' + document.id + '/structure?page=' + page + '&parse_run_id=' + encodeURIComponent(document.parse_run_id));
      setLoaded({ document_id: document.id, parse_run_id: structure.parse_run_id });
      setBlocks(structure.blocks.filter(block => ['paragraph', 'text', 'list_item'].includes(block.type) && block.text.trim().length >= 10 && block.text.length <= 12000 && !/references|bibliography|参考文献/i.test(String(block.section_path || ''))));
    } catch (cause) { setError(cause instanceof Error ? cause.message : '读取原文失败'); }
    finally { setBusy(false); }
  }
  function updateTerms(text: string) {
    setTerms(text);
    const glossary: Record<string, string> = {};
    for (const line of text.split('\n').filter(line => line.trim())) {
      const split = line.indexOf('=');
      if (split <= 0 || !line.slice(split + 1).trim()) { setError('术语表每行使用“原词=译法”。'); onValidityChange(false); return; }
      glossary[line.slice(0, split).trim()] = line.slice(split + 1).trim();
    }
    setError(''); onValidityChange(true); onChange({ ...value, glossary });
  }
  return <section className="task-translation-picker" aria-label="选择任务待译段落">
    <h4>先选原文，再确认翻译</h4><p>最多4个正文段落，每段单独保存检查点。此处只读取本地页面；翻译范围会在执行前列入计划。</p>
    <div className="task-translation-selectors"><label className="studio-field">原文文献<select aria-label="待译文献" disabled={busy} value={document?.id || ''} onChange={event => { setDocumentId(event.target.value); setPage(1); setBlocks([]); setLoaded(null); }}>{documents.map(item => <option key={item.id} value={item.id}>{item.title} · {item.version_label || item.filename}</option>)}</select></label><label className="studio-field">PDF页码<input aria-label="待译PDF页码" disabled={busy} type="number" min={1} max={document?.page_count || 1} value={page} onChange={event => { setPage(Number(event.target.value)); setBlocks([]); setLoaded(null); }} /></label><button className="secondary-button" disabled={busy || !document || page < 1 || page > document.page_count} onClick={() => void readPage()}>{busy ? '读取中…' : '读取本页段落'}</button></div>
    <div className="task-translation-blocks">{loaded && !blocks.length && <p>本页没有可选正文段落，请换页；图表、公式和书目不加入本步骤。</p>}{loaded && blocks.map(block => {
      const selected = value.passages.some(item => item.document_id === loaded.document_id && item.parse_run_id === loaded.parse_run_id && item.block_id === block.id);
      return <label key={block.id}><input type="checkbox" aria-label={'选择待译段落：' + block.text.slice(0, 65)} checked={selected} disabled={!selected && value.passages.length >= 4} onChange={event => onChange({ ...value, passages: event.target.checked ? [...value.passages, { ...loaded, block_id: block.id, page: block.page }] : value.passages.filter(item => !(item.document_id === loaded.document_id && item.block_id === block.id && item.parse_run_id === loaded.parse_run_id)) })} /><span>{block.text}</span></label>;
    })}</div>
    <div className="task-translation-selected"><strong>已选 {value.passages.length}/4 段</strong>{value.passages.map((item, index) => <div key={item.document_id + item.block_id}><span>{index + 1}. {documents.find(document => document.id === item.document_id)?.title || '来源已移出当前文献选择'} · PDF第{item.page}页</span><button className="text-button" aria-label={'移除待译段落 ' + (index + 1)} onClick={() => onChange({ ...value, passages: value.passages.filter((_, selected) => selected !== index) })}>移除</button></div>)}</div>
    <label className="studio-field">目标语言<select aria-label="任务翻译目标语言" value={value.target_language} onChange={event => onChange({ ...value, target_language: event.target.value as 'zh' | 'en' })}><option value="zh">中文</option><option value="en">英文</option></select></label><label className="studio-field">术语表（可选，每行“原词=译法”）<textarea aria-label="任务翻译术语表" value={terms} rows={2} maxLength={10000} onChange={event => updateTerms(event.target.value)} /></label>
    {error && <p role="alert">{error}</p>}<small>未允许模型且无匹配缓存时只保留待译原文。缓存按原文、版本、目标语言、术语表与模型绑定；译文不自动作为论文事实。</small>
  </section>;
}

export function TaskTranslationResult({ result, onSource }: { result: TranslationResult & { evidence?: Evidence[] }; onSource: (evidence: Evidence) => void }) {
  const source = result.evidence?.[0];
  return <section className="task-translation-result"><p className="studio-note">{result.translation_status === 'cached' ? '复用相同来源与参数的缓存译文 · 语义待核验' : result.translation_status === 'translated' ? '译文候选 · 数字/占位符保护通过 · 语义待核验' : '尚未生成译文 · 原文保留'}</p>{source && <><div className="task-translation-origin"><strong>{source.title}</strong><button className="source-anchor" onClick={() => onSource(source)}>PDF 第 {source.page} 页</button></div><div className="task-translation-columns"><article><h4>原文</h4><p>{source.text}</p></article><article><h4>译文 · {result.target_language === 'en' ? '英文' : '中文'}</h4><p>{result.translation || '当前没有可用译文；未把原文复制成已翻译结果。'}</p></article></div></>}{Object.keys(result.glossary || {}).length > 0 && <p className="task-translation-glossary">术语：{Object.entries(result.glossary || {}).map(([term, target]) => term + ' → ' + target).join('；')}</p>}</section>;
}
