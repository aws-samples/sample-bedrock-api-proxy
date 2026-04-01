import { useState, useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import {
  useApiKeys,
  useCreateApiKey,
  useUpdateApiKey,
  useDeactivateApiKey,
  useReactivateApiKey,
  useDeleteApiKey,
  useDashboardStats,
  useProviders,
} from '../hooks';
import type { ApiKey, ApiKeyCreate, ApiKeyUpdate } from '../types';
import { formatTokens } from '../utils';

// Modal Component
function Modal({
  isOpen,
  onClose,
  title,
  children,
}: {
  isOpen: boolean;
  onClose: () => void;
  title: string;
  children: React.ReactNode;
}) {
  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-50 overflow-y-auto">
      <div className="flex min-h-full items-center justify-center p-4">
        <div className="fixed inset-0 bg-black/50 backdrop-blur-sm" onClick={onClose}></div>
        <div className="relative w-full max-w-lg bg-surface-dark border border-border-dark rounded-xl shadow-2xl">
          <div className="flex items-center justify-between px-6 py-4 border-b border-border-dark">
            <h2 className="text-lg font-bold text-white">{title}</h2>
            <button
              onClick={onClose}
              className="text-slate-400 hover:text-white transition-colors"
            >
              <span className="material-symbols-outlined">close</span>
            </button>
          </div>
          <div className="p-6">{children}</div>
        </div>
      </div>
    </div>
  );
}

