import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { modelMappingApi } from '../services/api';
import type { ModelMappingCreate, ModelMappingUpdate } from '../types';

export function useModelMappings(params?: { search?: string }) {
  return useQuery({
    queryKey: ['modelMappings', params],
    queryFn: () => modelMappingApi.list(params),
  });
}

export function useModelMapping(anthropicModelId: string) {
  return useQuery({
    queryKey: ['modelMapping', anthropicModelId],
    queryFn: () => modelMappingApi.get(anthropicModelId),
    enabled: !!anthropicModelId,
  });
}

export function useCreateModelMapping() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (data: ModelMappingCreate) => modelMappingApi.create(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['modelMappings'] });
    },
  });
}

export function useUpdateModelMapping() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({ anthropicModelId, data }: { anthropicModelId: string; data: ModelMappingUpdate }) =>
      modelMappingApi.update(anthropicModelId, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['modelMappings'] });
    },
  });
}

export function useDeleteModelMapping() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (anthropicModelId: string) => modelMappingApi.delete(anthropicModelId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['modelMappings'] });
    },
  });
}
