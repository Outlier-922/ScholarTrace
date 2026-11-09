export type DocumentStatus = 'queued' | 'processing' | 'ready' | 'partial' | 'failed';

export interface ResearchDocument {
  id: string;
  work_id?: string;
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
  upload_outcome?: 'reused' | 'created';
  version_recommendation?: string | null;
  warnings: string[];
  pdf_url: string;
  job_id: string;
  parse_run_id?: string;
}

export interface BlockMetadata {
  extraction?: string;
  confidence?: number;
  confidence_min?: number;
  verification_status?: string;
  rows?: (string | number | null)[][];
  header?: unknown;
  row_count?: number;
  col_count?: number;
  ocr_performed?: boolean;
  low_confidence?: boolean;
  lines?: { text: string; bbox: [number, number, number, number]; confidence?: number }[];
  [key: string]: unknown;
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
  parse_run_id?: string;
  metadata?: BlockMetadata;
}

export interface DocumentStructure {
  document_id: string;
  parse_run_id?: string;
  pages: { number: number; width: number; height: number; status: string }[];
  blocks: DocumentBlock[];
  outline: { id: string; title: string; level: number; page: number; block_id: string }[];
  references: unknown[];
}

export interface SourceLocation {
  document_id: string;
  parse_run_id?: string;
  page: number;
  block_id: string;
}

export interface Evidence extends SourceLocation {
  id: string;
  title: string;
  version_label?: string | null;
  bbox: [number, number, number, number];
  text: string;
  score: number;
  extraction?: string;
  confidence?: number;
  source_kind?: 'table_cell' | 'table_draft';
  table_cell?: {source_id:string; revision_id:null; row:number; column:number; column_label:string; value:string; origin:string};
}

export interface TableReference { source_id:string; revision_id:string|null; document_id:string; parse_run_id:string; title:string; page:number; row_indices:number[]|null; }
export interface TableDraft extends Evidence {
  table_draft:{source_id:string;revision_id:string;row:number;changes:{column:number;label:string;before:string|null;after:string|null;bbox:number[]}[];header_changes:{column:number;before:string;after:string}[];note:string;sent_to_model:false};
}

export interface ClaimValidation {
  version?:string; semantic_verified?:boolean; truncated?:number;
  decisions?:{index:number;status:string;reason:string;unknown_numbers?:{literal:string;span:number[]}[]}[];
}
export interface AnswerResult {
  run_id?:string;
  question?:string;
  validation_replay?:{source_run_id:string;actual_model_calls:number;source_checked:boolean};
  model_proposal?:Record<string,unknown>;
  claim_validation?:ClaimValidation;
  field_validation?:ClaimValidation;
  mode: 'evidence_only' | 'llm';
  answer: string;
  claims: { text: string; evidence_ids: string[]; status: string }[];
  evidence: Evidence[];
  missing_information: string[];
  steps: { label: string; status: string; detail: string }[];
  warnings: string[];
  table_drafts?:TableDraft[];
  table_context?:{source_id:string;revision_id:string|null;document_id:string;parse_run_id:string;row_indices:number[]|null;available:boolean}[];
  answer_coverage?: { document_id: string; title: string; field: string; label: string; status: string; evidence_ids: string[] }[];
  retrieval?: {
    mode: 'bm25_dense_rrf' | 'bm25' | 'mixed' | 'table_bm25' | 'mixed_tables';
    index_required?: { document_id: string; parse_run_id: string; missing_chunks: number }[];
    enabled?: boolean;
    ready?: boolean;
    used?: boolean;
    model?: string;
    reason?: string;
  };
}

export interface Health {
  status: string;
  version: string;
  capabilities: Record<string, boolean | string>;
  model_configured: boolean;
  deployment?: { demo_enabled: boolean; read_only: boolean; workspace: string; user_isolation: boolean };
}

export interface Stats {
  document_count: number;
  ready_count: number;
  total_pages: number;
  blocks_count: number;
}
