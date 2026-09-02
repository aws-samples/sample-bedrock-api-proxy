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
