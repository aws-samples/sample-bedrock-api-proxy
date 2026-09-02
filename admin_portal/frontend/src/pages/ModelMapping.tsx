import { useState, useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import {
  useModelMappings,
  useCreateModelMapping,
  useUpdateModelMapping,
  useDeleteModelMapping,
  useModelMappingSyncStatus,
  useSyncModelMappings,
} from '../hooks/useModelMapping';
import type { ModelMapping, ModelMappingCreate, ModelMappingSyncResult } from '../types';

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

export default function ModelMappingPage() {
  const { t } = useTranslation();
  const [searchQuery, setSearchQuery] = useState('');
  const [showCreatePanel, setShowCreatePanel] = useState(false);
  const [editingMapping, setEditingMapping] = useState<ModelMapping | null>(null);
  const [deleteConfirm, setDeleteConfirm] = useState<ModelMapping | null>(null);

  const [syncResult, setSyncResult] = useState<ModelMappingSyncResult | null>(null);
  const [syncError, setSyncError] = useState<string | null>(null);

  const { data, isLoading, error } = useModelMappings();
  const { data: syncStatus } = useModelMappingSyncStatus();
  const createMutation = useCreateModelMapping();
  const updateMutation = useUpdateModelMapping();
  const deleteMutation = useDeleteModelMapping();
  const syncMutation = useSyncModelMappings();

  const handleSync = async () => {
    setSyncResult(null);
    setSyncError(null);
    try {
      const result = await syncMutation.mutateAsync(undefined);
      setSyncResult(result);
    } catch (err) {
      setSyncError(err instanceof Error ? err.message : String(err));
    }
  };

  // Filter items based on search
  const filteredItems = useMemo(() => {
    if (!data?.items) return [];
    if (!searchQuery) return data.items;

    const query = searchQuery.toLowerCase();
    return data.items.filter(
      (item) =>
        item.anthropic_model_id.toLowerCase().includes(query) ||
        item.bedrock_model_id.toLowerCase().includes(query)
    );
  }, [data?.items, searchQuery]);

  const handleCreate = async (formData: ModelMappingCreate) => {
    try {
      await createMutation.mutateAsync(formData);
      setShowCreatePanel(false);
    } catch (err) {
      console.error('Failed to create mapping:', err);
    }
  };

  const handleUpdate = async (anthropicModelId: string, bedrockModelId: string) => {
    try {
      await updateMutation.mutateAsync({
        anthropicModelId,
        data: { bedrock_model_id: bedrockModelId },
      });
      setEditingMapping(null);
    } catch (err) {
      console.error('Failed to update mapping:', err);
    }
  };

  const handleDelete = async (anthropicModelId: string) => {
    try {
      await deleteMutation.mutateAsync(anthropicModelId);
      setDeleteConfirm(null);
    } catch (err) {
      console.error('Failed to delete mapping:', err);
    }
  };

  if (error) {
    return (
      <div className="p-6">
        <div className="bg-red-500/10 border border-red-500/20 rounded-lg p-4 text-red-400">
          {t('common.error')}: {(error as Error).message}
        </div>
      </div>
    );
  }

  return (
    <div className="p-6 space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-white">{t('modelMapping.title')}</h1>
          <p className="text-slate-400 mt-1">{t('modelMapping.subtitle')}</p>
          {syncStatus && (
            <p className="text-xs text-slate-500 mt-2 break-all">
              {t(`modelMapping.sync.sourceLabel.${syncStatus.source}`, {
                count: syncStatus.mapping_count,
              })}
              {syncStatus.source === 'remote' && (
                <>
                  {' · '}
                  <a
                    href={syncStatus.source_url}
                    target="_blank"
                    rel="noreferrer"
                    className="underline hover:text-slate-300"
                  >
                    {syncStatus.source_url}
                  </a>
                </>
              )}
              {syncStatus.last_error && (
                <span className="text-amber-400">
                  {' · '}
                  {t('modelMapping.sync.lastError', { error: syncStatus.last_error })}
                </span>
              )}
            </p>
          )}
        </div>
        <div className="flex items-center gap-3">
          <button
            onClick={handleSync}
            disabled={syncMutation.isPending || syncStatus?.enabled === false}
            className="flex items-center gap-2 px-4 py-2.5 bg-surface-dark border border-border-dark text-slate-200 rounded-lg font-medium transition-colors hover:bg-border-dark disabled:opacity-50"
            title={
              syncStatus?.enabled === false
                ? t('modelMapping.sync.disabledTooltip')
                : t('modelMapping.sync.tooltip')
            }
          >
            <span
              className={`material-symbols-outlined text-[20px] ${
                syncMutation.isPending ? 'animate-spin' : ''
              }`}
            >
              sync
            </span>
            {syncMutation.isPending ? t('modelMapping.sync.syncing') : t('modelMapping.sync.syncNow')}
          </button>
          <button
            onClick={() => setShowCreatePanel(true)}
            className="flex items-center gap-2 px-4 py-2.5 bg-primary hover:bg-primary/90 text-white rounded-lg font-medium transition-colors shadow-lg shadow-blue-500/30"
          >
            <span className="material-symbols-outlined text-[20px]">add</span>
            {t('modelMapping.addMapping')}
          </button>
        </div>
      </div>

      {/* Sync Result Banner */}
      {syncResult && (
        <div className="bg-emerald-900/20 border border-emerald-700/50 rounded-xl p-4 flex items-start justify-between gap-4">
          <div className="flex items-start gap-3">
            <span className="material-symbols-outlined text-emerald-400 mt-0.5">check_circle</span>
            <div className="text-sm text-slate-300">
              <p className="font-medium text-emerald-300 mb-1">{t('modelMapping.sync.completed')}</p>
              <p>
                {t('modelMapping.sync.summary', {
                  remote: syncResult.remote_models,
                  active: syncResult.mapping_count,
                  added: syncResult.added.length,
                  removed: syncResult.removed.length,
                  changed: syncResult.changed.length,
                })}
              </p>
              {syncResult.local_overrides > 0 && (
                <p className="text-slate-400 mt-1">
                  {t('modelMapping.sync.localOverrides', { count: syncResult.local_overrides })}
                </p>
              )}
            </div>
          </div>
          <button
            onClick={() => setSyncResult(null)}
            className="text-slate-400 hover:text-slate-300 flex-shrink-0"
          >
            <span className="material-symbols-outlined text-[20px]">close</span>
          </button>
        </div>
      )}
      {syncError && (
        <div className="bg-red-900/20 border border-red-700/50 rounded-xl p-4 flex items-start justify-between gap-4">
          <div className="flex items-start gap-3">
            <span className="material-symbols-outlined text-red-400 mt-0.5">error</span>
            <div className="text-sm text-slate-300">
              <p className="font-medium text-red-300 mb-1">{t('modelMapping.sync.failed')}</p>
              <p className="break-all">{syncError}</p>
            </div>
          </div>
          <button
            onClick={() => setSyncError(null)}
            className="text-slate-400 hover:text-slate-300 flex-shrink-0"
          >
            <span className="material-symbols-outlined text-[20px]">close</span>
          </button>
        </div>
      )}

      {/* Search */}
      <div className="relative">
        <span className="material-symbols-outlined absolute left-3 top-1/2 -translate-y-1/2 text-slate-400">
          search
        </span>
        <input
          type="text"
          placeholder={t('modelMapping.searchPlaceholder')}
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
          className="w-full pl-10 pr-4 py-2.5 bg-input-bg border border-border-dark rounded-lg text-white placeholder-slate-500 focus:outline-none focus:ring-2 focus:ring-primary/50 focus:border-primary"
        />
      </div>

      {/* Table */}
      <div className="bg-surface-dark border border-border-dark rounded-xl overflow-hidden">
        <table className="w-full">
          <thead>
            <tr className="border-b border-border-dark">
              <th className="text-left px-6 py-4 text-sm font-semibold text-slate-300">
                {t('modelMapping.anthropicModelId')}
              </th>
              <th className="text-left px-6 py-4 text-sm font-semibold text-slate-300">
                {t('modelMapping.bedrockModelId')}
              </th>
              <th className="text-left px-6 py-4 text-sm font-semibold text-slate-300">
                {t('modelMapping.source')}
              </th>
              <th className="text-right px-6 py-4 text-sm font-semibold text-slate-300">
                {t('common.actions')}
              </th>
            </tr>
          </thead>
          <tbody>
            {isLoading ? (
              <tr>
                <td colSpan={4} className="px-6 py-12 text-center text-slate-400">
                  {t('common.loading')}
                </td>
              </tr>
            ) : filteredItems.length === 0 ? (
              <tr>
                <td colSpan={4} className="px-6 py-12 text-center text-slate-400">
                  No mappings found
                </td>
              </tr>
            ) : (
              filteredItems.map((item) => (
                <tr
                  key={item.anthropic_model_id}
                  className="border-b border-border-dark last:border-0 hover:bg-slate-800/50 group"
                >
                  <td className="px-6 py-4">
                    <code className="text-sm text-white bg-slate-800 px-2 py-1 rounded">
                      {item.anthropic_model_id}
                    </code>
                  </td>
                  <td className="px-6 py-4">
                    <code className="text-sm text-slate-300 bg-slate-800/50 px-2 py-1 rounded">
                      {item.bedrock_model_id}
                    </code>
                  </td>
                  <td className="px-6 py-4">
                    <span
                      className={`inline-flex items-center px-2.5 py-1 rounded-full text-xs font-medium ${
                        item.source === 'default'
                          ? 'bg-slate-700 text-slate-300'
                          : item.source === 'override'
                            ? 'bg-amber-500/20 text-amber-400'
                            : 'bg-blue-500/20 text-blue-400'
                      }`}
                      title={
                        item.source === 'override' && item.default_bedrock_model_id
                          ? `${t('modelMapping.defaultValue')}: ${item.default_bedrock_model_id}`
                          : undefined
                      }
                    >
                      {t(`modelMapping.sources.${item.source}`)}
                    </span>
                  </td>
                  <td className="px-6 py-4 text-right">
                    <div className="flex items-center justify-end gap-2 opacity-0 group-hover:opacity-100 transition-opacity">
                      <button
                        onClick={() => setEditingMapping(item)}
                        className="p-2 text-slate-400 hover:text-white hover:bg-slate-700 rounded-lg transition-colors"
                        title={t('common.edit')}
                      >
                        <span className="material-symbols-outlined text-[18px]">edit</span>
                      </button>
                      {item.source === 'override' && (
                        <button
                          onClick={() => setDeleteConfirm(item)}
                          className="p-2 text-slate-400 hover:text-amber-400 hover:bg-amber-500/10 rounded-lg transition-colors"
                          title={t('modelMapping.restoreDefault')}
                        >
                          <span className="material-symbols-outlined text-[18px]">history</span>
                        </button>
                      )}
                      {item.source === 'custom' && (
                        <button
                          onClick={() => setDeleteConfirm(item)}
                          className="p-2 text-slate-400 hover:text-red-400 hover:bg-red-500/10 rounded-lg transition-colors"
                          title={t('common.delete')}
                        >
                          <span className="material-symbols-outlined text-[18px]">delete</span>
                        </button>
                      )}
                    </div>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      {/* Create SlideOver */}
      <SlideOver
        isOpen={showCreatePanel}
        onClose={() => setShowCreatePanel(false)}
        title={t('modelMapping.form.createTitle')}
      >
        <MappingForm
          onSubmit={handleCreate}
          onCancel={() => setShowCreatePanel(false)}
          isLoading={createMutation.isPending}
        />
      </SlideOver>

      {/* Edit SlideOver */}
      <SlideOver
        isOpen={!!editingMapping}
        onClose={() => setEditingMapping(null)}
        title={t('modelMapping.form.editTitle')}
      >
        {editingMapping && (
          <MappingForm
            initialData={editingMapping}
            onSubmit={(data) => handleUpdate(editingMapping.anthropic_model_id, data.bedrock_model_id)}
            onCancel={() => setEditingMapping(null)}
            isLoading={updateMutation.isPending}
            isEdit
          />
        )}
      </SlideOver>

      {/* Delete / Restore Confirmation */}
      {deleteConfirm && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
          <div className="bg-surface-dark border border-border-dark rounded-xl p-6 max-w-md w-full mx-4">
            <h3 className="text-lg font-semibold text-white mb-2">{t('common.confirm')}</h3>
            <p className="text-slate-400 mb-4">
              {deleteConfirm.source === 'override'
                ? t('modelMapping.confirmRestoreDefault')
                : t('modelMapping.confirmDelete')}
            </p>
            {deleteConfirm.source === 'override' && deleteConfirm.default_bedrock_model_id && (
              <code className="block text-sm text-slate-300 bg-slate-800 px-3 py-2 rounded mb-6 break-all">
                {deleteConfirm.default_bedrock_model_id}
              </code>
            )}
            <div className="flex justify-end gap-3">
              <button
                onClick={() => setDeleteConfirm(null)}
                className="px-4 py-2 border border-border-dark text-slate-300 rounded-lg hover:bg-slate-800 transition-colors"
              >
                {t('common.cancel')}
              </button>
              <button
                onClick={() => handleDelete(deleteConfirm.anthropic_model_id)}
                disabled={deleteMutation.isPending}
                className={`px-4 py-2 text-white rounded-lg transition-colors disabled:opacity-50 ${
                  deleteConfirm.source === 'override'
                    ? 'bg-amber-600 hover:bg-amber-700'
                    : 'bg-red-600 hover:bg-red-700'
                }`}
              >
                {deleteMutation.isPending
                  ? t('common.loading')
                  : deleteConfirm.source === 'override'
                    ? t('modelMapping.restoreDefault')
                    : t('common.delete')}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

// Form Component
interface MappingFormProps {
  initialData?: ModelMapping;
  onSubmit: (data: ModelMappingCreate) => void;
  onCancel: () => void;
  isLoading: boolean;
  isEdit?: boolean;
}

function MappingForm({ initialData, onSubmit, onCancel, isLoading, isEdit }: MappingFormProps) {
  const { t } = useTranslation();
  const [anthropicModelId, setAnthropicModelId] = useState(initialData?.anthropic_model_id || '');
  const [bedrockModelId, setBedrockModelId] = useState(initialData?.bedrock_model_id || '');

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    onSubmit({
      anthropic_model_id: anthropicModelId,
      bedrock_model_id: bedrockModelId,
    });
  };

  const isValid = anthropicModelId.trim() && bedrockModelId.trim();

  return (
    <form onSubmit={handleSubmit} className="space-y-6">
      <div>
        <label className="block text-sm font-medium text-slate-300 mb-2">
          {t('modelMapping.form.anthropicModelId')} *
        </label>
        <input
          type="text"
          value={anthropicModelId}
          onChange={(e) => setAnthropicModelId(e.target.value)}
          placeholder={t('modelMapping.form.anthropicModelIdPlaceholder')}
          disabled={isEdit}
          className="w-full px-4 py-2.5 bg-input-bg border border-border-dark rounded-lg text-white placeholder-slate-500 focus:outline-none focus:ring-2 focus:ring-primary/50 focus:border-primary disabled:opacity-50 disabled:cursor-not-allowed"
        />
      </div>

      <div>
        <label className="block text-sm font-medium text-slate-300 mb-2">
          {t('modelMapping.form.bedrockModelId')} *
        </label>
        <input
          type="text"
          value={bedrockModelId}
          onChange={(e) => setBedrockModelId(e.target.value)}
          placeholder={t('modelMapping.form.bedrockModelIdPlaceholder')}
          className="w-full px-4 py-2.5 bg-input-bg border border-border-dark rounded-lg text-white placeholder-slate-500 focus:outline-none focus:ring-2 focus:ring-primary/50 focus:border-primary"
        />
        {isEdit && (initialData?.source === 'default' || initialData?.source === 'override') && (
          <p className="mt-2 text-xs text-slate-500">
            {t('modelMapping.form.overrideHint')}{' '}
            <code className="text-slate-400">
              {initialData.default_bedrock_model_id ?? initialData.bedrock_model_id}
            </code>
          </p>
        )}
      </div>

      <div className="flex justify-end gap-3 pt-4 border-t border-border-dark">
        <button
          type="button"
          onClick={onCancel}
          className="px-4 py-2.5 border border-border-dark text-slate-300 rounded-lg hover:bg-slate-800 transition-colors"
        >
          {t('common.cancel')}
        </button>
        <button
          type="submit"
          disabled={!isValid || isLoading}
          className="px-4 py-2.5 bg-primary hover:bg-primary/90 text-white rounded-lg font-medium transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
        >
          {isLoading ? t('common.loading') : t('modelMapping.form.save')}
        </button>
      </div>
    </form>
  );
}
