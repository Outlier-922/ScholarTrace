export type DocumentStatus = 'queued' | 'processing' | 'ready' | 'partial' | 'failed';

export interface ResearchDocument {
  id: string;
  title: string;
  authors: string[];
  filename: string;
  page_count: number;
  created_at: string;
  status: DocumentStatus;
  progress_pages: number;
  blocks_count: number;
  version_label?: string | null;
  arxiv_id?: string | null;
  sha256: string;
  duplicate_of?: string | null;
  duplicate_kind?: string | null;
  version_recommendation?: string | null;
  warnings: string[];
  pdf_url: string;
  job_id: string;
}

export interface DocumentBlock {
  id: string;
  page: number;
  type: string;
  text: string;
  bbox: [number, number, number, number];
  order: number;
  heading_level?: number | null;
  section_path?: string | string[];
}

export interface DocumentStructure {
  document_id: string;
  pages: { number: number; width: number; height: number; status: string }[];
  blocks: DocumentBlock[];
  outline: { id: string; title: string; level: number; page: number; block_id: string }[];
  references: unknown[];
}

export interface Evidence {
  id: string;
  document_id: string;
  title: string;
  version_label?: string | null;
  page: number;
  block_id: string;
  bbox: [number, number, number, number];
  text: string;
  score: number;
}

export interface AnswerResult {
  mode: 'evidence_only' | 'llm';
  answer: string;
  claims: { text: string; evidence_ids: string[]; status: string }[];
  evidence: Evidence[];
  missing_information: string[];
  steps: { label: string; status: string; detail: string }[];
  warnings: string[];
}

export interface Health {
  status: string;
  version: string;
  capabilities: Record<string, boolean | string>;
  model_configured: boolean;
}

export interface Stats {
  document_count: number;
  ready_count: number;
  total_pages: number;
  blocks_count: number;
}
