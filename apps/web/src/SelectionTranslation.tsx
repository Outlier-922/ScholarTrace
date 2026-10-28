import { useEffect, useRef, useState } from 'react';
import type { RefObject } from 'react';
import { Languages, LoaderCircle, Target, X } from 'lucide-react';
import type { DocumentBlock } from './types';
import { postJSON } from './api';
import './selection-translation.css';

type Anchor = { block: DocumentBlock; text: string; start: number; end: number };
type Result = { id: string; status: string; translation: string; source: { block_id: string; page: number; parse_run_id: string }; notice: string };
type Props = { root: RefObject<HTMLDivElement | null>; documentId: string; runId?: string; page: number; blocks: DocumentBlock[]; modelConfigured: boolean; readOnly: boolean; bounds: (box: DocumentBlock['bbox']) => { left: string; top: string; width: string; height: string }; onSource: (block: DocumentBlock) => void };

function normalized(text: string) {
  let value = ''; const positions: number[] = [];
  for (let i = 0; i < text.length; i++) {
    const letter = /\s/.test(text[i]) ? ' ' : text[i];
    if (letter === ' ' && value.endsWith(' ')) continue;
    value += letter; positions.push(i);
  }
  return { value, positions };
}

export default function SelectionTranslation({ root, documentId, runId, page, blocks, modelConfigured, readOnly, bounds, onSource }: Props) {
  const [anchor, setAnchor] = useState<Anchor | null>(null);
  const [selectionError, setSelectionError] = useState('');
  const [error, setError] = useState('');
  const [language, setLanguage] = useState('zh');
  const [result, setResult] = useState<Result | null>(null);
  const [busy, setBusy] = useState(false);
  const controller = useRef<AbortController | null>(null);
  const serial = useRef(0);
  function resetRequest() { serial.current++; controller.current?.abort(); setResult(null); setBusy(false); setError(''); }
  useEffect(() => { resetRequest(); setAnchor(null); setSelectionError(''); return () => { serial.current++; controller.current?.abort(); }; }, [documentId, runId, page]);
  useEffect(() => {
    const container = root.current;
    if (!container) return;
    function capture(event: Event) {
      if (event.target instanceof Node && !container!.contains(event.target) && !(event.type === 'keyup' && event.target === container!.ownerDocument.body)) return;
      const selection = container!.ownerDocument.getSelection();
      if (!selection?.rangeCount || selection.isCollapsed || !container!.contains(selection.anchorNode) || !container!.contains(selection.focusNode)) return;
      const selected = normalized(selection.toString()).value.trim();
      if (!selected) return;
      resetRequest();
      if (selected.length > 1000) { setAnchor(null); setSelectionError('一次最多选1000字符，请缩小选区。'); return; }
      const range = selection.getRangeAt(0);
      const paper = container!.querySelector('.pdf-paper');
      const rects = [...range.getClientRects()].filter(rect => rect.width > 0 && rect.height > 0);
      const paperBox = paper?.getBoundingClientRect();
      const structured = (selection.anchorNode instanceof Element ? selection.anchorNode : selection.anchorNode?.parentElement)?.closest<HTMLElement>('[data-selection-block]');
      const candidates: Anchor[] = [];
      for (const block of blocks) {
        if (structured && structured.dataset.selectionBlock !== block.id) continue;
        if (!structured) {
          if (!paperBox || !paper?.contains(selection.anchorNode) || !paper.contains(selection.focusNode) || !rects.length) continue;
          const box = bounds(block.bbox);
          const left = paperBox.left + parseFloat(box.left) / 100 * paperBox.width;
          const top = paperBox.top + parseFloat(box.top) / 100 * paperBox.height;
          const right = left + parseFloat(box.width) / 100 * paperBox.width;
          const bottom = top + parseFloat(box.height) / 100 * paperBox.height;
          if (!rects.every(rect => { const x = rect.left + rect.width / 2, y = rect.top + rect.height / 2; return x >= left - 4 && x <= right + 4 && y >= top - 4 && y <= bottom + 4; })) continue;
        }
        const source = normalized(block.text), index = source.value.indexOf(selected);
        if (index < 0 || source.value.indexOf(selected, index + 1) >= 0) continue;
        const start = source.positions[index], end = source.positions[index + selected.length - 1] + 1;
        candidates.push({ block, text: block.text.slice(start, end), start: Array.from(block.text.slice(0, start)).length, end: Array.from(block.text.slice(0, end)).length });
      }
      if (candidates.length !== 1) { setAnchor(null); setSelectionError('不能唯一定位到一个原文段落，请缩小选区，或在结构文本中选择。'); return; }
      setAnchor(candidates[0]); setSelectionError('');
    }
    container.ownerDocument.addEventListener('mouseup', capture);
    container.ownerDocument.addEventListener('keyup', capture);
    container.ownerDocument.addEventListener('touchend', capture);
    let pending: ReturnType<typeof setTimeout> | undefined;
    function selectionChanged() { clearTimeout(pending); pending = setTimeout(() => capture(new Event('selectionchange')), 200); }
    container.ownerDocument.addEventListener('selectionchange', selectionChanged);
    return () => { clearTimeout(pending); container.ownerDocument.removeEventListener('selectionchange', selectionChanged); container.ownerDocument.removeEventListener('mouseup', capture); container.ownerDocument.removeEventListener('keyup', capture); container.ownerDocument.removeEventListener('touchend', capture); };
  }, [root, documentId, runId, page, blocks, bounds]);

  async function translate() {
    if (!anchor || !runId || readOnly || !modelConfigured || busy) return;
    resetRequest(); const current = serial.current; const abort = new AbortController(); controller.current = abort; setBusy(true);
    try {
      const value = await postJSON<Result>('/selection-translations', { document_id: documentId, parse_run_id: runId, block_id: anchor.block.id, page, start: anchor.start, end: anchor.end, source_text: anchor.text, target_language: language }, abort.signal);
      if (serial.current === current && !abort.signal.aborted) setResult(value);
    } catch (cause) { if (serial.current === current && !abort.signal.aborted) setError(cause instanceof Error ? cause.message : '选区翻译失败'); }
    finally { if (serial.current === current) setBusy(false); }
  }
  if (!anchor && !selectionError) return null;
  return <aside className="selection-translation" aria-label="选区翻译"><header><strong><Languages size={15} />选区翻译 · 第{page}页</strong><button aria-label="关闭选区翻译" onClick={() => { resetRequest(); setAnchor(null); setSelectionError(''); }}><X size={15} /></button></header>
    {selectionError ? <p role="alert">{selectionError}</p> : anchor && <><p className="selection-original">{anchor.text}</p><div className="selection-actions"><select aria-label="选区目标语言" value={language} onChange={event => { resetRequest(); setLanguage(event.target.value); }}><option value="zh">中文</option><option value="en">English</option></select><button disabled={readOnly || !modelConfigured || !runId || busy} onClick={() => void translate()}>{busy ? <LoaderCircle className="spin" size={13} /> : <Languages size={13} />}翻译选区</button><button onClick={() => onSource(anchor.block)}><Target size={13} />所属段落</button></div>
      {readOnly ? <small>只读演示不提交翻译。</small> : !modelConfigured ? <small>尚未配置模型；仅展示选区，不发送请求。</small> : <small>点击才发送此选区；最多一次模型请求，需核对上下文。</small>}
      {error && <p role="alert">{error}</p>}{result && <div className="selection-result" role="status"><p>{result.translation}</p><small>{result.status === 'cached' ? '读取已有译文' : result.status === 'preserved' ? '公式、代码、表格或引用保持原文' : '自动翻译，仍需核验'} · 原解析版本已固定</small></div>}</>}
  </aside>;
}
