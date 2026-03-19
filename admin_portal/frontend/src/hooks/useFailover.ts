import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { failoverApi } from '../services/api';
import type { FailoverChainCreate, FailoverChainUpdate } from '../types';

export function useFailoverChains() {
  return useQuery({
    queryKey: ['failoverChains'],
    queryFn: () => failoverApi.listChains(),
  });
}

export function useCreateFailoverChain() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (data: FailoverChainCreate) => failoverApi.createChain(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['failoverChains'] });
    },
  });
}

export function useUpdateFailoverChain() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ sourceModel, data }: { sourceModel: string; data: FailoverChainUpdate }) =>
      failoverApi.updateChain(sourceModel, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['failoverChains'] });
    },
  });
}

export function useDeleteFailoverChain() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (sourceModel: string) => failoverApi.deleteChain(sourceModel),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['failoverChains'] });
    },
  });
}
