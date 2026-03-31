import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { providersApi } from '../services/api';
import type { ProviderCreate, ProviderUpdate } from '../types';

export function useProviders() {
  return useQuery({
    queryKey: ['providers'],
    queryFn: () => providersApi.list(),
  });
}

export function useCreateProvider() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (data: ProviderCreate) => providersApi.create(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['providers'] });
    },
  });
}

export function useUpdateProvider() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ providerId, data }: { providerId: string; data: ProviderUpdate }) =>
      providersApi.update(providerId, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['providers'] });
    },
  });
}

export function useDeleteProvider() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (providerId: string) => providersApi.delete(providerId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['providers'] });
    },
  });
}

export function useTestProvider() {
  return useMutation({
    mutationFn: (providerId: string) => providersApi.test(providerId),
  });
}
