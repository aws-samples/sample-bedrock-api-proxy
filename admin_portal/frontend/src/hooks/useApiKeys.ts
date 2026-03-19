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
