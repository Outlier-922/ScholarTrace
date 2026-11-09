import { useEffect, useState } from 'react';
import { request } from './api';
import type { SourceLocation } from './types';

interface HeaderSource extends SourceLocation {
  text: string;
  start: number;
  end: number;
}

interface HeaderAuthors {
  authors: {
    source: HeaderSource;
    affiliations: { source: HeaderSource; method: string }[];
    work_notes: HeaderSource[];
  }[];
}

export default function FrontMatter({ documentId, parseRunId, status, onSource }: {
  documentId: string;
  parseRunId?: string;
  status: string;
  onSource: (source: SourceLocation) => void;
}) {
  const [result, setResult] = useState<HeaderAuthors | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    const query = parseRunId ? `?parse_run_id=${encodeURIComponent(parseRunId)}` : '';
    setError(false);
    void request<HeaderAuthors>(`/documents/${documentId}/front-matter${query}`, { signal: controller.signal })
      .then(value => { if (!controller.signal.aborted) setResult(value); })
      .catch(() => { if (!controller.signal.aborted) setError(true); });
    return () => controller.abort();
  }, [documentId, parseRunId, status]);

  return <details className="front-matter">
    <summary>作者与机构{result?.authors.length ? ` · ${result.authors.length} 个候选` : ''}</summary>
    {error ? <p role="status">暂时无法读取首页署名</p> : !result ? <p role="status">正在读取首页署名…</p>
      : !result.authors.length ? <p>当前解析未找到足够明确的首页署名。可打开 PDF 首页核对。</p>
      : <>
        <p>首页原文候选，点击定位。</p>
        <ol>{result.authors.map(author => <li key={`${author.source.block_id}-${author.source.start}`}>
          <button aria-label={`定位作者 ${author.source.text}`} onClick={() => onSource(author.source)}>
            <strong>{author.source.text}</strong>
          </button>
          {author.affiliations.length ? author.affiliations.map(({ source }) =>
            <button key={`${source.block_id}-${source.start}`} aria-label={`定位机构 ${source.text}`}
              onClick={() => onSource(source)}>{source.text}</button>)
            : <small>署名机构待核对</small>}
          {author.work_notes.map(note => <button className="front-matter-note" key={`${note.block_id}-${note.start}`}
            onClick={() => onSource(note)}>工作期间脚注 · {note.text}</button>)}
        </li>)}</ol>
      </>}
  </details>;
}
