export interface ProviderKey {
  key_id: string;
  provider: string;
  api_key_masked: string;
  models: string[];
  is_enabled: boolean;
  status: 'available' | 'cooldown' | 'disabled';
  created_at: string;
  updated_at?: string;
}

export interface ProviderKeyCreate {
  provider: string;
  api_key: string;
  models: string[];
}

export interface ProviderKeyUpdate {
  models?: string[];
  is_enabled?: boolean;
}
