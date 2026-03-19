/**
 * API Service for Admin Portal
 *
 * Handles all API requests to the backend with Bearer token authentication.
 * Token is obtained from AWS Amplify session.
 */
import { fetchAuthSession } from 'aws-amplify/auth';
import { isAmplifyConfigured } from '../config/amplify';
import type {
  ApiKey,
  ApiKeyCreate,
  ApiKeyUpdate,
  ApiKeyListResponse,
  ApiKeyUsage,
  ModelPricing,
  PricingCreate,
  PricingUpdate,
  PricingListResponse,
  DashboardStats,
  ModelMapping,
  ModelMappingCreate,
  ModelMappingUpdate,
  ModelMappingListResponse,
  ProviderKey,
  ProviderKeyCreate,
  ProviderKeyUpdate,
  RoutingRule,
  RoutingRuleCreate,
  RoutingRuleUpdate,
  SmartRoutingConfig,
  FailoverChain,
  FailoverChainCreate,
  FailoverChainUpdate,
} from '../types';

const API_BASE_URL = '/api';

// Flag to prevent multiple auth error redirects
let isHandlingAuthError = false;

// Custom event for auth errors - listened by useAuth hook
export const AUTH_ERROR_EVENT = 'auth:session-expired';

/**
 * Emit auth error event for the auth context to handle.
 * Prevents multiple simultaneous redirects.
 */
function emitAuthError(reason: string): void {
  if (isHandlingAuthError) return;
  isHandlingAuthError = true;

  // Reset flag after a delay to allow future auth errors
  setTimeout(() => {
    isHandlingAuthError = false;
  }, 3000);

  window.dispatchEvent(new CustomEvent(AUTH_ERROR_EVENT, { detail: { reason } }));
}

/**
 * Get the current authentication token from Amplify session.
 * Returns null in development mode when Cognito is not configured.
 * Amplify automatically refreshes expired tokens if refresh token is valid.
 */
async function getAuthToken(): Promise<string | null> {
  if (!isAmplifyConfigured()) {
    return null;
  }

  try {
    const session = await fetchAuthSession();
    return session.tokens?.idToken?.toString() || null;
  } catch (error) {
    // Token refresh failed - likely refresh token expired
    console.error('Failed to get auth token:', error);
    emitAuthError('token_refresh_failed');
    return null;
  }
}

/**
 * Check if user is authenticated.
 */
export async function isAuthenticated(): Promise<boolean> {
  if (!isAmplifyConfigured()) {
    // Development mode - always authenticated
    return true;
  }

  const token = await getAuthToken();
  return !!token;
}

/**
 * Base fetch wrapper with Bearer token authentication.
 */
async function apiFetch<T>(
  endpoint: string,
  options: RequestInit = {}
): Promise<T> {
  const headers: HeadersInit = {
    'Content-Type': 'application/json',
    ...options.headers,
  };

  // Add Bearer token if available
  const token = await getAuthToken();
  if (token) {
    (headers as Record<string, string>)['Authorization'] = `Bearer ${token}`;
  } else if (isAmplifyConfigured()) {
    // Cognito is configured but no token - emit auth error event
    emitAuthError('no_token');
    throw new Error('Authentication required');
  }

  const response = await fetch(`${API_BASE_URL}${endpoint}`, {
    ...options,
    headers,
  });

  if (!response.ok) {
    // Handle 401 Unauthorized - emit auth error event
    if (response.status === 401) {
      emitAuthError('unauthorized');
      throw new Error('Session expired. Please login again.');
    }

    const error = await response.json().catch(() => ({ detail: 'Unknown error' }));
    throw new Error(error.detail || error.message || `HTTP error! status: ${response.status}`);
  }

  return response.json();
}

// Auth API
export const authApi = {
  /**
   * Get Cognito configuration from backend.
   */
  getConfig: async (): Promise<{
    userPoolId: string;
    userPoolClientId: string;
    region: string;
  }> => {
    const response = await fetch(`${API_BASE_URL}/auth/config`);
    if (!response.ok) {
      throw new Error('Failed to fetch auth config');
    }
    return response.json();
  },

  /**
   * Verify current session with backend.
   */
  verify: async (): Promise<{
    authenticated: boolean;
    username?: string;
    email?: string;
  }> => {
    return apiFetch('/auth/verify');
  },

  /**
   * Get current user details from backend.
   */
  me: async (): Promise<{
    user: {
      username: string;
      email?: string;
      name?: string;
    };
    token_claims?: Record<string, unknown>;
  }> => {
    return apiFetch('/auth/me');
  },
};

