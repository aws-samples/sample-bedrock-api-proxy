import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { modelMappingApi } from '../services/api';
import type {
  ModelMappingCreate,
  ModelMappingUpdate,
  ModelMappingSyncRequest,
  SpeedTestLatestResponse,
} from '../types';

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

export function useModelMappingSyncStatus() {
  return useQuery({
    queryKey: ['modelMappingSyncStatus'],
    queryFn: () => modelMappingApi.syncStatus(),
  });
}

export function useSyncModelMappings() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (data?: ModelMappingSyncRequest) => modelMappingApi.sync(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['modelMappings'] });
      queryClient.invalidateQueries({ queryKey: ['modelMappingSyncStatus'] });
    },
  });
}

// ---------------------------------------------------------------------------
// Speed test (TTFT / OTPS per Bedrock model ID)
// ---------------------------------------------------------------------------

/** Latest speed-test record for every Bedrock model ID in the mapping list. */
export function useSpeedTestLatest() {
  return useQuery({
    queryKey: ['speedTestLatest'],
    queryFn: () => modelMappingApi.speedTestLatest(),
    staleTime: 60 * 1000,
  });
}

/**
 * Last N speed-test runs for one Bedrock model ID (newest first).
 * Fetched lazily (enabled=false until hover), like useApiKeyDailyUsage.
 */
export function useSpeedTestHistory(bedrockModelId: string, enabled = true, limit = 10) {
  return useQuery({
    queryKey: ['speedTestHistory', bedrockModelId, limit],
    queryFn: () => modelMappingApi.speedTestHistory(bedrockModelId, limit),
    enabled: enabled && !!bedrockModelId,
    staleTime: 60 * 1000,
  });
}

/**
 * Runs one speed test for a Bedrock model ID. Resolves with the persisted
 * record even when the run itself failed (status === 'error'); rejects only
 * on transport / HTTP errors (503 = admin backend misconfigured).
 */
export function useRunSpeedTest() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (bedrockModelId: string) => modelMappingApi.runSpeedTest(bedrockModelId),
    onSuccess: (record) => {
      // Show the new result immediately; the invalidation below reconciles with the server.
      queryClient.setQueryData<SpeedTestLatestResponse>(['speedTestLatest'], (old) => ({
        items: { ...(old?.items ?? {}), [record.bedrock_model_id]: record },
      }));
    },
    onSettled: (_record, _error, bedrockModelId) => {
      queryClient.invalidateQueries({ queryKey: ['speedTestLatest'] });
      queryClient.invalidateQueries({ queryKey: ['speedTestHistory', bedrockModelId] });
    },
  });
}
