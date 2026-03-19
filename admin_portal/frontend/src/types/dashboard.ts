export interface DashboardStats {
  total_api_keys: number;
  active_api_keys: number;
  revoked_api_keys: number;
  total_budget: number;
  total_budget_used: number;
  total_models: number;
  active_models: number;
  system_status: string;
  new_keys_this_week: number;
  // Models that have usage but no pricing configured
  models_without_pricing: string[];
  // Total token usage across all API keys
  total_input_tokens: number;
  total_output_tokens: number;
  total_cached_tokens: number;
  total_cache_write_tokens: number;
  total_requests: number;
}
