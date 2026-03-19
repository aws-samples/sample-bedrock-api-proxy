import { useTranslation } from 'react-i18next';
import { Link } from 'react-router-dom';
import { useDashboardStats } from '../hooks';
import { formatTokens } from '../utils';

export default function Dashboard() {
  const { t } = useTranslation();
  const { data: stats, isLoading, error } = useDashboardStats();

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-64">
        <span className="material-symbols-outlined animate-spin text-4xl text-primary">
          progress_activity
        </span>
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="text-center text-red-400">
          <span className="material-symbols-outlined text-4xl mb-2">error</span>
          <p>Failed to load dashboard data</p>
        </div>
      </div>
    );
  }

  const budgetPercent = stats
    ? Math.round((stats.total_budget_used / Math.max(stats.total_budget, 1)) * 100)
    : 0;

  return (
    <div className="flex flex-col gap-8">
      {/* Page Heading */}
      <div className="flex flex-col gap-2">
        <h1 className="text-3xl md:text-4xl font-bold text-white tracking-tight">
          {t('dashboard.title')}
        </h1>
        <p className="text-slate-400 text-base">{t('dashboard.subtitle')}</p>
      </div>

      {/* Stats Cards */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
        {/* Total Budget */}
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
              className={`h-full rounded-full ${
                budgetPercent > 90
                  ? 'bg-red-500'
                  : budgetPercent > 75
                  ? 'bg-orange-500'
                  : 'bg-emerald-500'
              }`}
              style={{ width: `${Math.min(budgetPercent, 100)}%` }}
            ></div>
          </div>
        </div>

        {/* Active Keys */}
        <div className="bg-surface-dark border border-border-dark rounded-xl p-5 flex flex-col gap-1 shadow-sm">
          <div className="flex items-center justify-between">
            <span className="text-slate-400 text-sm font-medium">{t('dashboard.activeKeys')}</span>
            <span className="material-symbols-outlined text-primary">key</span>
          </div>
          <div className="flex items-baseline gap-2 mt-2">
            <span className="text-2xl font-bold text-white">{stats?.active_api_keys || 0}</span>
            {stats && stats.new_keys_this_week > 0 && (
              <span className="text-sm text-emerald-500 font-medium">
                +{stats.new_keys_this_week} {t('dashboard.newKeysThisWeek')}
              </span>
            )}
          </div>
          <p className="text-xs text-slate-500 mt-2">
            {stats?.revoked_api_keys || 0} {t('common.revoked').toLowerCase()}
          </p>
        </div>

        {/* Total Models */}
        <div className="bg-surface-dark border border-border-dark rounded-xl p-5 flex flex-col gap-1 shadow-sm">
          <div className="flex items-center justify-between">
            <span className="text-slate-400 text-sm font-medium">{t('dashboard.totalModels')}</span>
            <span className="material-symbols-outlined text-purple-500">smart_toy</span>
          </div>
          <div className="flex items-baseline gap-2 mt-2">
            <span className="text-2xl font-bold text-white">{stats?.total_models || 0}</span>
            <span className="text-sm text-slate-400">
              {stats?.active_models || 0} {t('dashboard.activeModels')}
            </span>
          </div>
          <p className="text-xs text-slate-500 mt-2">
            Pricing configurations
          </p>
        </div>

        {/* System Status */}
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

      {/* Token Usage Card */}
      <div className="bg-surface-dark border border-border-dark rounded-xl p-5 shadow-sm">
        <div className="flex items-center justify-between mb-4">
          <span className="text-slate-400 text-sm font-medium">{t('dashboard.totalTokenUsage')}</span>
          <span className="material-symbols-outlined text-cyan-500">token</span>
        </div>
        <div className="grid grid-cols-2 md:grid-cols-5 gap-4">
          <div className="flex flex-col gap-1">
            <div className="flex items-center gap-2">
              <span className="material-symbols-outlined text-[16px] text-emerald-500">arrow_upward</span>
              <span className="text-xs text-slate-400">{t('apiKeys.inputTokens')}</span>
            </div>
            <span className="text-lg font-bold text-white">{formatTokens(stats?.total_input_tokens)}</span>
          </div>
          <div className="flex flex-col gap-1">
            <div className="flex items-center gap-2">
              <span className="material-symbols-outlined text-[16px] text-blue-400">arrow_downward</span>
              <span className="text-xs text-slate-400">{t('apiKeys.outputTokens')}</span>
            </div>
            <span className="text-lg font-bold text-white">{formatTokens(stats?.total_output_tokens)}</span>
          </div>
          <div className="flex flex-col gap-1">
            <div className="flex items-center gap-2">
              <span className="material-symbols-outlined text-[16px] text-purple-400">cached</span>
              <span className="text-xs text-slate-400">{t('apiKeys.cacheRead')}</span>
            </div>
            <span className="text-lg font-bold text-white">{formatTokens(stats?.total_cached_tokens)}</span>
          </div>
          <div className="flex flex-col gap-1">
            <div className="flex items-center gap-2">
              <span className="material-symbols-outlined text-[16px] text-amber-400">edit_note</span>
              <span className="text-xs text-slate-400">{t('apiKeys.cacheWrite')}</span>
            </div>
            <span className="text-lg font-bold text-white">{formatTokens(stats?.total_cache_write_tokens)}</span>
          </div>
          <div className="flex flex-col gap-1">
            <div className="flex items-center gap-2">
              <span className="material-symbols-outlined text-[16px] text-slate-400">send</span>
              <span className="text-xs text-slate-400">{t('apiKeys.requests')}</span>
            </div>
            <span className="text-lg font-bold text-white">{(stats?.total_requests || 0).toLocaleString()}</span>
          </div>
        </div>
      </div>

      {/* Quick Actions */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <Link
          to="/api-keys"
          className="bg-surface-dark border border-border-dark rounded-xl p-6 hover:border-primary/50 transition-colors group"
        >
          <div className="flex items-center gap-4">
            <div className="size-12 bg-primary/10 rounded-xl flex items-center justify-center text-primary">
              <span className="material-symbols-outlined text-2xl">vpn_key</span>
            </div>
            <div>
              <h3 className="text-lg font-bold text-white group-hover:text-primary transition-colors">
                Manage API Keys
              </h3>
              <p className="text-slate-400 text-sm">
                Create, update, and manage API access keys
              </p>
            </div>
            <span className="material-symbols-outlined text-slate-400 ml-auto group-hover:text-primary transition-colors">
              arrow_forward
            </span>
          </div>
        </Link>

        <Link
          to="/pricing"
          className="bg-surface-dark border border-border-dark rounded-xl p-6 hover:border-primary/50 transition-colors group"
        >
          <div className="flex items-center gap-4">
            <div className="size-12 bg-purple-500/10 rounded-xl flex items-center justify-center text-purple-500">
              <span className="material-symbols-outlined text-2xl">payments</span>
            </div>
            <div>
              <h3 className="text-lg font-bold text-white group-hover:text-primary transition-colors">
                Configure Pricing
              </h3>
              <p className="text-slate-400 text-sm">
                Set model pricing tiers and cache rates
              </p>
            </div>
            <span className="material-symbols-outlined text-slate-400 ml-auto group-hover:text-primary transition-colors">
              arrow_forward
            </span>
          </div>
        </Link>
      </div>
    </div>
  );
}