// Dashboard API
export const dashboardApi = {
  getStats: async (): Promise<DashboardStats> => {
    return apiFetch('/dashboard/stats');
  },
};

// API Keys API
export const apiKeysApi = {
  list: async (params?: {
    limit?: number;
    status?: string;
    search?: string;
  }): Promise<ApiKeyListResponse> => {
    const searchParams = new URLSearchParams();
    if (params?.limit) searchParams.set('limit', params.limit.toString());
    if (params?.status) searchParams.set('status', params.status);
    if (params?.search) searchParams.set('search', params.search);

    const query = searchParams.toString();
    return apiFetch(`/keys${query ? `?${query}` : ''}`);
  },

  get: async (apiKey: string): Promise<ApiKey> => {
    return apiFetch(`/keys/${encodeURIComponent(apiKey)}`);
  },

  create: async (data: ApiKeyCreate): Promise<ApiKey> => {
    return apiFetch('/keys', {
      method: 'POST',
      body: JSON.stringify(data),
    });
  },

  update: async (apiKey: string, data: ApiKeyUpdate): Promise<ApiKey> => {
    return apiFetch(`/keys/${encodeURIComponent(apiKey)}`, {
      method: 'PUT',
      body: JSON.stringify(data),
    });
  },

  deactivate: async (apiKey: string): Promise<{ message: string }> => {
    return apiFetch(`/keys/${encodeURIComponent(apiKey)}`, {
      method: 'DELETE',
    });
  },

  reactivate: async (apiKey: string): Promise<{ message: string }> => {
    return apiFetch(`/keys/${encodeURIComponent(apiKey)}/reactivate`, {
      method: 'POST',
    });
  },

  deletePermanently: async (apiKey: string): Promise<{ message: string }> => {
    return apiFetch(`/keys/${encodeURIComponent(apiKey)}/permanent`, {
      method: 'DELETE',
    });
  },

  getUsage: async (apiKey: string): Promise<ApiKeyUsage> => {
    return apiFetch(`/keys/${encodeURIComponent(apiKey)}/usage`);
  },
};

// Pricing API
export const pricingApi = {
  list: async (params?: {
    limit?: number;
    provider?: string;
    status?: string;
    search?: string;
  }): Promise<PricingListResponse> => {
    const searchParams = new URLSearchParams();
    if (params?.limit) searchParams.set('limit', params.limit.toString());
    if (params?.provider) searchParams.set('provider', params.provider);
    if (params?.status) searchParams.set('status', params.status);
    if (params?.search) searchParams.set('search', params.search);

    const query = searchParams.toString();
    return apiFetch(`/pricing${query ? `?${query}` : ''}`);
  },

  getProviders: async (): Promise<{ providers: string[] }> => {
    return apiFetch('/pricing/providers');
  },

  get: async (modelId: string): Promise<ModelPricing> => {
    return apiFetch(`/pricing/${encodeURIComponent(modelId)}`);
  },

  create: async (data: PricingCreate): Promise<ModelPricing> => {
    return apiFetch('/pricing', {
      method: 'POST',
      body: JSON.stringify(data),
    });
  },

  update: async (modelId: string, data: PricingUpdate): Promise<ModelPricing> => {
    return apiFetch(`/pricing/${encodeURIComponent(modelId)}`, {
      method: 'PUT',
      body: JSON.stringify(data),
    });
  },

  delete: async (modelId: string): Promise<{ message: string }> => {
    return apiFetch(`/pricing/${encodeURIComponent(modelId)}`, {
      method: 'DELETE',
    });
  },
};