// API Key Form Component
function ApiKeyForm({
  initialData,
  onSubmit,
  onCancel,
  isLoading,
}: {
  initialData?: ApiKey;
  onSubmit: (data: ApiKeyCreate | ApiKeyUpdate) => void;
  onCancel: () => void;
  isLoading: boolean;
}) {
  const { t } = useTranslation();
  const isEdit = !!initialData;
  const { data: providersData } = useProviders();

  const [formData, setFormData] = useState({
    user_id: initialData?.user_id || '',
    name: initialData?.name || '',
    // Use owner_name if set, otherwise fall back to user_id (same as list display)
    owner_name: initialData?.owner_name || initialData?.user_id || '',
    monthly_budget: initialData?.monthly_budget || 0,
    rate_limit: initialData?.rate_limit || 1000,
    service_tier: initialData?.service_tier || 'default',
    cache_ttl: initialData?.cache_ttl || '',
    routing_strategy: initialData?.routing_strategy || 'off',
    compression_strategy: initialData?.compression_strategy || 'off',
    provider_id: initialData?.provider_id || '',
  });

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const submitData = {
      ...formData,
      provider_id: formData.provider_id || null,
    };
    onSubmit(submitData);
  };

  return (
    <form onSubmit={handleSubmit} className="flex flex-col gap-4">
      {!isEdit && (
        <div>
          <label className="block text-sm font-medium text-slate-300 mb-1">
            {t('apiKeys.form.userId')}
          </label>
          <input
            type="text"
            value={formData.user_id}
            onChange={(e) => setFormData({ ...formData, user_id: e.target.value })}
            className="w-full px-3 py-2 bg-input-bg border border-border-dark rounded-lg text-white focus:border-primary focus:ring-1 focus:ring-primary"
            required
          />
        </div>
      )}

      <div>
        <label className="block text-sm font-medium text-slate-300 mb-1">
          {t('apiKeys.form.name')}
        </label>
        <input
          type="text"
          value={formData.name}
          onChange={(e) => setFormData({ ...formData, name: e.target.value })}
          className="w-full px-3 py-2 bg-input-bg border border-border-dark rounded-lg text-white focus:border-primary focus:ring-1 focus:ring-primary"
          required
        />
      </div>

      <div>
        <label className="block text-sm font-medium text-slate-300 mb-1">
          {t('apiKeys.form.ownerName')}
        </label>
        <input
          type="text"
          value={formData.owner_name}
          onChange={(e) => setFormData({ ...formData, owner_name: e.target.value })}
          className="w-full px-3 py-2 bg-input-bg border border-border-dark rounded-lg text-white focus:border-primary focus:ring-1 focus:ring-primary"
        />
      </div>

      <div className="grid grid-cols-2 gap-4">
        <div>
          <label className="block text-sm font-medium text-slate-300 mb-1">
            {t('apiKeys.form.monthlyBudget')}
          </label>
          <input
            type="number"
            step="0.01"
            value={formData.monthly_budget}
            onChange={(e) =>
              setFormData({ ...formData, monthly_budget: parseFloat(e.target.value) || 0 })
            }
            className="w-full px-3 py-2 bg-input-bg border border-border-dark rounded-lg text-white focus:border-primary focus:ring-1 focus:ring-primary"
          />
        </div>

        <div>
          <label className="block text-sm font-medium text-slate-300 mb-1">
            {t('apiKeys.form.rateLimit')}
          </label>
          <input
            type="number"
            value={formData.rate_limit}
            onChange={(e) =>
              setFormData({ ...formData, rate_limit: parseInt(e.target.value) || 0 })
            }
            className="w-full px-3 py-2 bg-input-bg border border-border-dark rounded-lg text-white focus:border-primary focus:ring-1 focus:ring-primary"
          />
        </div>
      </div>

      <div>
        <label className="block text-sm font-medium text-slate-300 mb-1">
          {t('apiKeys.form.serviceTier')}
        </label>
        <select
          value={formData.service_tier}
          onChange={(e) => setFormData({ ...formData, service_tier: e.target.value })}
          className="w-full px-3 py-2 bg-input-bg border border-border-dark rounded-lg text-white focus:border-primary focus:ring-1 focus:ring-primary"
        >
          <option value="default">{t('apiKeys.serviceTiers.default')}</option>
          <option value="flex">{t('apiKeys.serviceTiers.flex')}</option>
          <option value="priority">{t('apiKeys.serviceTiers.priority')}</option>
          <option value="reserved">{t('apiKeys.serviceTiers.reserved')}</option>
        </select>
        <p className="mt-1 text-xs text-slate-500">
          {formData.service_tier === 'flex' && t('apiKeys.serviceTiers.flexDesc')}
          {formData.service_tier === 'priority' && t('apiKeys.serviceTiers.priorityDesc')}
          {formData.service_tier === 'default' && t('apiKeys.serviceTiers.defaultDesc')}
          {formData.service_tier === 'reserved' && t('apiKeys.serviceTiers.reservedDesc')}
        </p>
      </div>

      <div>
        <label className="block text-sm font-medium text-slate-300 mb-1">
          {t('apiKeys.form.cacheTtl')}
        </label>
        <select
          value={formData.cache_ttl}
          onChange={(e) => setFormData({ ...formData, cache_ttl: e.target.value })}
          className="w-full px-3 py-2 bg-input-bg border border-border-dark rounded-lg text-white focus:border-primary focus:ring-1 focus:ring-primary"
        >
          <option value="">{t('apiKeys.cacheTtlOptions.default')}</option>
          <option value="5m">{t('apiKeys.cacheTtlOptions.5m')}</option>
          <option value="1h">{t('apiKeys.cacheTtlOptions.1h')}</option>
        </select>
        <p className="mt-1 text-xs text-slate-500">
          {!formData.cache_ttl && t('apiKeys.cacheTtlDesc.default')}
          {formData.cache_ttl === '5m' && t('apiKeys.cacheTtlDesc.5m')}
          {formData.cache_ttl === '1h' && t('apiKeys.cacheTtlDesc.1h')}
        </p>
      </div>

      <div>
        <label className="block text-sm font-medium text-slate-300 mb-1">Provider</label>
        <select
          value={formData.provider_id}
          onChange={(e) => setFormData({ ...formData, provider_id: e.target.value })}
          className="w-full px-3 py-2 bg-input-bg border border-border-dark rounded-lg text-white focus:border-primary focus:ring-1 focus:ring-primary"
        >
          <option value="">Default (Environment Config)</option>
          {providersData?.items
            ?.filter((p) => p.is_active)
            .map((p) => (
              <option key={p.provider_id} value={p.provider_id}>
                {p.name} ({p.aws_region})
              </option>
            ))}
        </select>
        <p className="mt-1 text-xs text-slate-500">
          Select a Bedrock provider account for this API key, or use the default environment config.
        </p>
      </div>

      {/* TODO: Routing Strategy and Compression Strategy hidden — re-enable after optimization */}

      <div className="flex gap-3 mt-4">
        <button
          type="button"
          onClick={onCancel}
          className="flex-1 px-4 py-2 border border-border-dark rounded-lg text-slate-300 hover:bg-surface-dark transition-colors"
        >
          {t('common.cancel')}
        </button>
        <button
          type="submit"
          disabled={isLoading}
          className="flex-1 px-4 py-2 bg-primary hover:bg-blue-600 text-white rounded-lg font-medium transition-colors disabled:opacity-50"
        >
          {isLoading ? t('common.loading') : t('common.save')}
        </button>
      </div>
    </form>
  );
}

