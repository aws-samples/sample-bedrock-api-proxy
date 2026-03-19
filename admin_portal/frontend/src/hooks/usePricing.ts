import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { pricingApi } from '../services/api';
import type { PricingCreate, PricingUpdate } from '../types';

export function usePricing(params?: {
  limit?: number;
  provider?: string;
  status?: string;
  search?: string;
}) {
  return useQuery({
    queryKey: ['pricing', params],
    queryFn: () => pricingApi.list(params),
  });
}

export function usePricingProviders() {
  return useQuery({
    queryKey: ['pricingProviders'],
    queryFn: () => pricingApi.getProviders(),
  });
}

export function usePricingItem(modelId: string) {
  return useQuery({
    queryKey: ['pricing', modelId],
    queryFn: () => pricingApi.get(modelId),
    enabled: !!modelId,
  });
}

export function useCreatePricing() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (data: PricingCreate) => pricingApi.create(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['pricing'] });
      queryClient.invalidateQueries({ queryKey: ['pricingProviders'] });
    },
  });
}

export function useUpdatePricing() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({ modelId, data }: { modelId: string; data: PricingUpdate }) =>
      pricingApi.update(modelId, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['pricing'] });
    },
  });
}

export function useDeletePricing() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (modelId: string) => pricingApi.delete(modelId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['pricing'] });
      queryClient.invalidateQueries({ queryKey: ['pricingProviders'] });
    },
  });
}
