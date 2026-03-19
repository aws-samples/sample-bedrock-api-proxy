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
