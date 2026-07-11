import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { apiKeysApi } from '../services/api';
import type { ApiKeyCreate, ApiKeyUpdate } from '../types';

export function useApiKeys(params?: {
  limit?: number;
  status?: string;
  search?: string;
}) {
  return useQuery({
    queryKey: ['apiKeys', params],
    queryFn: () => apiKeysApi.list(params),
  });
}

export function useApiKey(apiKey: string) {
  return useQuery({
    queryKey: ['apiKey', apiKey],
    queryFn: () => apiKeysApi.get(apiKey),
    enabled: !!apiKey,
  });
}

export function useApiKeyUsage(apiKey: string) {
  return useQuery({
    queryKey: ['apiKeyUsage', apiKey],
    queryFn: () => apiKeysApi.getUsage(apiKey),
    enabled: !!apiKey,
  });
}

/**
 * Per-key daily usage for the hover thumbnail charts.
 * Fetched lazily (enabled=false until hover) and cached for 5 minutes
 * so repeated hovers don't re-scan the usage table.
 */
export function useApiKeyDailyUsage(apiKey: string, days = 7, enabled = true) {
  return useQuery({
    queryKey: ['apiKeyDailyUsage', apiKey, days],
    queryFn: () => apiKeysApi.getDailyUsage(apiKey, days),
    enabled: enabled && !!apiKey,
    staleTime: 5 * 60 * 1000,
  });
}

export function useCreateApiKey() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (data: ApiKeyCreate) => apiKeysApi.create(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['apiKeys'] });
    },
  });
}

export function useUpdateApiKey() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({ apiKey, data }: { apiKey: string; data: ApiKeyUpdate }) =>
      apiKeysApi.update(apiKey, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['apiKeys'] });
    },
  });
}

export function useDeactivateApiKey() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (apiKey: string) => apiKeysApi.deactivate(apiKey),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['apiKeys'] });
    },
  });
}

export function useReactivateApiKey() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (apiKey: string) => apiKeysApi.reactivate(apiKey),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['apiKeys'] });
    },
  });
}

export function useDeleteApiKey() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (apiKey: string) => apiKeysApi.deletePermanently(apiKey),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['apiKeys'] });
    },
  });
}
