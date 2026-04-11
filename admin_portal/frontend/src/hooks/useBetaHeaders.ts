import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { betaHeadersApi } from '../services/api';
import type { BetaHeaderCreate, BetaHeaderUpdate } from '../types';

export function useBetaHeaders(params?: { type?: string; search?: string }) {
  return useQuery({
    queryKey: ['betaHeaders', params],
    queryFn: () => betaHeadersApi.list(params),
  });
}

export function useCreateBetaHeader() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (data: BetaHeaderCreate) => betaHeadersApi.create(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['betaHeaders'] });
    },
  });
}

export function useUpdateBetaHeader() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ headerName, data }: { headerName: string; data: BetaHeaderUpdate }) =>
      betaHeadersApi.update(headerName, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['betaHeaders'] });
    },
  });
}

export function useDeleteBetaHeader() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (headerName: string) => betaHeadersApi.delete(headerName),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['betaHeaders'] });
    },
  });
}
