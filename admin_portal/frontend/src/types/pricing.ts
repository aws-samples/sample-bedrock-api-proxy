export interface ModelPricing {
  model_id: string;
  provider: string;
  display_name?: string;
  input_price: number;
  output_price: number;
  cache_read_price?: number;
  cache_write_price?: number;
  status: 'active' | 'deprecated' | 'disabled';
  created_at: number;
  updated_at?: number;
}

export interface PricingCreate {
  model_id: string;
  provider: string;
  display_name?: string;
  input_price: number;
  output_price: number;
  cache_read_price?: number;
  cache_write_price?: number;
  status?: 'active' | 'deprecated' | 'disabled';
}

export interface PricingUpdate {
  provider?: string;
  display_name?: string;
  input_price?: number;
  output_price?: number;
  cache_read_price?: number;
  cache_write_price?: number;
  status?: 'active' | 'deprecated' | 'disabled';
}

export interface PricingListResponse {
  items: ModelPricing[];
  count: number;
  last_key?: string;
}

export interface PricingSyncRequest {
  url?: string;
  create_missing?: boolean;
  overwrite_manual?: boolean;
  dry_run?: boolean;
}

export interface PricingSyncResult {
  source_url: string;
  source_models: number;
  created: string[];
  updated: string[];
  skipped_manual: string[];
  unchanged: number;
  not_found: string[];
  dry_run: boolean;
}
