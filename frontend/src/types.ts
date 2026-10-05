export type APIConfig = {
  base_url: string;
  model: string;
  protocol: 'chat_completions' | 'responses';
  vision: boolean;
  json_mode: boolean;
  api_key: string;
  timeout_seconds: number;
};

export type Settings = {
  api: Omit<APIConfig, 'api_key' | 'timeout_seconds'> & { has_api_key: boolean };
  limits: { max_upload_mb: number; max_documents: number };
  formats: string[];
  capabilities: { libreoffice: boolean; browser: boolean };
  demo_available: boolean;
};

export type DocumentSummary = {
  id: string;
  name: string;
  kind: string;
  unit_label: string;
  total_units: number;
  warnings: string[];
  units: { index: number; label: string; preview: string; has_images: boolean }[];
};

export type UnitDetail = {
  index: number; label: string; text: string; images: string[];
  transcription?: { text: string; uncertainties: string[] } | null;
};
export type Scope = { mode: 'all' | 'pages' | 'topics'; ranges: Record<string, string>; topics: string; include_prerequisites?: boolean };
export type GenerateRequest = {
  document_ids: string[];
  scope: Scope;
  api: APIConfig;
  learner_notes: string;
  language: string;
  layout: 'a4' | 'wide';
  reading_mode: 'auto' | 'handwritten';
};

export type Job = {
  id: string;
  status: 'queued' | 'running' | 'completed' | 'failed' | 'cancelled';
  stage: string;
  progress: number;
  created_at: string;
  error?: string | null;
  title?: string | null;
  page_count?: number | null;
  demo?: boolean;
  selection?: { document: string; label: string; ref: string }[];
  warnings?: string[];
  artifacts?: { pdf?: string; html?: string; json?: string; source_zip?: string };
};
