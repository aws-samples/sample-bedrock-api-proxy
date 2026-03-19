import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  usePricing,
  usePricingProviders,
  useCreatePricing,
  useUpdatePricing,
  useDeletePricing,
} from '../hooks';
import type { ModelPricing, PricingCreate, PricingUpdate } from '../types';

// Slide-over Panel Component
function SlideOver({
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
    <div className="fixed inset-0 z-50 overflow-hidden">
      <div className="absolute inset-0 bg-black/50 backdrop-blur-sm" onClick={onClose}></div>
      <div className="absolute inset-y-0 right-0 max-w-md w-full bg-surface-dark shadow-2xl border-l border-border-dark flex flex-col transform transition-transform duration-300">
        <div className="px-6 py-4 border-b border-border-dark flex items-center justify-between">
          <h2 className="text-lg font-bold text-white">{title}</h2>
          <button onClick={onClose} className="text-slate-400 hover:text-slate-300">
            <span className="material-symbols-outlined">close</span>
          </button>
        </div>
        <div className="flex-1 overflow-y-auto p-6">{children}</div>
      </div>
    </div>
  );
}

// Pricing Form Component
function PricingForm({
  initialData,
  onSubmit,
  onCancel,
  isLoading,
}: {
  initialData?: ModelPricing;
  onSubmit: (data: PricingCreate | PricingUpdate) => void;
  onCancel: () => void;
  isLoading: boolean;
}) {
  const { t } = useTranslation();
  const isEdit = !!initialData;

  const [formData, setFormData] = useState({
    model_id: initialData?.model_id || '',
    provider: initialData?.provider || 'Anthropic',
    display_name: initialData?.display_name || '',
    input_price: initialData?.input_price || 0,
    output_price: initialData?.output_price || 0,
    cache_read_price: initialData?.cache_read_price || 0,
    cache_write_price: initialData?.cache_write_price || 0,
    status: initialData?.status || 'active',
  });

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    onSubmit(formData);
  };

  return (
    <form onSubmit={handleSubmit} className="flex flex-col gap-6">
      {/* Model ID */}
      {isEdit ? (
        <div className="space-y-1">
          <label className="block text-xs font-medium text-slate-400 uppercase tracking-wide">
            {t('pricing.form.modelId')}
          </label>
          <p className="text-sm font-mono text-white bg-background-dark p-2 rounded border border-border-dark break-all">
            {formData.model_id}
          </p>
        </div>
      ) : (
        <div>
          <label className="block text-sm font-medium text-slate-300 mb-1">
            {t('pricing.form.modelId')}
          </label>
          <input
            type="text"
            value={formData.model_id}
            onChange={(e) => setFormData({ ...formData, model_id: e.target.value })}
            className="w-full px-3 py-2 bg-input-bg border border-border-dark rounded-lg text-white focus:border-primary focus:ring-1 focus:ring-primary font-mono text-sm"
            placeholder="anthropic.claude-3-5-sonnet-20241022-v2:0"
            required
          />
        </div>
      )}

      {/* Provider */}
      <div>
        <label className="block text-sm font-medium text-slate-300 mb-1">
          {t('pricing.form.provider')}
        </label>
        <select
          value={formData.provider}
          onChange={(e) => setFormData({ ...formData, provider: e.target.value })}
          className="w-full px-3 py-2 bg-input-bg border border-border-dark rounded-lg text-white focus:border-primary focus:ring-1 focus:ring-primary"
        >
          <option value="Anthropic">Anthropic</option>
          <option value="OpenAI">OpenAI</option>
          <option value="Qwen">Qwen</option>
          <option value="DeepSeek">DeepSeek</option>
          <option value="MiniMax">MiniMax</option>
          <option value="Kimi">Kimi</option>
          <option value="NVIDIA">NVIDIA</option>
          <option value="Cohere">Cohere</option>
          <option value="AI21 Labs">AI21 Labs</option>
          <option value="Meta">Meta</option>
          <option value="Amazon">Amazon</option>
          <option value="Mistral">Mistral</option>
          <option value="Custom Model Import">Custom Model Import</option>
        </select>
      </div>

      {/* Display Name */}
      <div>
        <label className="block text-sm font-medium text-slate-300 mb-1">
          {t('pricing.form.displayName')}
        </label>
        <input
          type="text"
          value={formData.display_name}
          onChange={(e) => setFormData({ ...formData, display_name: e.target.value })}
          className="w-full px-3 py-2 bg-input-bg border border-border-dark rounded-lg text-white focus:border-primary focus:ring-1 focus:ring-primary"
          placeholder="Claude 3.5 Sonnet"
        />
      </div>

      {/* Prices */}
      <div className="grid grid-cols-2 gap-4">
        <div className="space-y-2">
          <label className="block text-sm font-medium text-slate-300">
            {t('pricing.form.inputPrice')}
          </label>
          <div className="relative rounded-lg shadow-sm">
            <div className="pointer-events-none absolute inset-y-0 left-0 flex items-center pl-3">
              <span className="text-slate-500 sm:text-sm">$</span>
            </div>
            <input
              type="number"
              step="0.0001"
              value={formData.input_price}
              onChange={(e) =>
                setFormData({ ...formData, input_price: parseFloat(e.target.value) || 0 })
              }
              className="block w-full rounded-lg border border-border-dark bg-input-bg text-white pl-7 pr-3 focus:border-primary focus:ring-primary sm:text-sm h-10"
              placeholder="0.00"
            />
          </div>
          <p className="text-xs text-slate-500">{t('pricing.form.perMillionTokens')}</p>
        </div>

        <div className="space-y-2">
          <label className="block text-sm font-medium text-slate-300">
            {t('pricing.form.outputPrice')}
          </label>
          <div className="relative rounded-lg shadow-sm">
            <div className="pointer-events-none absolute inset-y-0 left-0 flex items-center pl-3">
              <span className="text-slate-500 sm:text-sm">$</span>
            </div>
            <input
              type="number"
              step="0.0001"
              value={formData.output_price}
              onChange={(e) =>
                setFormData({ ...formData, output_price: parseFloat(e.target.value) || 0 })
              }
              className="block w-full rounded-lg border border-border-dark bg-input-bg text-white pl-7 pr-3 focus:border-primary focus:ring-primary sm:text-sm h-10"
              placeholder="0.00"
            />
          </div>
          <p className="text-xs text-slate-500">{t('pricing.form.perMillionTokens')}</p>
        </div>
      </div>

      {/* Cache Pricing */}
      <div className="border-t border-border-dark pt-4">
        <h3 className="text-sm font-medium text-white mb-4 flex items-center gap-2">
          <span className="material-symbols-outlined text-sm">cached</span> Cache Pricing
        </h3>
        <div className="grid grid-cols-2 gap-4">
          <div className="space-y-2">
            <label className="block text-sm font-medium text-slate-300">
              {t('pricing.form.cacheReadPrice')}
            </label>
            <div className="relative rounded-lg shadow-sm">
              <div className="pointer-events-none absolute inset-y-0 left-0 flex items-center pl-3">
                <span className="text-slate-500 sm:text-sm">$</span>
              </div>
              <input
                type="number"
                step="0.0001"
                value={formData.cache_read_price}
                onChange={(e) =>
                  setFormData({ ...formData, cache_read_price: parseFloat(e.target.value) || 0 })
                }
                className="block w-full rounded-lg border border-border-dark bg-input-bg text-white pl-7 pr-3 focus:border-primary focus:ring-primary sm:text-sm h-10"
                placeholder="0.00"
              />
            </div>
          </div>

          <div className="space-y-2">
            <label className="block text-sm font-medium text-slate-300">
              {t('pricing.form.cacheWritePrice')}
            </label>
            <div className="relative rounded-lg shadow-sm">
              <div className="pointer-events-none absolute inset-y-0 left-0 flex items-center pl-3">
                <span className="text-slate-500 sm:text-sm">$</span>
              </div>
              <input
                type="number"
                step="0.0001"
                value={formData.cache_write_price}
                onChange={(e) =>
                  setFormData({ ...formData, cache_write_price: parseFloat(e.target.value) || 0 })
                }
                className="block w-full rounded-lg border border-border-dark bg-input-bg text-white pl-7 pr-3 focus:border-primary focus:ring-primary sm:text-sm h-10"
                placeholder="0.00"
              />
            </div>
          </div>
        </div>
      </div>

      {/* Status */}
      <div className="border-t border-border-dark pt-4">
        <label className="block text-sm font-medium text-slate-300 mb-2">
          {t('pricing.form.status')}
        </label>
        <select
          value={formData.status}
          onChange={(e) =>
            setFormData({
              ...formData,
              status: e.target.value as 'active' | 'deprecated' | 'disabled',
            })
          }
          className="w-full px-3 py-2 bg-input-bg border border-border-dark rounded-lg text-white focus:border-primary focus:ring-1 focus:ring-primary"
        >
          <option value="active">{t('common.active')}</option>
          <option value="deprecated">{t('common.deprecated')}</option>
          <option value="disabled">{t('common.disabled')}</option>
        </select>
        <p className="text-xs text-slate-500 mt-2">
          Disabled models will not be available for selection.
        </p>
      </div>

      {/* Actions */}
      <div className="flex gap-3 mt-4 pt-4 border-t border-border-dark">
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
          className="flex-1 px-4 py-2 bg-primary hover:bg-blue-600 text-white rounded-lg font-medium transition-colors shadow-lg shadow-blue-500/30 disabled:opacity-50"
        >
          {isLoading ? t('common.loading') : t('common.save')}
        </button>
      </div>
    </form>
  );
}

