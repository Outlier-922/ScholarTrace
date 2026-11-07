import { useEffect, useState } from 'react';
import { request } from './api';

interface Versions {
  items: { id: string; filename: string; version_label: string | null; page_count: number; status: string }[];
  latest_readable_version: string | null;
  latest_readable_ids: string[];
}

export default function WorkVersions({ documentId, groupRevision, onSelect }: {
  documentId: string;
  groupRevision: string;
  onSelect: (id: string) => void;
}) {
  const [versions, setVersions] = useState<Versions | null>(null);
  const [error, setError] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    setVersions(null);
    setError(false);
    void request<Versions>(`/documents/${documentId}/work-versions`, { signal: controller.signal })
      .then(value => { if (!controller.signal.aborted) setVersions(value); })
      .catch(() => { if (!controller.signal.aborted) setError(true); });
    return () => controller.abort();
  }, [documentId, groupRevision]);

  if (error) return <p className="work-version-picker" role="status">暂时无法读取作品版本</p>;
  if (!versions || versions.items.length < 2) return null;
  return <div className="work-version-picker">
    <label>作品版本 <select aria-label="切换作品版本" value={documentId} onChange={event => onSelect(event.target.value)}>
      {versions.items.map(item => <option key={item.id} value={item.id}>
        {item.version_label || '版本未注明'} · {item.filename} · {item.page_count} 页
      </option>)}
    </select></label>
    <span>{versions.latest_readable_version
      ? `同 arXiv 标识的库内最新可读版本 ${versions.latest_readable_version}${versions.latest_readable_ids.length > 1 ? '（有多个文件）' : ''}`
      : '版本顺序待核对'}</span>
  </div>;
}
