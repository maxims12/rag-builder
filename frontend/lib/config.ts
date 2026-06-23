// Typed config domain layer. Mirrors CONTRACT.md §3 Shared Data Schemas exactly.
// Every call routes through lib/api.ts (Rule 5: no API bypass).

import { apiGet, apiPost, apiPut } from "@/lib/api";

// ---------------------------------------------------------------------------
// Section interfaces (CONTRACT.md §3)
// ---------------------------------------------------------------------------

export interface SourcesConfig {
  docs_folder: string;
  watch_mode: boolean;
  recursive: boolean;
  file_types: string[];
  exclude_patterns: string[];
  max_file_size_mb: number;
  polling_interval: number;
}

export interface WebSourcesConfig {
  web_urls: string[];
  web_mode: "single" | "crawl" | "sitemap";
  crawl_depth: number;
  max_pages: number;
  same_domain_only: boolean;
  sitemap_url: string | null;
  render_js: boolean;
  strip_selectors: string[];
  respect_robots_txt: boolean;
  request_timeout_s: number;
  crawl_concurrency: number;
  auto_refresh: boolean;
  refresh_interval_hours: number;
}

export interface ChunkingConfig {
  chunk_strategy: "recursive" | "semantic" | "fixed" | "markdown" | "token";
  chunk_size: number;
  chunk_overlap: number;
  min_chunk_size: number;
  respect_sentence_boundary: boolean;
}

export interface EmbeddingConfig {
  emb_provider: "openai" | "cohere" | "huggingface" | "ollama" | "voyage";
  emb_model: string;
  emb_dimensions: number | null;
  emb_batch_size: number;
  emb_normalize: boolean;
  emb_device: "cpu" | "cuda";
}

export interface VectorStoreConfig {
  vs_backend: "chroma" | "qdrant" | "pgvector" | "milvus";
  vs_collection: string;
  vs_distance: "cosine" | "euclidean" | "dot";
  vs_hnsw_m: number;
  vs_hnsw_ef_construct: number;
  vs_on_disk: boolean;
}

export interface RetrievalConfig {
  top_k: number;
  score_threshold: number;
  search_type: "similarity" | "mmr" | "hybrid";
  mmr_diversity: number;
  reranking: boolean;
  reranker_model: string;
  hybrid_alpha: number;
  hybrid_method: "token_overlap" | "bm25";
  multi_query: boolean;
  multi_query_count: number;
  contextual_compression: boolean;
}

export interface LLMConfig {
  llm_provider: "anthropic" | "openai" | "ollama" | "groq";
  llm_model: string;
  temperature: number;
  max_tokens: number;
  system_prompt: string;
  streaming: boolean;
}

export interface SystemConfig {
  parallel_workers: number;
  cache_embeddings: boolean;
  log_level: string;
  rate_limit_rpm: number;
}

// On GET, stored keys come back as "******"; null when empty. On PUT, send the
// raw key to set, or omit / leave masked to keep the stored value untouched.
export interface CredentialsConfig {
  openai_api_key: string | null;
  cohere_api_key: string | null;
  anthropic_api_key: string | null;
  groq_api_key: string | null;
  voyage_api_key: string | null;
}

export interface RAGConfig {
  sources: SourcesConfig;
  web_sources: WebSourcesConfig;
  chunking: ChunkingConfig;
  embedding: EmbeddingConfig;
  vectorstore: VectorStoreConfig;
  retrieval: RetrievalConfig;
  llm: LLMConfig;
  system: SystemConfig;
  credentials: CredentialsConfig;
}

export type SectionName = keyof RAGConfig;

export interface SectionTypeMap {
  sources: SourcesConfig;
  web_sources: WebSourcesConfig;
  chunking: ChunkingConfig;
  embedding: EmbeddingConfig;
  vectorstore: VectorStoreConfig;
  retrieval: RetrievalConfig;
  llm: LLMConfig;
  system: SystemConfig;
  credentials: CredentialsConfig;
}

// ---------------------------------------------------------------------------
// Config CRUD (GET/PUT /settings/config/{section})
// ---------------------------------------------------------------------------

export function getSection<K extends SectionName>(
  section: K
): Promise<SectionTypeMap[K]> {
  return apiGet<SectionTypeMap[K]>(`/settings/config/${section}`);
}

export function putSection<K extends SectionName>(
  section: K,
  body: SectionTypeMap[K]
): Promise<SectionTypeMap[K]> {
  return apiPut<SectionTypeMap[K]>(`/settings/config/${section}`, body);
}

// ---------------------------------------------------------------------------
// Ingestion / Pipeline (CONTRACT.md §2)
// ---------------------------------------------------------------------------

export interface IngestJobAck {
  job_id: number;
  source_type: "local" | "web";
  status: string;
  started_at: string;
}

export interface IndexJob {
  id: number;
  source_type: "local" | "web";
  status: "pending" | "running" | "done" | "error" | string;
  files_processed: number;
  pages_fetched: number;
  chunks_created: number;
  error_message: string | null;
  started_at: string;
  finished_at: string | null;
}

export interface JobsResponse {
  total: number;
  jobs: IndexJob[];
}

export interface HealthStats {
  vector_store_healthy: boolean;
  db_healthy: boolean;
  local_docs_count: number;
  web_pages_count: number;
  total_chunks_count: number;
}

export function ingestSources(): Promise<IngestJobAck> {
  return apiPost<IngestJobAck>("/sources/ingest");
}

export function ingestWebSources(): Promise<IngestJobAck> {
  return apiPost<IngestJobAck>("/web-sources/ingest");
}

export function listJobs(limit = 10, offset = 0): Promise<JobsResponse> {
  return apiGet<JobsResponse>(`/pipeline/jobs?limit=${limit}&offset=${offset}`);
}

// ---------------------------------------------------------------------------
// Web source extraction preview (POST /web-sources/test)
// ---------------------------------------------------------------------------

export interface WebTestRequest {
  url: string;
  render_js: boolean;
  strip_selectors: string[];
}

export interface WebTestResponse {
  url: string;
  title: string | null;
  clean_text: string;
  raw_html_length: number;
  extracted_text_length: number;
  content_hash: string;
  fetched_at: string;
}

export function testWebExtraction(
  body: WebTestRequest
): Promise<WebTestResponse> {
  return apiPost<WebTestResponse>("/web-sources/test", body);
}

// ---------------------------------------------------------------------------
// Playground source schema (shared by SSE + non-stream)
// ---------------------------------------------------------------------------

export interface PlaygroundSource {
  source_type: "web" | "local" | string;
  source_path_or_url: string;
  title: string | null;
  snippet: string;
  score: number;
}