export default function Pricing() {
  const { t } = useTranslation();
  const [search, setSearch] = useState('');
  const [providerFilter, setProviderFilter] = useState<string>('');
  const [showCreatePanel, setShowCreatePanel] = useState(false);
  const [editingPricing, setEditingPricing] = useState<ModelPricing | null>(null);

  const { data, isLoading, error } = usePricing({
    provider: providerFilter || undefined,
    search: search || undefined,
  });

  const { data: providersData } = usePricingProviders();
  const createMutation = useCreatePricing();
  const updateMutation = useUpdatePricing();
  const deleteMutation = useDeletePricing();

  const handleCreate = async (data: PricingCreate | PricingUpdate) => {
    await createMutation.mutateAsync(data as PricingCreate);
    setShowCreatePanel(false);
  };

  const handleUpdate = async (data: PricingCreate | PricingUpdate) => {
    if (editingPricing) {
      await updateMutation.mutateAsync({ modelId: editingPricing.model_id, data });
      setEditingPricing(null);
    }
  };

  const handleDelete = async (modelId: string) => {
    if (confirm(t('pricing.confirmDelete'))) {
      await deleteMutation.mutateAsync(modelId);
    }
  };

  const getStatusIcon = (status: string) => {
    switch (status) {
      case 'active':
        return { icon: 'smart_toy', color: 'text-indigo-400', bgColor: 'bg-indigo-900/30' };
      case 'deprecated':
        return { icon: 'archive', color: 'text-amber-400', bgColor: 'bg-amber-900/30' };
      default:
        return { icon: 'block', color: 'text-gray-400', bgColor: 'bg-gray-700/30' };
    }
  };

  if (error) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="text-center text-red-400">
          <span className="material-symbols-outlined text-4xl mb-2">error</span>
          <p>Failed to load pricing data</p>
        </div>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-8">
      {/* Page Heading */}
      <div className="flex flex-col md:flex-row md:items-end justify-between gap-4">
        <div className="flex-1">
          <h1 className="text-3xl md:text-4xl font-bold tracking-tight text-white mb-2">
            {t('pricing.title')}
          </h1>
          <p className="text-slate-400 max-w-2xl text-base">{t('pricing.subtitle')}</p>
        </div>
        <div className="flex items-center gap-3">
          <button className="h-10 px-4 rounded-lg bg-surface-dark border border-border-dark text-slate-200 font-medium text-sm flex items-center gap-2 hover:bg-border-dark transition-colors shadow-sm">
            <span className="material-symbols-outlined text-[20px]">file_upload</span>
            {t('pricing.importCsv')}
          </button>
          <button
            onClick={() => setShowCreatePanel(true)}
            className="h-10 px-4 rounded-lg bg-primary hover:bg-blue-600 text-white font-medium text-sm flex items-center gap-2 transition-all shadow-lg shadow-blue-500/20"
          >
            <span className="material-symbols-outlined text-[20px]">add</span>
            {t('pricing.addNewModel')}
          </button>
        </div>
      </div>

      {/* Search & Filter */}
      <div className="bg-surface-dark p-4 rounded-xl border border-border-dark shadow-sm flex flex-col sm:flex-row gap-4 items-center justify-between">
        <div className="relative w-full sm:max-w-md">
          <div className="absolute inset-y-0 left-0 pl-3 flex items-center pointer-events-none">
            <span className="material-symbols-outlined text-slate-400">search</span>
          </div>
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="block w-full pl-10 pr-3 py-2.5 border border-border-dark rounded-lg leading-5 bg-background-dark text-white placeholder-slate-400 focus:outline-none focus:ring-2 focus:ring-primary/50 focus:border-primary sm:text-sm transition-shadow"
            placeholder={t('pricing.searchPlaceholder')}
          />
        </div>
        <div className="flex items-center gap-2 w-full sm:w-auto">
          <div className="relative w-full sm:w-auto">
            <select
              value={providerFilter}
              onChange={(e) => setProviderFilter(e.target.value)}
              className="appearance-none block w-full pl-3 pr-10 py-2.5 border border-border-dark rounded-lg leading-5 bg-background-dark text-slate-300 focus:outline-none focus:ring-2 focus:ring-primary/50 focus:border-primary sm:text-sm cursor-pointer"
            >
              <option value="">{t('pricing.allProviders')}</option>
              {providersData?.providers.map((provider) => (
                <option key={provider} value={provider}>
                  {provider}
                </option>
              ))}
            </select>
            <div className="pointer-events-none absolute inset-y-0 right-0 flex items-center px-2 text-slate-500">
              <span className="material-symbols-outlined text-[20px]">arrow_drop_down</span>
            </div>
          </div>
        </div>
      </div>

      {/* Data Table */}
      <div className="bg-surface-dark rounded-xl border border-border-dark shadow-sm overflow-hidden">
        <div className="overflow-x-auto">
          <table className="min-w-full divide-y divide-border-dark">
            <thead className="bg-[#151b26]">
              <tr>
                <th className="px-6 py-4 text-left text-xs font-bold text-slate-400 uppercase tracking-wider">
                  {t('pricing.modelId')}
                </th>
                <th className="px-6 py-4 text-left text-xs font-bold text-slate-400 uppercase tracking-wider">
                  {t('pricing.provider')}
                </th>
                <th className="px-6 py-4 text-right text-xs font-bold text-slate-400 uppercase tracking-wider">
                  {t('pricing.inputPrice')}
                  <span className="normal-case font-normal text-slate-500 text-[10px] block">
                    ({t('pricing.perMillionTokens')})
                  </span>
                </th>
                <th className="px-6 py-4 text-right text-xs font-bold text-slate-400 uppercase tracking-wider">
                  {t('pricing.outputPrice')}
                  <span className="normal-case font-normal text-slate-500 text-[10px] block">
                    ({t('pricing.perMillionTokens')})
                  </span>
                </th>
                <th className="px-6 py-4 text-right text-xs font-bold text-slate-400 uppercase tracking-wider hidden xl:table-cell">
                  {t('pricing.cacheRead')}
                  <span className="normal-case font-normal text-slate-500 text-[10px] block">
                    ({t('pricing.perMillionTokens')})
                  </span>
                </th>
                <th className="px-6 py-4 text-right text-xs font-bold text-slate-400 uppercase tracking-wider hidden xl:table-cell">
                  {t('pricing.cacheWrite')}
                  <span className="normal-case font-normal text-slate-500 text-[10px] block">
                    ({t('pricing.perMillionTokens')})
                  </span>
                </th>
                <th className="px-6 py-4 text-center text-xs font-bold text-slate-400 uppercase tracking-wider w-20">
                  {t('common.actions')}
                </th>
              </tr>
            </thead>
            <tbody className="bg-surface-dark divide-y divide-border-dark">
              {isLoading ? (
                <tr>
                  <td colSpan={7} className="px-6 py-12 text-center">
                    <span className="material-symbols-outlined animate-spin text-4xl text-primary">
                      progress_activity
                    </span>
                  </td>
                </tr>
              ) : data?.items.length === 0 ? (
                <tr>
                  <td colSpan={7} className="px-6 py-12 text-center text-slate-400">
                    No pricing data found
                  </td>
                </tr>
              ) : (
                data?.items.map((pricing) => {
                  const statusInfo = getStatusIcon(pricing.status);
                  const isDeprecated = pricing.status === 'deprecated';

                  return (
                    <tr
                      key={pricing.model_id}
                      className={`group hover:bg-slate-800/50 transition-colors ${
                        isDeprecated ? 'opacity-60' : ''
                      }`}
                    >
                      <td className="px-6 py-4 whitespace-nowrap">
                        <div className="flex items-center">
                          <div
                            className={`flex-shrink-0 h-8 w-8 rounded flex items-center justify-center ${statusInfo.bgColor} ${statusInfo.color} border border-current/20`}
                          >
                            <span className="material-symbols-outlined text-[18px]">
                              {statusInfo.icon}
                            </span>
                          </div>
                          <div className="ml-4">
                            <div className="text-sm font-medium text-white">
                              {pricing.display_name || pricing.model_id}
                            </div>
                            <div className="flex items-center gap-1.5 mt-0.5">
                              <span
                                className={`h-1.5 w-1.5 rounded-full ${
                                  pricing.status === 'active'
                                    ? 'bg-emerald-500'
                                    : pricing.status === 'deprecated'
                                    ? 'bg-amber-500'
                                    : 'bg-gray-500'
                                }`}
                              ></span>
                              <span className="text-xs text-slate-400 capitalize">
                                {pricing.status}
                              </span>
                            </div>
                          </div>
                        </div>
                      </td>
                      <td className="px-6 py-4 whitespace-nowrap">
                        <span className="px-2.5 py-0.5 rounded-full text-xs font-medium bg-slate-800 text-slate-300 border border-slate-700">
                          {pricing.provider}
                        </span>
                      </td>
                      <td className="px-6 py-4 whitespace-nowrap text-right text-sm font-mono text-slate-300">
                        ${pricing.input_price.toFixed(2)}
                      </td>
                      <td className="px-6 py-4 whitespace-nowrap text-right text-sm font-mono text-slate-300">
                        ${pricing.output_price.toFixed(2)}
                      </td>
                      <td className="px-6 py-4 whitespace-nowrap text-right text-sm font-mono text-slate-400 hidden xl:table-cell">
                        {pricing.cache_read_price ? `$${pricing.cache_read_price.toFixed(2)}` : '—'}
                      </td>
                      <td className="px-6 py-4 whitespace-nowrap text-right text-sm font-mono text-slate-400 hidden xl:table-cell">
                        {pricing.cache_write_price
                          ? `$${pricing.cache_write_price.toFixed(2)}`
                          : '—'}
                      </td>
                      <td className="px-6 py-4 whitespace-nowrap text-center text-sm font-medium">
                        <div className="flex items-center justify-center gap-2 opacity-0 group-hover:opacity-100 transition-opacity">
                          <button
                            onClick={() => setEditingPricing(pricing)}
                            className="text-slate-400 hover:text-primary transition-colors p-1"
                            title={t('common.edit')}
                          >
                            <span className="material-symbols-outlined text-[20px]">edit</span>
                          </button>
                          <button
                            onClick={() => handleDelete(pricing.model_id)}
                            className="text-slate-400 hover:text-red-500 transition-colors p-1"
                            title={t('common.delete')}
                          >
                            <span className="material-symbols-outlined text-[20px]">delete</span>
                          </button>
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
        <div className="bg-[#151b26] px-4 py-3 flex items-center justify-between border-t border-border-dark sm:px-6">
          <div className="hidden sm:flex-1 sm:flex sm:items-center sm:justify-between">
            <div>
              <p className="text-sm text-slate-400">
                {t('common.showing')} <span className="font-medium">1</span> {t('common.of')}{' '}
                <span className="font-medium">{data?.count || 0}</span> {t('common.entries')}
              </p>
            </div>
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
      </div>

      {/* Create Panel */}
      <SlideOver
        isOpen={showCreatePanel}
        onClose={() => setShowCreatePanel(false)}
        title={t('pricing.form.createTitle')}
      >
        <PricingForm
          onSubmit={handleCreate}
          onCancel={() => setShowCreatePanel(false)}
          isLoading={createMutation.isPending}
        />
      </SlideOver>

      {/* Edit Panel */}
      <SlideOver
        isOpen={!!editingPricing}
        onClose={() => setEditingPricing(null)}
        title={t('pricing.form.editTitle')}
      >
        {editingPricing && (
          <PricingForm
            initialData={editingPricing}
            onSubmit={handleUpdate}
            onCancel={() => setEditingPricing(null)}
            isLoading={updateMutation.isPending}
          />
        )}
      </SlideOver>
    </div>
  );
}
