import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { routingApi } from '../services/api';
import type { RoutingRuleCreate, RoutingRuleUpdate, SmartRoutingConfig } from '../types';

export function useRoutingRules() {
  return useQuery({
    queryKey: ['routingRules'],
    queryFn: () => routingApi.listRules(),
  });
}

export function useCreateRoutingRule() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (data: RoutingRuleCreate) => routingApi.createRule(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['routingRules'] });
    },
  });
}

export function useUpdateRoutingRule() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ ruleId, data }: { ruleId: string; data: RoutingRuleUpdate }) =>
      routingApi.updateRule(ruleId, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['routingRules'] });
    },
  });
}

export function useDeleteRoutingRule() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (ruleId: string) => routingApi.deleteRule(ruleId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['routingRules'] });
    },
  });
}

export function useReorderRoutingRules() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (ruleIds: string[]) => routingApi.reorderRules(ruleIds),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['routingRules'] });
    },
  });
}

export function useSmartRoutingConfig() {
  return useQuery({
    queryKey: ['smartRoutingConfig'],
    queryFn: () => routingApi.getSmartConfig(),
  });
}

export function useUpdateSmartRoutingConfig() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (data: SmartRoutingConfig) => routingApi.updateSmartConfig(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['smartRoutingConfig'] });
    },
  });
}
