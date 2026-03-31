export interface Provider {
  provider_id: string;
  name: string;
  aws_region: string;
  auth_type: 'bearer_token' | 'ak_sk';
  masked_credentials: string;
  endpoint_url?: string;
  is_active: boolean;
  created_at: string;
  updated_at: string;
  api_key_count?: number;
}

export interface ProviderCreate {
  name: string;
  aws_region: string;
  auth_type: 'bearer_token' | 'ak_sk';
  credentials: Record<string, string>;
  endpoint_url?: string;
}

export interface ProviderUpdate {
  name?: string;
  aws_region?: string;
  auth_type?: 'bearer_token' | 'ak_sk';
  credentials?: Record<string, string>;
  endpoint_url?: string;
  is_active?: boolean;
}

export interface ProviderListResponse {
  items: Provider[];
  count: number;
}

export interface ProviderTestResult {
  status: 'ok' | 'error';
  message: string;
}
