export interface RoutingRule {
  rule_id: string;
  rule_name: string;
  rule_type: 'keyword' | 'regex' | 'model';
  pattern: string;
  target_model: string;
  target_provider: string;
  priority: number;
  is_enabled: boolean;
  created_at: string;
  updated_at?: string;
}

export interface RoutingRuleCreate {
  rule_name: string;
  rule_type: 'keyword' | 'regex' | 'model';
  pattern: string;
  target_model: string;
  target_provider?: string;
}

export interface RoutingRuleUpdate {
  rule_name?: string;
  rule_type?: string;
  pattern?: string;
  target_model?: string;
  target_provider?: string;
  is_enabled?: boolean;
}

export interface SmartRoutingConfig {
  strong_model: string;
  weak_model: string;
  threshold: number;
}
