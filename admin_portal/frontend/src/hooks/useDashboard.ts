import { useQuery } from '@tanstack/react-query';
import { dashboardApi } from '../services/api';

export function useDashboardStats() {
  return useQuery({
    queryKey: ['dashboardStats'],
    queryFn: () => dashboardApi.getStats(),
    refetchInterval: 60000, // Refresh every minute
  });
}

export function useDailyUsage(days: number) {
  return useQuery({
    queryKey: ['dailyUsage', days],
    queryFn: () => dashboardApi.getDailyUsage(days),
    staleTime: 5 * 60 * 1000, // 5 min — avoids re-scanning the usage table on every view
  });
}
