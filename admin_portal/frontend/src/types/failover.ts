export interface FailoverTarget {
  provider: string;
  model: string;
}

export interface FailoverChain {
  source_model: string;
  targets: FailoverTarget[];
  created_at: string;
  updated_at?: string;
}

export interface FailoverChainCreate {
  source_model: string;
  targets: FailoverTarget[];
}

export interface FailoverChainUpdate {
  targets: FailoverTarget[];
}
