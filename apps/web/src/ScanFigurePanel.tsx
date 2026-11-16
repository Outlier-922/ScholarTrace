import { useEffect, useState } from 'react';
import { request } from './api';
import type { DocumentBlock } from './types';
import './scan-figures.css';

type Bbox = DocumentBlock['bbox'];

interface Figure {
  id: string;
  number: string;
  text: string;
  caption_bbox: Bbox;
  figure_bbox: Bbox | null;
}

interface PageFigures {
  status: 'ready' | 'needs_ocr' | 'not_scan';
  items: Figure[];
}

interface Props {
  documentId: string;
  runId: string;
  page: number;
  onSource: (bbox: Bbox, label: string) => void;
}

export default function ScanFigurePanel({ documentId, runId, page, onSource }: Props) {
  const [result, setResult] = useState<PageFigures | null>(null);
  const [error, setError] = useState('');
  const route = `/documents/${documentId}/scan-figures`;
  const query = `?page=${page}&parse_run_id=${encodeURIComponent(runId)}`;

  useEffect(() => {
    const abort = new AbortController();
    void request<PageFigures>(route + query, { signal: abort.signal })
      .then(data => { if (!abort.signal.aborted) setResult(data); })
      .catch(cause => {
        if (!abort.signal.aborted) setError(cause instanceof Error ? cause.message : String(cause));
      });
    return () => abort.abort();
  }, [route, query]);

  return <section className="scan-figure-panel" aria-label="扫描图与图注">
    <h3>扫描图与图注</h3>
    {error ? <p role="alert">{error}</p>
      : !result ? <p role="status">正在读取第 {page} 页的图注…</p>
      : result.status === 'needs_ocr' ? <p>请先为扫描件运行本机 OCR，以读取图注。</p>
      : result.status === 'not_scan' ? <p>请切换到扫描页查看图注配对。</p>
      : !result.items.length ? <p>第 {page} 页没有识别到带编号的图注。</p>
      : result.items.map(figure => {
        const preview = `/api${route}/${encodeURIComponent(figure.id)}/image${query}`;
        const image = figure.figure_bbox;
        return <figure key={figure.id}>
          {image && <img src={preview} alt={`图 ${figure.number} 的 PDF 原图裁剪`} loading="lazy" />}
          <figcaption>{figure.text}</figcaption>
          <small>{image ? '自动配对候选 · 核对时请查看原页' : '图注已识别，附近图像区域待确认。'}</small>
          <div className="scan-figure-actions">
            {image && <>
              <button onClick={() => onSource(image, `图 ${figure.number} 原图区域`)}>定位原图</button>
              <a href={preview} download={`figure-${page}-${figure.number}.png`}>下载原图裁剪</a>
            </>}
            <button onClick={() => onSource(figure.caption_bbox, `图 ${figure.number} 图注`)}>定位图注</button>
          </div>
        </figure>;
      })}
  </section>;
}
