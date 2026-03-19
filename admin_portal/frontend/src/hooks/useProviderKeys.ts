import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { providerKeysApi } from '../services/api';
import type { ProviderKeyCreate, ProviderKeyUpdate } from '../types';

export function useProviderKeys() {
  return useQuery({
    queryKey: ['providerKeys'],
    queryFn: () => providerKeysApi.list(),
  });
}

export function useCreateProviderKey() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (data: ProviderKeyCreate) => providerKeysApi.create(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['providerKeys'] });
    },
  });
}

export function useUpdateProviderKey() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ keyId, data }: { keyId: string; data: ProviderKeyUpdate }) =>
      providerKeysApi.update(keyId, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['providerKeys'] });
    },
  });
}

export function useDeleteProviderKey() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (keyId: string) => providerKeysApi.delete(keyId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['providerKeys'] });
    },
  });
}
