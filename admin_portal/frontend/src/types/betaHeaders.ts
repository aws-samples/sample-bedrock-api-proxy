export interface BetaHeader {
  header_name: string;
  header_type: 'mapping' | 'blocklist';
  mapped_to: string[];
  description: string;
  created_at: string;
  updated_at: string;
}

export interface BetaHeaderCreate {
  header_name: string;
  header_type: 'mapping' | 'blocklist';
  mapped_to?: string[];
  description?: string;
}

export interface BetaHeaderUpdate {
  header_type?: 'mapping' | 'blocklist';
  mapped_to?: string[];
  description?: string;
}

export interface BetaHeaderListResponse {
  items: BetaHeader[];
  count: number;
}
