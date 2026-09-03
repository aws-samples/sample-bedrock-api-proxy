export interface ModelMapping {
  anthropic_model_id: string;
  bedrock_model_id: string;
  source: 'default' | 'custom' | 'override';
  default_bedrock_model_id?: string;
  updated_at?: number;
}

export interface ModelMappingCreate {
  anthropic_model_id: string;
  bedrock_model_id: string;
}

export interface ModelMappingUpdate {
  bedrock_model_id: string;
}

export interface ModelMappingListResponse {
  items: ModelMapping[];
  count: number;
}

export interface ModelMappingSyncRequest {
  url?: string;
  dry_run?: boolean;
}

export interface ModelMappingSyncResult {
  source_url: string;
  remote_models: number;
  mapping_count: number;
  local_overrides: number;
  added: string[];
  removed: string[];
  changed: string[];
  dry_run: boolean;
}

export interface ModelMappingSyncStatus {
  enabled: boolean;
  source_url: string;
  source: 'remote' | 'bundled' | 'env';
  mapping_count: number;
  local_override_count: number;
  last_attempt_at?: number | null;
  last_success_at?: number | null;
  last_error?: string | null;
}

/**
 * One speed-test run through the proxy for a Bedrock model ID.
 * Mirrors the DynamoDB item / `SpeedTestRecord` pydantic model in the admin backend.
 */
export interface SpeedTestRecord {
  bedrock_model_id: string;
  /** Epoch milliseconds at request send. */
  tested_at: number;
  status: 'ok' | 'error';
  ttft_ms: number | null;
  total_ms: number | null;
  output_tokens: number | null;
  /** Hidden reasoning tokens counted in output_tokens (OpenAI-compat models); null when not reported. */
  reasoning_tokens?: number | null;
  /** Streamed tokens per second (excluding TTFT and hidden reasoning), null when not computable. */
  otps: number | null;
  has_reasoning: boolean;
  error: string | null;
  proxy_base_url: string;
}

export interface SpeedTestRequest {
  bedrock_model_id: string;
}

export interface SpeedTestHistoryResponse {
  /** Newest first. */
  items: SpeedTestRecord[];
  count: number;
}

export interface SpeedTestLatestResponse {
  /** Keyed by bedrock_model_id. */
  items: Record<string, SpeedTestRecord>;
}
