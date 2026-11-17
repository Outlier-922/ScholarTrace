import { lazy, Suspense, useEffect, useRef, useState } from 'react';
import type { CSSProperties } from 'react';
import { ArrowLeft, ChevronLeft, ChevronRight, ExternalLink, FileText, Layers, Link2, LoaderCircle, Minus, Plus, ScanLine, Search, Target, TextSelect } from 'lucide-react';
import { getDocument, GlobalWorkerOptions, TextLayer } from 'pdfjs-dist';
import type { PDFDocumentProxy } from 'pdfjs-dist';
import pdfWorkerUrl from 'pdfjs-dist/build/pdf.worker.min.mjs?url';
import './pdf-text-layer.css';
import type { DocumentBlock, DocumentStructure, ResearchDocument, Evidence, SourceLocation, TableReference } from './types';
import BlockContent, { isOcrBlock } from './BlockContent';
import TranslationPane from './TranslationPane';
import SelectionTranslation from './SelectionTranslation';
const FormulaPanel = lazy(() => import('./FormulaPanel'));
const TablePanel = lazy(() => import('./TablePanel'));
const ScanFigurePanel = lazy(() => import('./ScanFigurePanel'));
import type { TranslationHandle } from './TranslationPane';
import { request } from './api';
import ReferenceDiscovery from './ReferenceDiscovery';

interface CitationSource { page: number; block_id: string; bbox: DocumentBlock['bbox']; line_bbox?: DocumentBlock['bbox']; text?: string; context?: string; }
interface CitationLink extends CitationSource { id: string; marker_text: string; offset: number; reference_ids: string[]; context: string; }
interface ReferenceEntry extends CitationSource { id: string; marker: string | null; text: string; backlinks: CitationLink[]; source_spans?: CitationSource[]; }
interface CitationMap { parse_run_id: string; links: CitationLink[]; references: ReferenceEntry[]; }

GlobalWorkerOptions.workerSrc = pdfWorkerUrl;

interface Props {
  document: ResearchDocument;
  structure: DocumentStructure | null;
  page: number;
  onPage: (page: number) => void;
  selectedBlock: string | null;
  onSelectBlock: (block: DocumentBlock) => void;
  ocrReady?: boolean;
  onRequestReprocess?: () => void;
  modelConfigured?: boolean;
  readOnly?: boolean;
  sourceEvidence?: SourceLocation | Evidence | null;
  onUseTable:(reference:TableReference)=>void;
  onFindReference?: (text: string) => void;
  onOpenReferenceSearch?: (id: string) => void;
}

