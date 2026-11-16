import { useRef, useState, type PointerEvent } from 'react';
import type { DocumentBlock } from './types';
import './region-picker.css';

type Region = DocumentBlock['bbox'];

export default function RegionPicker({ url, region, onChange, disabled, header, onPickHeader }: {
  url: string;
  region: Region | null;
  onChange: (region: Region | null) => void;
  disabled: boolean;
  header?: number;
  onPickHeader?: (y: number) => void;
}) {
  const start = useRef<{x: number; y: number; id: number} | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [failed, setFailed] = useState(false);

  function point(event: PointerEvent<HTMLDivElement>) {
    const bounds = event.currentTarget.getBoundingClientRect();
    const clamp = (n: number) => Math.max(0, Math.min(1, n));
    return { x: clamp((event.clientX - bounds.left) / bounds.width), y: clamp((event.clientY - bounds.top) / bounds.height) };
  }

  function move(event: PointerEvent<HTMLDivElement>) {
    if (start.current?.id !== event.pointerId) return;
    const { x, y } = point(event);
    const first = start.current;
    onChange([Math.min(first.x, x), Math.min(first.y, y), Math.max(first.x, x), Math.max(first.y, y)]);
  }

  return <>
    <div className="pdf-region-picker" onPointerDown={event => {
      if (!loaded || disabled || !event.isPrimary || event.button !== 0 || onPickHeader) return;
      start.current = { ...point(event), id: event.pointerId };
      event.currentTarget.setPointerCapture(event.pointerId);
      onChange(null);
    }} onPointerMove={move} onPointerUp={event => {
      if (loaded && !disabled && onPickHeader && event.isPrimary && event.button === 0) {
        const {x, y} = point(event);
        if (region && x >= region[0] && x <= region[2] && y > region[1] && y < region[3]) onPickHeader(y);
      } else {
        move(event);
        start.current = null;
      }
    }} onPointerCancel={() => {
      if (start.current) onChange(null);
      start.current = null;
    }}>
      <img src={url} alt="待框选的原始 PDF 页面" draggable={false} onLoad={() => setLoaded(true)} onError={() => setFailed(true)} />
      {loaded && region && <div className="pdf-selected-region" style={{
        left: region[0]*100+'%', top: region[1]*100+'%',
        width: (region[2]-region[0])*100+'%', height: (region[3]-region[1])*100+'%',
      }} />}
      {loaded && region && header !== undefined && header > region[1] && header < region[3] &&
        <div className="pdf-region-header" style={{left: region[0]*100+'%', top: header*100+'%', width:(region[2]-region[0])*100+'%'}} />}
    </div>
    {failed && <p role="alert">该页原图无法用于框选。</p>}
  </>;
}
