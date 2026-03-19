import { useQuery } from '@tanstack/react-query';
import { dashboardApi } from '../services/api';

export function useDashboardStats() {
  return useQuery({
    queryKey: ['dashboardStats'],
    queryFn: () => dashboardApi.getStats(),
    refetchInterval: 60000, // Refresh every minute
  });
}
