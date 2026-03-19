export interface ApiKey {
  api_key: string;
  user_id: string;
  name: string;
  owner_name?: string;
  role?: string;
  monthly_budget?: number;
  budget_used?: number;  // Total cumulative budget used (never resets)
  budget_used_mtd?: number;  // Month-to-date budget used (resets monthly)
  budget_mtd_month?: string;  // Month for MTD tracking (YYYY-MM format)
  budget_history?: string;  // Monthly budget history as JSON string (e.g., {"2025-11": 32.11})
  tpm_limit?: number;
  rate_limit?: number;
  service_tier?: string;
  is_active: boolean;
  deactivated_reason?: string;  // Reason for deactivation (e.g., "budget_exceeded")
  cache_ttl?: string | null;  // Per-key cache TTL override ("5m", "1h", or null)
  routing_strategy?: string;  // cost/quality/auto/off
  compression_strategy?: string;  // aggressive/moderate/conservative/off
  created_at: number;
  updated_at?: number;
  // Usage stats (aggregated from usage_stats table)
  total_input_tokens?: number;
  total_output_tokens?: number;
  total_cached_tokens?: number;      // Cache read tokens
  total_cache_write_tokens?: number; // Cache write tokens
  total_requests?: number;
}

export interface ApiKeyCreate {
  user_id: string;
  name: string;
  owner_name?: string;
  role?: string;
  monthly_budget?: number;
  rate_limit?: number;
  service_tier?: string;
  cache_ttl?: string | null;
  routing_strategy?: string;
  compression_strategy?: string;
}

export interface ApiKeyUpdate {
  name?: string;
  owner_name?: string;
  role?: string;
  monthly_budget?: number;
  rate_limit?: number;
  service_tier?: string;
  is_active?: boolean;
  cache_ttl?: string | null;
  routing_strategy?: string;
  compression_strategy?: string;
}

export interface ApiKeyListResponse {
  items: ApiKey[];
  count: number;
  last_key?: string;
}

export interface ApiKeyUsage {
  total_requests: number;
  successful_requests: number;
  failed_requests: number;
  total_input_tokens: number;
  total_output_tokens: number;
  models_used: { [key: string]: number };
  recent_requests: Array<{
    timestamp: number;
    model: string;
    input_tokens: number;
    output_tokens: number;
    success: boolean;
  }>;
}