// Model Mapping API
export const modelMappingApi = {
  list: async (params?: { search?: string }): Promise<ModelMappingListResponse> => {
    const searchParams = new URLSearchParams();
    if (params?.search) searchParams.set('search', params.search);

    const query = searchParams.toString();
    return apiFetch(`/model-mapping${query ? `?${query}` : ''}`);
  },

  get: async (anthropicModelId: string): Promise<ModelMapping> => {
    return apiFetch(`/model-mapping/${encodeURIComponent(anthropicModelId)}`);
  },

  create: async (data: ModelMappingCreate): Promise<ModelMapping> => {
    return apiFetch('/model-mapping', {
      method: 'POST',
      body: JSON.stringify(data),
    });
  },

  update: async (anthropicModelId: string, data: ModelMappingUpdate): Promise<ModelMapping> => {
    return apiFetch(`/model-mapping/${encodeURIComponent(anthropicModelId)}`, {
      method: 'PUT',
      body: JSON.stringify(data),
    });
  },

  delete: async (anthropicModelId: string): Promise<{ message: string }> => {
    return apiFetch(`/model-mapping/${encodeURIComponent(anthropicModelId)}`, {
      method: 'DELETE',
    });
  },
};


// Provider Keys API
export const providerKeysApi = {
  list: async (): Promise<ProviderKey[]> => {
    return apiFetch('/provider-keys');
  },

  create: async (data: ProviderKeyCreate): Promise<ProviderKey> => {
    return apiFetch('/provider-keys', {
      method: 'POST',
      body: JSON.stringify(data),
    });
  },

  update: async (keyId: string, data: ProviderKeyUpdate): Promise<{ success: boolean }> => {
    return apiFetch(`/provider-keys/${encodeURIComponent(keyId)}`, {
      method: 'PUT',
      body: JSON.stringify(data),
    });
  },

  delete: async (keyId: string): Promise<void> => {
    await apiFetch(`/provider-keys/${encodeURIComponent(keyId)}`, {
      method: 'DELETE',
    });
  },
};

// Routing API
export const routingApi = {
  listRules: async (): Promise<RoutingRule[]> => {
    return apiFetch('/routing/rules');
  },

  createRule: async (data: RoutingRuleCreate): Promise<RoutingRule> => {
    return apiFetch('/routing/rules', {
      method: 'POST',
      body: JSON.stringify(data),
    });
  },

  updateRule: async (ruleId: string, data: RoutingRuleUpdate): Promise<{ success: boolean }> => {
    return apiFetch(`/routing/rules/${encodeURIComponent(ruleId)}`, {
      method: 'PUT',
      body: JSON.stringify(data),
    });
  },

  deleteRule: async (ruleId: string): Promise<void> => {
    await apiFetch(`/routing/rules/${encodeURIComponent(ruleId)}`, {
      method: 'DELETE',
    });
  },

  reorderRules: async (ruleIds: string[]): Promise<{ success: boolean }> => {
    return apiFetch('/routing/rules/reorder', {
      method: 'PUT',
      body: JSON.stringify({ rule_ids: ruleIds }),
    });
  },

  getSmartConfig: async (): Promise<SmartRoutingConfig> => {
    return apiFetch('/routing/smart-config');
  },

  updateSmartConfig: async (data: SmartRoutingConfig): Promise<SmartRoutingConfig> => {
    return apiFetch('/routing/smart-config', {
      method: 'PUT',
      body: JSON.stringify(data),
    });
  },
};

// Failover API
export const failoverApi = {
  listChains: async (): Promise<FailoverChain[]> => {
    return apiFetch('/failover/chains');
  },

  createChain: async (data: FailoverChainCreate): Promise<FailoverChain> => {
    return apiFetch('/failover/chains', {
      method: 'POST',
      body: JSON.stringify(data),
    });
  },

  updateChain: async (sourceModel: string, data: FailoverChainUpdate): Promise<{ success: boolean }> => {
    return apiFetch(`/failover/chains/${encodeURIComponent(sourceModel)}`, {
      method: 'PUT',
      body: JSON.stringify(data),
    });
  },

  deleteChain: async (sourceModel: string): Promise<void> => {
    await apiFetch(`/failover/chains/${encodeURIComponent(sourceModel)}`, {
      method: 'DELETE',
    });
  },
};