export default function PdfReader({ document, structure, page, onPage, selectedBlock, onSelectBlock, ocrReady = false, onRequestReprocess, modelConfigured = false, readOnly = false, onFindReference, onOpenReferenceSearch, sourceEvidence, onUseTable }: Props) {
  const [regionTarget,setRegionTarget]=useState<{bbox:DocumentBlock['bbox'];label:string}|null>(null);
  const [formulaTarget,setFormulaTarget]=useState<{bbox:DocumentBlock['bbox'];page:number;documentId:string;runId:string|undefined}|null>(null);
  useEffect(()=>setRegionTarget(null),[document.id,structure?.parse_run_id,page,selectedBlock]);
  const [pdf, setPdf] = useState<PDFDocumentProxy | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [zoom, setZoom] = useState(1);
  const [availableWidth, setAvailableWidth] = useState(600);
  const [pageSize, setPageSize] = useState({ width: 0, height: 0 });
  const [pageRotation, setPageRotation] = useState(0);
  const [showBlocks, setShowBlocks] = useState(false);
  const [view, setView] = useState<'pdf' | 'text' | 'references' | 'parallel' | 'formulas' | 'tables' | 'figures'>('pdf');
  useEffect(()=>{
    if(!sourceEvidence){setRegionTarget(null);return;}
    const matches=sourceEvidence.document_id===document.id&&sourceEvidence.page===page&&sourceEvidence.parse_run_id===structure?.parse_run_id&&sourceEvidence.block_id===selectedBlock;
    if(!matches){setRegionTarget(null);return;}
    if ('source_kind' in sourceEvidence && (sourceEvidence.source_kind === 'table_cell' || sourceEvidence.source_kind === 'table_draft')) {
      setRegionTarget({bbox:sourceEvidence.bbox,label:sourceEvidence.table_cell?`问答表格第${sourceEvidence.table_cell.row+1}行第${sourceEvidence.table_cell.column+1}列`:'手工稿对应原页（未核验）'});
    }
    else if (sourceEvidence.ocr_line_bbox) {
      setRegionTarget({bbox:sourceEvidence.ocr_line_bbox,label:'首页署名原文'});
    }
    else setRegionTarget(null);
    setView('pdf');
  },[sourceEvidence,document.id,structure?.parse_run_id,page,selectedBlock]);
  const [citations, setCitations] = useState<CitationMap | null>(null);
  const [citationError, setCitationError] = useState('');
  const [activeCitation, setActiveCitation] = useState<CitationLink | null>(null);
  const [hotspots, setHotspots] = useState<{ link: CitationLink; left: number; top: number; width: number; height: number }[]>([]);
  const [readingHistory, setReadingHistory] = useState<CitationLink[]>([]);
  const [citationFocus, setCitationFocus] = useState<{ source: CitationSource; run: string | undefined; label: string } | null>(null);
  const focusedRegion = citationFocus && citationFocus.source.line_bbox && citationFocus.run === structure?.parse_run_id &&
    citationFocus.source.page === page && citationFocus.source.block_id === selectedBlock
      ? { bbox: citationFocus.source.line_bbox, label: citationFocus.label } : null;
  const formulaRegion = formulaTarget?.documentId === document.id && formulaTarget.runId === structure?.parse_run_id &&
    formulaTarget.page === page ? { bbox:formulaTarget.bbox, label:'公式选区' } : null;
  const visibleRegion = focusedRegion || regionTarget || formulaRegion;
  const [linked, setLinked] = useState(true);
  const translationRef = useRef<TranslationHandle>(null);
  const suppressUntil = useRef(0);
  const [ocrSelection, setOcrSelection] = useState(true);
  const [pageInput, setPageInput] = useState(String(page));
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const textRef = useRef<HTMLDivElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const highlightRef = useRef<HTMLButtonElement>(null);
  const pageBlocks = structure?.blocks.filter((block) => block.page === page) ?? [];
  const pageTotal = pdf?.numPages || document.page_count || 1;
  const pageRecord = structure?.pages.find((item) => item.number === page);
  const pageStatus = pageRecord?.status;
  const ocrBlocks = pageBlocks.filter((block) => isOcrBlock(block) && block.text.trim());
  const ocrLines = ocrBlocks.flatMap(block => {
    const lines = block.metadata?.lines?.length ? block.metadata.lines : [{ text: block.text, bbox: block.bbox }];
    let cursor = 0;
    return lines.map((line, index) => {
      const start = block.text.indexOf(line.text, cursor);
      if (start >= 0) cursor = start + line.text.length;
      return { ...line, id: `${block.id}_${index}`, blockId: block.id,
        offset: start >= 0 ? Array.from(block.text.slice(0, start)).length : undefined };
    });
  });

  useEffect(() => {
    setCitations(null); setCitationError(''); setReadingHistory([]); setActiveCitation(null); setCitationFocus(null);
    if (!structure?.parse_run_id || (!['ready', 'partial'].includes(document.status) && structure.parse_run_id === document.parse_run_id)) return;
    const controller = new AbortController();
    void request<CitationMap>(`/documents/${document.id}/citations?parse_run_id=${encodeURIComponent(structure.parse_run_id)}`, { signal: controller.signal }).then(result => {
      if (!controller.signal.aborted) setCitations(result);
    }).catch(() => { if (!controller.signal.aborted) setCitationError('引用映射暂不可用'); });
    return () => controller.abort();
  }, [document.id, structure?.parse_run_id, document.status]);

  useEffect(() => setPageInput(String(page)), [page]);

  useEffect(() => {
    const target = scrollRef.current;
    if (!target) return;
    const observer = new ResizeObserver(([entry]) => {
      setAvailableWidth(Math.max(220, entry.contentRect.width - 48));
    });
    observer.observe(target);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    setPdf(null);
    setLoading(true);
    setError('');
    setPageSize({ width: 0, height: 0 });
    const task = getDocument({ url: `/api/documents/${document.id}/pdf` });
    let cancelled = false;
    task.promise.then((loaded) => {
      if (!cancelled) setPdf(loaded);
    }).catch((cause: Error) => {
      if (!cancelled) { setError(cause.message || '无法加载 PDF'); setLoading(false); }
    });
    return () => { cancelled = true; void task.destroy(); };
  }, [document.id]);

  useEffect(() => {
    if (!pdf || (view !== 'pdf' && view !== 'parallel')) return;
    let cancelled = false;
    let renderTask: ReturnType<Awaited<ReturnType<PDFDocumentProxy['getPage']>>['render']> | undefined;
    let textLayer: TextLayer | undefined;
    setLoading(true);
    setError('');
    void (async () => {
      try {
        const pdfPage = await pdf.getPage(Math.max(1, Math.min(page, pdf.numPages)));
        if (cancelled || !canvasRef.current || !textRef.current) return;
        const natural = pdfPage.getViewport({ scale: 1 });
        setPageRotation(pdfPage.rotate);
        const scale = (Math.min(availableWidth, 820) / natural.width) * zoom;
        const viewport = pdfPage.getViewport({ scale });
        const canvas = canvasRef.current;
        const context = canvas.getContext('2d');
        if (!context) throw new Error('浏览器不支持 PDF 画布');
        const ratio = window.devicePixelRatio || 1;
        canvas.width = Math.floor(viewport.width * ratio);
        canvas.height = Math.floor(viewport.height * ratio);
        canvas.style.width = `${viewport.width}px`;
        canvas.style.height = `${viewport.height}px`;
        setPageSize({ width: viewport.width, height: viewport.height });
        textRef.current.replaceChildren();
        textRef.current.style.setProperty('--scale-factor', String(viewport.scale));
        textRef.current.style.setProperty('--user-unit', String(pdfPage.userUnit));
        textRef.current.style.setProperty('--total-scale-factor', String(viewport.scale * pdfPage.userUnit));
        textRef.current.style.setProperty('--scale-round-x', '1px');
        textRef.current.style.setProperty('--scale-round-y', '1px');
        renderTask = pdfPage.render({
          canvas,
          canvasContext: context,
          viewport,
          transform: ratio !== 1 ? [ratio, 0, 0, ratio, 0, 0] : undefined,
        });
        await renderTask.promise;
        if (cancelled || !textRef.current) return;
        const content = await pdfPage.getTextContent();
        if (cancelled || !textRef.current) return;
        textLayer = new TextLayer({ textContentSource: content, container: textRef.current, viewport });
        await textLayer.render();
        if (!cancelled) setLoading(false);
      } catch (cause) {
        if (!cancelled && !(cause instanceof Error && cause.name === 'RenderingCancelledException')) {
          setError(cause instanceof Error ? cause.message : '当前页面渲染失败');
          setLoading(false);
        }
      }
    })();
    return () => {
      cancelled = true;
      renderTask?.cancel();
      textLayer?.cancel();
    };
  }, [pdf, page, availableWidth, zoom, view]);

  useEffect(() => {
    if (!loading && (selectedBlock || visibleRegion) && (view === 'pdf' || view === 'parallel')) {
      highlightRef.current?.scrollIntoView({ behavior: 'smooth', block: 'center', inline: 'nearest' });
    }
  }, [selectedBlock, regionTarget, citationFocus, formulaTarget, page, loading, view]);

  useEffect(() => {
    if (loading || !citations || (view !== 'pdf' && view !== 'parallel')) { setHotspots([]); return; }
    const paper = canvasRef.current?.parentElement;
    const layer = ocrBlocks.length ? paper?.querySelector('.ocr-selection-layer') : textRef.current;
    if (!paper || !layer) return;
    const paperBox = paper.getBoundingClientRect();
    const next: typeof hotspots = [];
    const pageLinks = citations.links.filter(item => item.page === page);
    const addHotspot = (link: CitationLink, rect: DOMRect) => {
      if (rect.width > 0 && rect.height > 0) next.push({ link, left: rect.left - paperBox.left, top: rect.top - paperBox.top, width: rect.width, height: rect.height });
    };
    if (ocrBlocks.length) {
      const byOffset = new Map(pageLinks.map(link => [`${link.block_id}:${link.offset}`, link]));
      for (const span of layer.querySelectorAll<HTMLElement>('[data-ocr-offset]')) {
        const node = span.firstChild;
        const text = node?.textContent;
        if (!node || !text) continue;
        const start = Number(span.dataset.ocrOffset);
        for (const match of text.matchAll(/\[\d{1,4}(?:\s*[,;–—-]\s*\d{1,4})*\]/g)) {
          const offset = start + Array.from(text.slice(0, match.index)).length;
          const link = byOffset.get(`${span.dataset.ocrBlock}:${offset}`);
          if (link?.marker_text !== match[0]) continue;
          const range = layer.ownerDocument.createRange();
          range.setStart(node, match.index);
          range.setEnd(node, match.index + match[0].length);
          addHotspot(link, range.getBoundingClientRect());
        }
      }
      setHotspots(next);
      return;
    }
    const walker = layer.ownerDocument.createTreeWalker(layer, NodeFilter.SHOW_TEXT);
    const nodes: { node: Node; start: number; end: number }[] = [];
    let content = ''; let node = walker.nextNode();
    while (node) { const text = node.textContent || ''; nodes.push({ node, start: content.length, end: content.length + text.length }); content += text; node = walker.nextNode(); }
    for (const match of content.matchAll(/\[\d{1,4}(?:\s*[,;–—-]\s*\d{1,4})*\]/g)) {
      const first = nodes.find(item => item.end > match.index!); const last = nodes.find(item => item.end >= match.index! + match[0].length);
      if (!first || !last) continue;
      const range = layer.ownerDocument.createRange(); range.setStart(first.node, match.index! - first.start); range.setEnd(last.node, match.index! + match[0].length - last.start);
      const rect = range.getBoundingClientRect(); const x = (rect.left + rect.width / 2 - paperBox.left) / paperBox.width; const y = (rect.top + rect.height / 2 - paperBox.top) / paperBox.height;
      const link = pageLinks.find(item => { const bounds = displayBounds(item.line_bbox || item.bbox); return item.marker_text.replace(/\s/g, '') === match[0].replace(/\s/g, '') && x >= parseFloat(bounds.left) / 100 - .01 && x <= (parseFloat(bounds.left) + parseFloat(bounds.width)) / 100 + .01 && y >= parseFloat(bounds.top) / 100 - .01 && y <= (parseFloat(bounds.top) + parseFloat(bounds.height)) / 100 + .01; });
      if (link) addHotspot(link, rect);
    }
    setHotspots(next);
  }, [loading, citations, page, view, zoom, pageSize.width, pageRotation, ocrSelection, structure]);

  function focusCitation(source: CitationSource, label: string) {
    setCitationFocus({ source, label, run: structure?.parse_run_id });
    onSelectBlock({ id: source.block_id, page: source.page, bbox: source.bbox, text: source.text || source.context || '', type: 'paragraph', order: 0 });
    setView('pdf');
  }
  function jumpReference(link: CitationLink, targetId?: string) {
    if (!targetId && link.reference_ids.length > 1) { setActiveCitation(link); return; }
    const target = citations?.references.find(item => item.id === (targetId || link.reference_ids[0]));
    if (!target) return;
    setActiveCitation(null);
    setReadingHistory(current => [...current, link]);
    focusCitation(target, `参考文献 [${target.marker}]`);
  }
  function returnToCitation() {
    const target = readingHistory.at(-1);
    if (!target) return;
    setReadingHistory(current => current.slice(0, -1));
    focusCitation(target, `正文引用 ${target.marker_text}`);
  }
  function syncFromPdf() {
    if (view !== 'parallel' || !linked || performance.now() < suppressUntil.current || !scrollRef.current || !pageSize.height) return;
    const root = scrollRef.current; const paperTop = (canvasRef.current?.parentElement?.getBoundingClientRect().top || root.getBoundingClientRect().top) - root.getBoundingClientRect().top + root.scrollTop; const position = root.scrollTop + root.clientHeight * .25 - paperTop;
    const blocks = pageBlocks.filter(block => block.text.trim()).map(block => ({ block, bounds: displayBounds(block.bbox) })).sort((a, b) => parseFloat(a.bounds.top) - parseFloat(b.bounds.top));
    const item = blocks.find(item => (parseFloat(item.bounds.top) + parseFloat(item.bounds.height)) / 100 * pageSize.height >= position) || blocks.at(-1);
    if (item) translationRef.current?.scrollToBlock(item.block.id, Math.max(0, Math.min(1, (position - parseFloat(item.bounds.top) / 100 * pageSize.height) / Math.max(1, parseFloat(item.bounds.height) / 100 * pageSize.height))));
  }
  function syncFromTranslation(id: string, ratio: number) { const block = pageBlocks.find(item => item.id === id); if (!block || !scrollRef.current) return; const bounds = displayBounds(block.bbox); const paperTop = (canvasRef.current?.parentElement?.getBoundingClientRect().top || scrollRef.current.getBoundingClientRect().top) - scrollRef.current.getBoundingClientRect().top + scrollRef.current.scrollTop; suppressUntil.current = performance.now() + 250; scrollRef.current.scrollTo({ top: paperTop + (parseFloat(bounds.top) + parseFloat(bounds.height) * ratio) / 100 * pageSize.height - scrollRef.current.clientHeight * .25, behavior: 'auto' }); }

  function submitPage() {
    const value = Number(pageInput);
    if (Number.isFinite(value)) onPage(Math.max(1, Math.min(pageTotal, Math.round(value))));
    else setPageInput(String(page));
  }

  function displayBounds(box: DocumentBlock['bbox']) {
    const rotation = ((pageRotation % 360) + 360) % 360;
    const points = [[box[0], box[1]], [box[2], box[1]], [box[0], box[3]], [box[2], box[3]]].map(([x, y]) => {
      if (rotation === 90) return [1 - y, x];
      if (rotation === 180) return [1 - x, 1 - y];
      if (rotation === 270) return [y, 1 - x];
      return [x, y];
    });
    const left = Math.min(...points.map(([x]) => x));
    const top = Math.min(...points.map(([, y]) => y));
    const right = Math.max(...points.map(([x]) => x));
    const bottom = Math.max(...points.map(([, y]) => y));
    return { left: `${left * 100}%`, top: `${top * 100}%`, width: `${Math.max(0.005, right - left) * 100}%`, height: `${Math.max(0.005, bottom - top) * 100}%` };
  }

  function ocrLineStyle(box: DocumentBlock['bbox'], text: string): CSSProperties {
    const rotation = ((pageRotation % 360) + 360) % 360;
    const [x, y] = rotation === 90 ? [1 - box[1], box[0]] : rotation === 180 ? [1 - box[0], 1 - box[1]] : rotation === 270 ? [box[1], 1 - box[0]] : [box[0], box[1]];
    const width = (box[2] - box[0]) * (rotation % 180 ? pageSize.height : pageSize.width);
    const height = (box[3] - box[1]) * (rotation % 180 ? pageSize.width : pageSize.height);
    return { left: `${x * 100}%`, top: `${y * 100}%`, width: `${width}px`, height: `${height}px`, fontSize: `${Math.max(2, Math.min(48, height / Math.max(1, text.split('\n').length) * 0.82))}px`, transform: `rotate(${rotation}deg)` };
  }

  return (
    <section className="reader" aria-label="论文阅读器">
      <div className="reader-title">
        <div className="reader-file-icon"><FileText size={18} /></div>
        <div className="reader-title-text">
          <h2 title={document.title}>{document.title || document.filename}</h2>
          <p>{document.version_label || '未标注版本'}<span>·</span>{document.page_count || '—'} 页<span>·</span>原文阅读</p>
        </div>
        <a className="icon-button" href={`/api/documents/${document.id}/pdf`} target="_blank" rel="noreferrer" title="在新标签打开原 PDF" aria-label="打开原 PDF"><ExternalLink size={16} /></a>
      </div>
      <div className="reader-toolbar">
        <div className="segmented-control small">
          <button className={view === 'pdf' ? 'active' : ''} onClick={() => setView('pdf')}>原始 PDF</button>
          <button className={view === 'text' ? 'active' : ''} onClick={() => setView('text')}>结构文本</button>
          <button className={view === 'references' ? 'active' : ''} onClick={() => setView('references')}>引用</button>
          <button className={view === 'parallel' ? 'active' : ''} onClick={() => setView('parallel')}>中英对照</button>
          <button className={view === 'tables' ? 'active' : ''} onClick={() => setView('tables')}>表格</button>
          <button className={view === 'formulas' ? 'active' : ''} onClick={() => setView('formulas')}>公式</button>
          {(pageStatus === 'ocr_ready' || pageStatus === 'needs_ocr' || view === 'figures') &&
            <button className={view === 'figures' ? 'active' : ''} onClick={() => setView('figures')}>图与图注</button>}
        </div>
        <div className="page-controls">
          <button className="icon-button" disabled={page <= 1} onClick={() => onPage(page - 1)} title="上一页" aria-label="上一页"><ChevronLeft size={16} /></button>
          <label><input aria-label="页码" value={pageInput} onChange={(event) => setPageInput(event.target.value)} onBlur={submitPage} onKeyDown={(event) => { if (event.key === 'Enter') submitPage(); }} /><span>/ {pageTotal}</span></label>
          <button className="icon-button" disabled={page >= pageTotal} onClick={() => onPage(page + 1)} title="下一页" aria-label="下一页"><ChevronRight size={16} /></button>
        </div>
        <div className="zoom-controls">
          <button className="icon-button" disabled={zoom <= 0.6 || view === 'text'} onClick={() => setZoom((value) => Math.max(0.6, value - 0.15))} aria-label="缩小"><Minus size={15} /></button>
          <span>{Math.round(zoom * 100)}%</span>
          <button className="icon-button" disabled={zoom >= 2 || view === 'text'} onClick={() => setZoom((value) => Math.min(2, value + 0.15))} aria-label="放大"><Plus size={15} /></button>
        </div>
        <button className={`icon-button block-toggle ${showBlocks ? 'is-on' : ''}`} disabled={view === 'text'} onClick={() => setShowBlocks(!showBlocks)} title="显示解析内容块" aria-label="显示解析内容块" aria-pressed={showBlocks}><ScanLine size={17} /></button>
      </div>
      <SelectionTranslation root={scrollRef} documentId={document.id} runId={structure?.parse_run_id} page={page} blocks={pageBlocks} modelConfigured={modelConfigured} readOnly={readOnly} bounds={displayBounds} onSource={block => { onSelectBlock(block); setView('pdf'); }} />
      {readingHistory.length > 0 && <div className="citation-return"><button onClick={returnToCitation}><ArrowLeft size={13} />返回正文引用处</button></div>}
      {activeCitation && <div className="citation-picker" role="group" aria-label="选择组合引用中的条目"><span>{activeCitation.marker_text} · 选择条目</span>{citations?.references.filter(item => activeCitation.reference_ids.includes(item.id)).map(item => <button key={item.id} title={item.text} onClick={() => jumpReference(activeCitation, item.id)}>[{item.marker}] {item.text.replace(/^\[\d+\]\s*/, '').slice(0, 55)}…</button>)}<button onClick={() => setActiveCitation(null)}>关闭</button></div>}
      {view === 'parallel' && <div className="parallel-options"><label><input type="checkbox" checked={linked} onChange={event => setLinked(event.target.checked)} />按段落联动滚动</label><span>公式和代码保留原文；自动翻译请核验。</span></div>}
      {ocrBlocks.length > 0 && <div className="ocr-reader-bar"><span><ScanLine size={13} />本页含 OCR 文字 · {ocrBlocks.length} 个区域</span><button className={ocrSelection ? 'active' : ''} onClick={() => setOcrSelection(!ocrSelection)} aria-pressed={ocrSelection}><TextSelect size={13} />{ocrSelection ? '文字选择已开启' : '开启文字选择'}</button></div>}
      <div className={`reader-split ${view === 'parallel' ? 'is-parallel' : ''}`}><div className="pdf-scroll" ref={scrollRef} onScroll={syncFromPdf} onWheel={() => { suppressUntil.current = 0; }} onPointerDown={() => { suppressUntil.current = 0; }}>
        {view === 'figures' && structure?.parse_run_id ?
          <Suspense fallback={<p>正在加载图注…</p>}>
            <ScanFigurePanel key={`${document.id}-${structure.parse_run_id}-${page}`}
              documentId={document.id} runId={structure.parse_run_id} page={page}
              onSource={(bbox, label) => { setRegionTarget({bbox, label}); setView('pdf'); }} />
          </Suspense> : view === 'tables' && structure?.parse_run_id ? <Suspense fallback={<p>正在加载表格工具…</p>}><TablePanel title={document.title} onUse={onUseTable} documentId={document.id} runId={structure.parse_run_id} page={page} readOnly={readOnly} onSource={(bbox,label)=>{setRegionTarget({bbox,label});setView('pdf');}} /></Suspense> : view === 'formulas' && structure?.parse_run_id ? <Suspense fallback={<p>正在加载公式工具…</p>}><FormulaPanel documentId={document.id} runId={structure.parse_run_id} page={page} pageTotal={pageTotal} rotation={pageRecord?.rotation} modelConfigured={modelConfigured} readOnly={readOnly}
              onRegion={(bbox, sourcePage = page) => {
                setRegionTarget(null); setCitationFocus(null);
                setFormulaTarget({bbox, page: sourcePage, documentId: document.id, runId: structure?.parse_run_id});
                if (sourcePage !== page) onPage(sourcePage);
                setView('pdf');
              }}
              onSource={block => { setFormulaTarget(null); onPage(block.page); onSelectBlock(block); setView('pdf'); }} /></Suspense>  : view === 'references' ? <div className="reference-list">
          <h3>References 原文条目</h3>
          {structure?.parse_run_id && onOpenReferenceSearch && <ReferenceDiscovery documentId={document.id} runId={structure.parse_run_id} readOnly={readOnly} onOpen={onOpenReferenceSearch} />}
          <p>点击数字引用可双向跳转。扫描条目请对照原图。</p>
          {citationError && <p role="alert">{citationError}</p>}
          {citations?.references.map(reference => {
            const continuation = new Map<number, CitationSource>();
            for (const part of reference.source_spans ?? []) {
              if (part.page !== reference.page && !continuation.has(part.page)) continuation.set(part.page, part);
            }
            return <article key={reference.id}>
              <strong>{reference.marker ? `[${reference.marker}]` : '未编号条目'}</strong>
              <p>{reference.text}</p>
              {onFindReference && <button onClick={() => onFindReference(reference.text)}><Search size={12} />查找书目</button>}
              <button onClick={() => focusCitation(reference, `参考文献 [${reference.marker}]`)}><Target size={12} />原文条目 · 第{reference.page}页</button>
              {[...continuation.values()].map(part => <button key={part.page} onClick={() => focusCitation({ ...part, line_bbox: part.bbox }, '参考文献续文')}><Target size={12} />原文续页 · 第{part.page}页</button>)}
              <div>{reference.backlinks.map(link => <button key={link.id} title={link.context} onClick={() => focusCitation(link, `正文引用 ${link.marker_text}`)}>正文引用 · 第{link.page}页</button>)}</div>
            </article>;
          })}
          {citations && !citations.references.length && <p>当前解析未识别出参考文献条目。</p>}
        </div> : error ? <div className="reader-error"><FileText size={28} /><h3>原文暂时无法显示</h3><p>{error}</p><a href={`/api/documents/${document.id}/pdf`} target="_blank" rel="noreferrer">直接打开 PDF</a></div> : (view === 'pdf' || view === 'parallel') ? (
          <>
            {loading && <div className="pdf-loading"><LoaderCircle className="spin" size={23} /><span>正在渲染原文</span></div>}
            <div className="pdf-paper" style={pageSize.width ? { width: pageSize.width, height: pageSize.height } : undefined}>
              <canvas ref={canvasRef} />
              <div className="textLayer" ref={textRef} />
              {ocrLines.length > 0 && <div className="ocr-selection-layer" aria-label="OCR 识别文字层" aria-hidden={!ocrSelection} style={ocrSelection ? undefined : { visibility: 'hidden' }}>{ocrLines.map((line) => <span key={line.id} className="ocr-selection-text" data-ocr-block={line.blockId} data-ocr-offset={line.offset} style={ocrLineStyle(line.bbox, line.text)} title="OCR 识别文字，可选中复制；请对照原图核验">{line.text}</span>)}</div>}
              <div className={`block-overlays ${showBlocks ? 'show-all' : ''}`}>
                {pageBlocks.filter((block) => showBlocks || !visibleRegion && block.id === selectedBlock).map((block) => (
                  <button
                    key={block.id}
                    ref={block.id === selectedBlock ? highlightRef : undefined}
                    className={`block-highlight ${!visibleRegion && block.id === selectedBlock ? 'selected' : ''}`}
                    title={block.text.slice(0, 160)}
                    aria-label={`定位内容：${block.text.slice(0, 60)}`}
                    style={displayBounds(block.bbox)}
                    onClick={() => onSelectBlock(block)}
                  />
                ))}
                {visibleRegion && <button ref={highlightRef} className="block-highlight selected" title={visibleRegion.label} aria-label={visibleRegion.label} style={displayBounds(visibleRegion.bbox)} />}
              </div>
              <div className="citation-links-layer">{hotspots.map((spot, index) => <button key={`${spot.link.id}_${index}`} style={{ left: spot.left, top: spot.top, width: spot.width, height: spot.height }} onClick={() => jumpReference(spot.link)} title={`跳转至References ${spot.link.marker_text}`} aria-label={`跳转到参考文献 ${spot.link.marker_text}`} />)}</div>
            </div>
            {pageStatus === 'needs_ocr' && <div className="scan-note"><ScanLine size={16} /><span>当前页缺少可用文字层。{ocrReady ? '可重新解析，启用本地 OCR。' : 'OCR 模型尚未就绪，原始图像仍可阅读。'}</span>{ocrReady && onRequestReprocess && <button onClick={onRequestReprocess}>识别扫描文字</button>}</div>}
          </>
        ) : (
          <div className="structured-page">
            <div className="structured-page-label"><Layers size={15} /> 第 {page} 页 · 解析文本</div>
            {pageBlocks.length ? pageBlocks.map((block) => <BlockContent key={block.id} block={block} selected={block.id === selectedBlock} onSelect={() => { onSelectBlock(block); setView('pdf'); }} />) : <div className="text-empty"><ScanLine size={26} /><p>{pageStatus === 'needs_ocr' ? '当前页需要 OCR，尚无可检索文本。' : '当前页尚无解析内容，请等待处理完成。'}</p></div>}
          </div>
        )}
      </div>{view === 'parallel' && <TranslationPane ref={translationRef} document={document} structure={structure} page={page} selectedBlock={selectedBlock} modelConfigured={modelConfigured} linked={linked} onAnchor={syncFromTranslation} onSource={block => { suppressUntil.current = performance.now() + 300; onSelectBlock(block); }} />}</div>
      <footer className="reader-footer"><span className="status-dot" /> 原始文件保真呈现<span className="footer-end">{hotspots.length > 0 && <><Link2 size={10} />{hotspots.length}处引用 · </>}{pageBlocks.length} 个内容块 · 第 {page} 页</span></footer>
    </section>
  );
}