export default function ApiKeys() {
  const { t } = useTranslation();
  const [search, setSearch] = useState('');
  const [statusFilter, setStatusFilter] = useState<string>('');
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [editingKey, setEditingKey] = useState<ApiKey | null>(null);
  const [copiedKey, setCopiedKey] = useState<string | null>(null);

  const { data, isLoading, error } = useApiKeys({
    status: statusFilter || undefined,
    search: search || undefined,
  });

  const { data: stats } = useDashboardStats();
  const { data: providersData } = useProviders();
  const createMutation = useCreateApiKey();
  const updateMutation = useUpdateApiKey();
  const deactivateMutation = useDeactivateApiKey();
  const reactivateMutation = useReactivateApiKey();
  const deleteMutation = useDeleteApiKey();

  const handleCreate = async (data: ApiKeyCreate | ApiKeyUpdate) => {
    await createMutation.mutateAsync(data as ApiKeyCreate);
    setShowCreateModal(false);
  };

  const handleUpdate = async (data: ApiKeyCreate | ApiKeyUpdate) => {
    if (editingKey) {
      await updateMutation.mutateAsync({ apiKey: editingKey.api_key, data });
      setEditingKey(null);
    }
  };

  const handleDeactivate = async (apiKey: string) => {
    if (confirm(t('apiKeys.confirmRevoke'))) {
      await deactivateMutation.mutateAsync(apiKey);
    }
  };

  const handleReactivate = async (apiKey: string) => {
    await reactivateMutation.mutateAsync(apiKey);
  };

  const handleDelete = async (apiKey: string) => {
    if (confirm(t('apiKeys.confirmDelete'))) {
      await deleteMutation.mutateAsync(apiKey);
    }
  };

  const handleExport = useCallback(() => {
    const apiKeys = data?.items || [];
    if (apiKeys.length === 0) {
      alert('No data to export');
      return;
    }

    const headers = ['API Key', 'Name', 'Owner', 'User ID', 'Status', 'Monthly Budget', 'Budget Used (MTD)', 'Budget Used (Total)', 'Rate Limit', 'Service Tier', 'Cache TTL', 'Created At', 'Total Requests', 'Total Input Tokens', 'Total Output Tokens', 'Total Cached Tokens'];
    
    const rows = apiKeys.map((key) => [
      key.api_key,
      key.name,
      key.owner_name || key.user_id,
      key.user_id,
      key.is_active ? 'Active' : 'Inactive',
      key.monthly_budget || 0,
      key.budget_used_mtd || 0,
      key.budget_used || 0,
      key.rate_limit || 0,
      key.service_tier || 'default',
      key.cache_ttl || 'default',
      new Date((key.created_at as number) * 1000).toISOString(),
      key.total_requests || 0,
      key.total_input_tokens || 0,
      key.total_output_tokens || 0,
      key.total_cached_tokens || 0,
    ]);

    const csvContent = [
      headers.join(','),
      ...rows.map((row) =>
        row.map((cell) => {
          const cellStr = String(cell);
          if (cellStr.includes(',') || cellStr.includes('"') || cellStr.includes('\n')) {
            return `"${cellStr.replace(/"/g, '""')}"`;
          }
          return cellStr;
        }).join(',')
      ),
    ].join('\n');

    const blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' });
    const link = document.createElement('a');
    const url = URL.createObjectURL(blob);
    const timestamp = new Date().toISOString().split('T')[0];
    
    link.setAttribute('href', url);
    link.setAttribute('download', `api-keys-export-${timestamp}.csv`);
    link.style.visibility = 'hidden';
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
  }, [data]);

  const copyToClipboard = useCallback(async (text: string) => {
    try {
      // Try modern Clipboard API first (requires HTTPS)
      if (navigator.clipboard && window.isSecureContext) {
        await navigator.clipboard.writeText(text);
      } else {
        // Fallback for HTTP: use textarea + execCommand
        const textArea = document.createElement('textarea');
        textArea.value = text;
        textArea.style.position = 'fixed';
        textArea.style.left = '-999999px';
        textArea.style.top = '-999999px';
        document.body.appendChild(textArea);
        textArea.focus();
        textArea.select();
        document.execCommand('copy');
        document.body.removeChild(textArea);
      }
      // Show success feedback
      setCopiedKey(text);
      setTimeout(() => setCopiedKey(null), 2000);
    } catch (err) {
      console.error('Failed to copy:', err);
    }
  }, []);

  const getInitials = (name: string) => {
    return name
      .split(' ')
      .map((n) => n[0])
      .join('')
      .toUpperCase()
      .slice(0, 2);
  };

  const formatKey = (key: string) => {
    return `${key.slice(0, 6)}...${key.slice(-4)}`;
  };

  const getLastMonthBudget = (budgetHistory?: string): number | null => {
    if (!budgetHistory) return null;
    try {
      const history = JSON.parse(budgetHistory) as Record<string, number>;
      const months = Object.keys(history).sort().reverse();
      // Get the most recent month in history (which should be the previous month)
      if (months.length > 0) {
        return history[months[0]];
      }
      return null;
    } catch {
      return null;
    }
  };

  const getLastMonthLabel = (budgetHistory?: string): string => {
    if (!budgetHistory) return '';
    try {
      const history = JSON.parse(budgetHistory) as Record<string, number>;
      const months = Object.keys(history).sort().reverse();
      if (months.length > 0) {
        // Convert YYYY-MM to a more readable format (e.g., "Dec 2025")
        const [year, month] = months[0].split('-');
        const date = new Date(parseInt(year), parseInt(month) - 1);
        return date.toLocaleDateString('en-US', { month: 'short', year: 'numeric' });
      }
      return '';
    } catch {
      return '';
    }
  };

  const budgetPercent = stats
    ? Math.round((stats.total_budget_used / Math.max(stats.total_budget, 1)) * 100)
    : 0;

  if (error) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="text-center text-red-400">
          <span className="material-symbols-outlined text-4xl mb-2">error</span>
          <p>Failed to load API keys</p>
        </div>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-8">
      {/* Page Heading */}
      <div className="flex flex-col md:flex-row md:items-end justify-between gap-4">
        <div className="flex flex-col gap-2">
          <h1 className="text-3xl md:text-4xl font-bold text-white tracking-tight">
            {t('apiKeys.title')}
          </h1>
          <p className="text-slate-400 text-base">{t('apiKeys.subtitle')}</p>
        </div>
        <div className="flex gap-3">
          <button 
            onClick={handleExport}
            className="flex items-center justify-center gap-2 h-10 px-4 rounded-lg bg-surface-dark border border-border-dark text-white text-sm font-medium hover:bg-border-dark transition-colors"
          >
            <span className="material-symbols-outlined text-[20px]">file_download</span>
            {t('apiKeys.export')}
          </button>
          <button
            onClick={() => setShowCreateModal(true)}
            className="flex items-center justify-center gap-2 h-10 px-4 rounded-lg bg-primary text-white text-sm font-bold shadow-lg shadow-primary/25 hover:bg-primary/90 transition-all"
          >
            <span className="material-symbols-outlined text-[20px]">add</span>
            {t('apiKeys.createNewKey')}
          </button>
        </div>
      </div>

      {/* Stats Cards */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <div className="bg-surface-dark border border-border-dark rounded-xl p-5 flex flex-col gap-1 shadow-sm">
          <div className="flex items-center justify-between">
            <span className="text-slate-400 text-sm font-medium">
              {t('dashboard.totalBudgetSpent')}
            </span>
            <span className="material-symbols-outlined text-emerald-500">trending_up</span>
          </div>
          <div className="flex items-baseline gap-2 mt-2">
            <span className="text-2xl font-bold text-white">
              ${stats?.total_budget_used.toFixed(2) || '0.00'}
            </span>
            <span className="text-sm text-slate-400">
              / ${stats?.total_budget.toFixed(2) || '0.00'}
            </span>
          </div>
          <div className="w-full bg-border-dark h-1.5 rounded-full mt-3 overflow-hidden">
            <div
              className="bg-emerald-500 h-full rounded-full"
              style={{ width: `${Math.min(budgetPercent, 100)}%` }}
            ></div>
          </div>
        </div>

        <div className="bg-surface-dark border border-border-dark rounded-xl p-5 flex flex-col gap-1 shadow-sm">
          <div className="flex items-center justify-between">
            <span className="text-slate-400 text-sm font-medium">{t('dashboard.activeKeys')}</span>
            <span className="material-symbols-outlined text-primary">key</span>
          </div>
          <div className="flex items-baseline gap-2 mt-2">
            <span className="text-2xl font-bold text-white">{stats?.active_api_keys || 0}</span>
            {stats && stats.new_keys_this_week > 0 && (
              <span className="text-sm text-emerald-500 font-medium">
                +{stats.new_keys_this_week} this week
              </span>
            )}
          </div>
          <p className="text-xs text-slate-500 mt-2">
            {stats?.revoked_api_keys || 0} revoked
          </p>
        </div>

        <div className="bg-surface-dark border border-border-dark rounded-xl p-5 flex flex-col gap-1 shadow-sm">
          <div className="flex items-center justify-between">
            <span className="text-slate-400 text-sm font-medium">
              {t('dashboard.systemStatus')}
            </span>
            <span className="material-symbols-outlined text-emerald-500">check_circle</span>
          </div>
          <div className="flex items-baseline gap-2 mt-2">
            <span className="text-2xl font-bold text-white">{t('dashboard.operational')}</span>
          </div>
          <p className="text-xs text-slate-500 mt-2">All systems normal</p>
        </div>
      </div>

      {/* Warning: Models without pricing */}
      {stats?.models_without_pricing && stats.models_without_pricing.length > 0 && (
        <div className="bg-amber-500/10 border border-amber-500/30 rounded-xl p-4 flex items-start gap-3">
          <span className="material-symbols-outlined text-amber-500 mt-0.5">warning</span>
          <div className="flex-1">
            <h3 className="text-sm font-semibold text-amber-500 mb-1">
              {t('apiKeys.warnings.modelsWithoutPricing')}
            </h3>
            <p className="text-xs text-slate-400 mb-2">
              {t('apiKeys.warnings.modelsWithoutPricingDesc')}
            </p>
            <div className="flex flex-wrap gap-2">
              {stats.models_without_pricing.map((model) => (
                <span
                  key={model}
                  className="inline-flex items-center px-2 py-1 rounded bg-amber-500/20 text-amber-300 text-xs font-mono"
                >
                  {model}
                </span>
              ))}
            </div>
          </div>
        </div>
      )}

      {/* Filters */}
      <div className="flex flex-col sm:flex-row gap-4 justify-between items-center bg-surface-dark p-2 rounded-xl border border-border-dark">
        <div className="relative w-full sm:max-w-md">
          <div className="absolute inset-y-0 left-0 pl-3 flex items-center pointer-events-none">
            <span className="material-symbols-outlined text-slate-400">search</span>
          </div>
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="block w-full pl-10 pr-3 py-2.5 border-none rounded-lg bg-transparent text-white placeholder-slate-400 focus:ring-0 sm:text-sm"
            placeholder={t('apiKeys.searchPlaceholder')}
          />
        </div>

        <div className="flex items-center gap-2 w-full sm:w-auto px-2">
          <select
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
            className="px-3 py-2 bg-transparent border border-border-dark rounded-lg text-slate-300 text-sm focus:border-primary focus:ring-0"
          >
            <option value="">{t('apiKeys.statusFilter')}: {t('common.all')}</option>
            <option value="active">{t('common.active')}</option>
            <option value="revoked">{t('common.revoked')}</option>
          </select>
        </div>
      </div>

      {/* Data Table */}
      <div className="overflow-hidden rounded-xl border border-border-dark bg-surface-dark shadow-sm">
        <div className="overflow-x-auto">
          <table className="w-full text-left border-collapse">
            <thead className="bg-[#151b28] border-b border-border-dark">
              <tr>
                <th className="px-6 py-4 text-xs font-semibold text-slate-400 uppercase tracking-wider">
                  {t('apiKeys.keyNameId')}
                </th>
                <th className="px-6 py-4 text-xs font-semibold text-slate-400 uppercase tracking-wider">
                  {t('apiKeys.owner')}
                </th>
                <th className="px-6 py-4 text-xs font-semibold text-slate-400 uppercase tracking-wider">
                  {t('apiKeys.tokenUsage')}
                </th>
                <th className="px-6 py-4 text-xs font-semibold text-slate-400 uppercase tracking-wider w-1/5">
                  {t('apiKeys.monthlyBudget')}
                </th>
                <th className="px-6 py-4 text-xs font-semibold text-slate-400 uppercase tracking-wider">
                  {t('apiKeys.lastMonth')}
                </th>
                <th className="px-6 py-4 text-xs font-semibold text-slate-400 uppercase tracking-wider">
                  {t('apiKeys.limits')}
                </th>
                <th className="px-6 py-4 text-xs font-semibold text-slate-400 uppercase tracking-wider hidden lg:table-cell">
                  {t('apiKeys.form.serviceTier')}
                </th>
                <th className="px-6 py-4 text-xs font-semibold text-slate-400 uppercase tracking-wider hidden lg:table-cell">
                  {t('apiKeys.form.cacheTtl')}
                </th>
                <th className="px-6 py-4 text-xs font-semibold text-slate-400 uppercase tracking-wider hidden lg:table-cell">
                  Provider
                </th>
                <th className="px-6 py-4 text-xs font-semibold text-slate-400 uppercase tracking-wider">
                  {t('common.status')}
                </th>
                <th className="px-6 py-4 text-xs font-semibold text-slate-400 uppercase tracking-wider text-right">
                  {t('common.actions')}
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border-dark">
              {isLoading ? (
                <tr>
                  <td colSpan={11} className="px-6 py-12 text-center">
                    <span className="material-symbols-outlined animate-spin text-4xl text-primary">
                      progress_activity
                    </span>
                  </td>
                </tr>
              ) : data?.items.length === 0 ? (
                <tr>
                  <td colSpan={11} className="px-6 py-12 text-center text-slate-400">
                    No API keys found
                  </td>
                </tr>
              ) : (
                data?.items.map((key) => {
                  // Use budget_used_mtd for comparison with monthly_budget
                  const mtdBudget = key.budget_used_mtd ?? key.budget_used ?? 0;
                  const usedPercent = key.monthly_budget
                    ? Math.round((mtdBudget / key.monthly_budget) * 100)
                    : 0;

                  return (
                    <tr
                      key={key.api_key}
                      className={`group hover:bg-[#1e2536] transition-colors ${
                        !key.is_active ? 'opacity-60' : ''
                      }`}
                    >
                      <td className="px-6 py-4 whitespace-nowrap">
                        <div className="flex flex-col">
                          <span
                            className={`text-sm font-bold ${
                              key.is_active ? 'text-white' : 'text-slate-400 line-through'
                            }`}
                          >
                            {key.name}
                          </span>
                          <div className="flex items-center gap-1.5 mt-1">
                            <span className="text-xs font-mono text-slate-500 bg-black/30 px-1.5 py-0.5 rounded">
                              {formatKey(key.api_key)}
                            </span>
                            <button
                              onClick={() => copyToClipboard(key.api_key)}
                              className={`transition-colors ${
                                copiedKey === key.api_key
                                  ? 'text-green-400'
                                  : 'text-slate-400 hover:text-primary'
                              }`}
                              title={copiedKey === key.api_key ? t('apiKeys.copied') : t('apiKeys.copyKey')}
                            >
                              <span className="material-symbols-outlined text-[14px]">
                                {copiedKey === key.api_key ? 'check' : 'content_copy'}
                              </span>
                            </button>
                          </div>
                        </div>
                      </td>
                      <td className="px-6 py-4 whitespace-nowrap">
                        <div className="flex items-center gap-3">
                          <div className="size-8 rounded-full bg-blue-900/50 flex items-center justify-center text-blue-200 text-xs font-bold border border-blue-500/20">
                            {getInitials(key.owner_name || key.user_id)}
                          </div>
                          <span className="text-sm font-medium text-white">
                            {key.owner_name || key.user_id}
                          </span>
                        </div>
                      </td>
                      <td className="px-6 py-4 whitespace-nowrap">
                        <div className="flex flex-col gap-1">
                          <div className="flex items-center gap-2">
                            <span className="material-symbols-outlined text-[14px] text-emerald-500">
                              arrow_upward
                            </span>
                            <span className="text-xs text-white font-medium">
                              {formatTokens(key.total_input_tokens)}
                            </span>
                            <span className="text-xs text-slate-500">{t('apiKeys.inputTokens')}</span>
                          </div>
                          <div className="flex items-center gap-2">
                            <span className="material-symbols-outlined text-[14px] text-blue-400">
                              arrow_downward
                            </span>
                            <span className="text-xs text-white font-medium">
                              {formatTokens(key.total_output_tokens)}
                            </span>
                            <span className="text-xs text-slate-500">{t('apiKeys.outputTokens')}</span>
                          </div>
                          <div className="flex items-center gap-2">
                            <span className="material-symbols-outlined text-[14px] text-purple-400">
                              cached
                            </span>
                            <span className="text-xs text-white font-medium">
                              {formatTokens(key.total_cached_tokens)}
                            </span>
                            <span className="text-xs text-slate-500">{t('apiKeys.cacheRead')}</span>
                          </div>
                          <div className="flex items-center gap-2">
                            <span className="material-symbols-outlined text-[14px] text-amber-400">
                              edit_note
                            </span>
                            <span className="text-xs text-white font-medium">
                              {formatTokens(key.total_cache_write_tokens)}
                            </span>
                            <span className="text-xs text-slate-500">{t('apiKeys.cacheWrite')}</span>
                          </div>
                          <span className="text-[10px] text-slate-500">
                            {(key.total_requests || 0).toLocaleString()} {t('apiKeys.requests')}
                          </span>
                        </div>
                      </td>
                      <td className="px-6 py-4 whitespace-nowrap align-middle">
                        <div className="w-full flex flex-col gap-1.5">
                          <div className="flex justify-between text-xs">
                            <span className="text-white font-medium">
                              ${mtdBudget.toFixed(2)}
                            </span>
                            <span className="text-slate-500">
                              of ${(key.monthly_budget || 0).toFixed(2)}
                            </span>
                          </div>
                          <div className="w-full bg-border-dark h-2 rounded-full overflow-hidden">
                            <div
                              className={`h-full rounded-full ${
                                usedPercent >= 100
                                  ? 'bg-red-500'
                                  : usedPercent > 90
                                  ? 'bg-red-500'
                                  : usedPercent > 75
                                  ? 'bg-orange-500'
                                  : 'bg-primary'
                              }`}
                              style={{ width: `${Math.min(usedPercent, 100)}%` }}
                            ></div>
                          </div>
                          <div className="flex justify-between text-[10px] text-slate-500">
                            <span>{usedPercent}% {t('apiKeys.used')}</span>
                            <span title={t('apiKeys.totalBudgetUsed')}>
                              {t('apiKeys.total')}: ${(key.budget_used || 0).toFixed(2)}
                            </span>
                          </div>
                        </div>
                      </td>
                      <td className="px-6 py-4 whitespace-nowrap">
                        {(() => {
                          const lastMonthBudget = getLastMonthBudget(key.budget_history);
                          const lastMonthLabel = getLastMonthLabel(key.budget_history);
                          if (lastMonthBudget !== null) {
                            return (
                              <div className="flex flex-col gap-0.5">
                                <span className="text-sm text-white font-medium">
                                  ${lastMonthBudget.toFixed(2)}
                                </span>
                                <span className="text-[10px] text-slate-500">
                                  {lastMonthLabel}
                                </span>
                              </div>
                            );
                          }
                          return (
                            <span className="text-xs text-slate-500">—</span>
                          );
                        })()}
                      </td>
                      <td className="px-6 py-4 whitespace-nowrap">
                        <span className="text-xs text-white font-medium">
                          {(key.rate_limit || 0).toLocaleString()} req/min
                        </span>
                      </td>
                      <td className="px-6 py-4 whitespace-nowrap hidden lg:table-cell">
                        <span className={`px-2 py-0.5 rounded text-xs font-medium ${
                          key.service_tier === 'flex'
                            ? 'bg-cyan-900/30 text-cyan-400 border border-cyan-800'
                            : key.service_tier === 'priority'
                            ? 'bg-amber-900/30 text-amber-400 border border-amber-800'
                            : 'bg-slate-800 text-slate-400 border border-slate-700'
                        }`}>
                          {t(`apiKeys.serviceTiers.${key.service_tier || 'default'}`)}
                        </span>
                      </td>
                      <td className="px-6 py-4 whitespace-nowrap hidden lg:table-cell">
                        <span className={`px-2 py-0.5 rounded text-xs font-medium ${
                          key.cache_ttl === '1h'
                            ? 'bg-blue-900/30 text-blue-400 border border-blue-800'
                            : key.cache_ttl === '5m'
                            ? 'bg-slate-800 text-slate-400 border border-slate-700'
                            : 'text-slate-500'
                        }`}>
                          {key.cache_ttl
                            ? t(`apiKeys.cacheTtlOptions.${key.cache_ttl}`)
                            : t('apiKeys.cacheTtlOptions.default')}
                        </span>
                      </td>
                      <td className="px-6 py-4 whitespace-nowrap hidden lg:table-cell">
                        {key.provider_id ? (
                          <span className="px-2 py-0.5 rounded text-xs font-medium bg-violet-900/30 text-violet-400 border border-violet-800">
                            {providersData?.items.find(p => p.provider_id === key.provider_id)?.name || key.provider_id}
                          </span>
                        ) : (
                          <span className="text-xs text-slate-500">Default</span>
                        )}
                      </td>
                      <td className="px-6 py-4 whitespace-nowrap">
                        <span
                          className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium border ${
                            key.is_active
                              ? 'bg-emerald-500/10 text-emerald-500 border-emerald-500/20'
                              : key.deactivated_reason === 'budget_exceeded'
                              ? 'bg-red-500/10 text-red-400 border-red-500/20'
                              : 'bg-slate-100 dark:bg-border-dark text-slate-500 border-slate-200 dark:border-slate-700'
                          }`}
                        >
                          <span
                            className={`size-1.5 rounded-full ${
                              key.is_active
                                ? 'bg-emerald-500'
                                : key.deactivated_reason === 'budget_exceeded'
                                ? 'bg-red-400'
                                : 'bg-slate-500'
                            }`}
                          ></span>
                          {key.is_active
                            ? t('common.active')
                            : key.deactivated_reason === 'budget_exceeded'
                            ? t('apiKeys.budgetExceeded')
                            : t('common.revoked')}
                        </span>
                      </td>
                      <td className="px-6 py-4 whitespace-nowrap text-right">
                        <div className="flex items-center justify-end gap-2 opacity-0 group-hover:opacity-100 transition-opacity">
                          <button
                            onClick={() => setEditingKey(key)}
                            className="p-2 text-slate-400 hover:text-white hover:bg-border-dark rounded-lg transition-colors"
                            title={t('apiKeys.editLimits')}
                          >
                            <span className="material-symbols-outlined text-[20px]">edit</span>
                          </button>
                          {key.is_active ? (
                            <button
                              onClick={() => handleDeactivate(key.api_key)}
                              className="p-2 text-slate-400 hover:text-red-400 hover:bg-red-500/10 rounded-lg transition-colors"
                              title={t('apiKeys.revokeKey')}
                            >
                              <span className="material-symbols-outlined text-[20px]">block</span>
                            </button>
                          ) : (
                            <>
                              <button
                                onClick={() => handleReactivate(key.api_key)}
                                className="p-2 text-slate-400 hover:text-white hover:bg-border-dark rounded-lg transition-colors"
                                title={t('apiKeys.reactivate')}
                              >
                                <span className="material-symbols-outlined text-[20px]">
                                  refresh
                                </span>
                              </button>
                              <button
                                onClick={() => handleDelete(key.api_key)}
                                className="p-2 text-slate-400 hover:text-red-400 hover:bg-red-500/10 rounded-lg transition-colors"
                                title={t('apiKeys.deletePermanently')}
                              >
                                <span className="material-symbols-outlined text-[20px]">
                                  delete
                                </span>
                              </button>
                            </>
                          )}
                        </div>
                      </td>
                    </tr>
                  );
                })
              )}
            </tbody>
          </table>
        </div>

        {/* Pagination */}
        <div className="flex items-center justify-between px-6 py-4 border-t border-border-dark bg-[#151b28]">
          <span className="text-sm text-slate-400">
            {t('common.showing')} 1 {t('common.of')} {data?.count || 0} {t('common.entries')}
          </span>
          <div className="flex items-center gap-2">
            <button
              className="px-3 py-1 text-sm text-slate-400 hover:text-white disabled:opacity-50"
              disabled
            >
              {t('common.previous')}
            </button>
            <button className="px-3 py-1 text-sm text-slate-400 hover:text-white">
              {t('common.next')}
            </button>
          </div>
        </div>
      </div>

      {/* Create Modal */}
      <Modal
        isOpen={showCreateModal}
        onClose={() => setShowCreateModal(false)}
        title={t('apiKeys.form.createTitle')}
      >
        <ApiKeyForm
          onSubmit={handleCreate}
          onCancel={() => setShowCreateModal(false)}
          isLoading={createMutation.isPending}
        />
      </Modal>

      {/* Edit Modal */}
      <Modal
        isOpen={!!editingKey}
        onClose={() => setEditingKey(null)}
        title={t('apiKeys.form.editTitle')}
      >
        {editingKey && (
          <ApiKeyForm
            initialData={editingKey}
            onSubmit={handleUpdate}
            onCancel={() => setEditingKey(null)}
            isLoading={updateMutation.isPending}
          />
        )}
      </Modal>
    </div>
  );
}
