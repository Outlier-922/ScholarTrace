import { useEffect, useRef, useState } from 'react';
import { ChevronLeft, ChevronRight, ExternalLink, FileText, Layers, LoaderCircle, Minus, Plus, ScanLine } from 'lucide-react';
import { getDocument, GlobalWorkerOptions, TextLayer } from 'pdfjs-dist';
import type { PDFDocumentProxy } from 'pdfjs-dist';
import pdfWorkerUrl from 'pdfjs-dist/build/pdf.worker.min.mjs?url';
import 'pdfjs-dist/web/pdf_viewer.css';
import type { DocumentBlock, DocumentStructure, ResearchDocument } from './types';

GlobalWorkerOptions.workerSrc = pdfWorkerUrl;

interface Props {
  document: ResearchDocument;
  structure: DocumentStructure | null;
  page: number;
  onPage: (page: number) => void;
  selectedBlock: string | null;
  onSelectBlock: (block: DocumentBlock) => void;
}

export default function PdfReader({ document, structure, page, onPage, selectedBlock, onSelectBlock }: Props) {
  const [pdf, setPdf] = useState<PDFDocumentProxy | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [zoom, setZoom] = useState(1);
  const [availableWidth, setAvailableWidth] = useState(600);
  const [pageSize, setPageSize] = useState({ width: 0, height: 0 });
  const [pageRotation, setPageRotation] = useState(0);
  const [showBlocks, setShowBlocks] = useState(false);
  const [view, setView] = useState<'pdf' | 'text'>('pdf');
  const [pageInput, setPageInput] = useState(String(page));
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const textRef = useRef<HTMLDivElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const highlightRef = useRef<HTMLButtonElement>(null);
  const pageBlocks = structure?.blocks.filter((block) => block.page === page) ?? [];
  const pageTotal = pdf?.numPages || document.page_count || 1;
  const pageStatus = structure?.pages.find((item) => item.number === page)?.status;

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
    if (!pdf || view !== 'pdf') return;
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
    if (!loading && selectedBlock && view === 'pdf') {
      highlightRef.current?.scrollIntoView({ behavior: 'smooth', block: 'center', inline: 'nearest' });
    }
  }, [selectedBlock, page, loading, view]);

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
      <div className="pdf-scroll" ref={scrollRef}>
        {error ? <div className="reader-error"><FileText size={28} /><h3>原文暂时无法显示</h3><p>{error}</p><a href={`/api/documents/${document.id}/pdf`} target="_blank" rel="noreferrer">直接打开 PDF</a></div> : view === 'pdf' ? (
          <>
            {loading && <div className="pdf-loading"><LoaderCircle className="spin" size={23} /><span>正在渲染原文</span></div>}
            <div className="pdf-paper" style={pageSize.width ? { width: pageSize.width, height: pageSize.height } : undefined}>
              <canvas ref={canvasRef} />
              <div className="textLayer" ref={textRef} />
              <div className={`block-overlays ${showBlocks ? 'show-all' : ''}`}>
                {pageBlocks.filter((block) => showBlocks || block.id === selectedBlock).map((block) => (
                  <button
                    key={block.id}
                    ref={block.id === selectedBlock ? highlightRef : undefined}
                    className={`block-highlight ${block.id === selectedBlock ? 'selected' : ''}`}
                    title={block.text.slice(0, 160)}
                    aria-label={`定位内容：${block.text.slice(0, 60)}`}
                    style={displayBounds(block.bbox)}
                    onClick={() => onSelectBlock(block)}
                  />
                ))}
              </div>
            </div>
            {pageStatus === 'needs_ocr' && <div className="scan-note"><ScanLine size={16} />当前页缺少可用文字层，OCR 将在下一阶段接入；原始图像可正常阅读。</div>}
          </>
        ) : (
          <div className="structured-page">
            <div className="structured-page-label"><Layers size={15} /> 第 {page} 页 · 解析文本</div>
            {pageBlocks.length ? pageBlocks.map((block) => (
              <button className={`text-block ${block.id === selectedBlock ? 'active' : ''} ${block.type === 'heading' ? 'heading' : ''}`} key={block.id} onClick={() => onSelectBlock(block)}>
                <span className="text-block-type">{block.type === 'heading' ? '标题' : block.type === 'reference' ? '参考文献' : block.type === 'table' ? '表格候选' : '正文'}</span>
                <span>{block.text}</span>
              </button>
            )) : <div className="text-empty"><ScanLine size={26} /><p>{pageStatus === 'needs_ocr' ? '当前页需要 OCR，尚无可检索文本。' : '当前页尚无解析内容，请等待处理完成。'}</p></div>}
          </div>
        )}
      </div>
      <footer className="reader-footer"><span className="status-dot" /> 原始文件保真呈现<span className="footer-end">{pageBlocks.length} 个内容块 · 第 {page} 页</span></footer>
    </section>
  );
}
