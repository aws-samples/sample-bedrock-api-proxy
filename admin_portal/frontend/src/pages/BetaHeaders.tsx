import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  useBetaHeaders,
  useCreateBetaHeader,
  useUpdateBetaHeader,
  useDeleteBetaHeader,
} from '../hooks/useBetaHeaders';
import type { BetaHeader, BetaHeaderCreate } from '../types';

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

export default function BetaHeaders() {
  const { t } = useTranslation();
  const [searchQuery, setSearchQuery] = useState('');
  const [typeFilter, setTypeFilter] = useState('');
  const [showCreatePanel, setShowCreatePanel] = useState(false);
  const [editingHeader, setEditingHeader] = useState<BetaHeader | null>(null);
  const [deleteConfirm, setDeleteConfirm] = useState<string | null>(null);

  const { data, isLoading, error } = useBetaHeaders({
    type: typeFilter || undefined,
    search: searchQuery || undefined,
  });
  const createMutation = useCreateBetaHeader();
  const updateMutation = useUpdateBetaHeader();
  const deleteMutation = useDeleteBetaHeader();

  const filteredItems = data?.items ?? [];

  const handleCreate = async (formData: BetaHeaderCreate) => {
    try {
      await createMutation.mutateAsync(formData);
      setShowCreatePanel(false);
    } catch (err) {
      console.error('Failed to create beta header:', err);
    }
  };

  const handleUpdate = async (headerName: string, formData: Omit<BetaHeaderCreate, 'header_name'>) => {
    try {
      await updateMutation.mutateAsync({
        headerName,
        data: {
          header_type: formData.header_type,
          mapped_to: formData.mapped_to,
          description: formData.description,
        },
      });
      setEditingHeader(null);
    } catch (err) {
      console.error('Failed to update beta header:', err);
    }
  };

  const handleDelete = async (headerName: string) => {
    try {
      await deleteMutation.mutateAsync(headerName);
      setDeleteConfirm(null);
    } catch (err) {
      console.error('Failed to delete beta header:', err);
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
          <h1 className="text-2xl font-bold text-white">{t('betaHeaders.title')}</h1>
          <p className="text-slate-400 mt-1">{t('betaHeaders.subtitle')}</p>
        </div>
        <button
          onClick={() => setShowCreatePanel(true)}
          className="flex items-center gap-2 px-4 py-2.5 bg-primary hover:bg-primary/90 text-white rounded-lg font-medium transition-colors shadow-lg shadow-blue-500/30"
        >
          <span className="material-symbols-outlined text-[20px]">add</span>
          {t('betaHeaders.addHeader')}
        </button>
      </div>

      {/* Search + Type Filter */}
      <div className="flex gap-4">
        <div className="relative flex-1">
          <span className="material-symbols-outlined absolute left-3 top-1/2 -translate-y-1/2 text-slate-400">
            search
          </span>
          <input
            type="text"
            placeholder={t('betaHeaders.searchPlaceholder')}
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="w-full pl-10 pr-4 py-2.5 bg-input-bg border border-border-dark rounded-lg text-white placeholder-slate-500 focus:outline-none focus:ring-2 focus:ring-primary/50 focus:border-primary"
          />
        </div>
        <select
          value={typeFilter}
          onChange={(e) => setTypeFilter(e.target.value)}
          className="px-4 py-2.5 bg-input-bg border border-border-dark rounded-lg text-white focus:outline-none focus:ring-2 focus:ring-primary/50 focus:border-primary"
        >
          <option value="">{t('betaHeaders.allTypes')}</option>
          <option value="mapping">{t('betaHeaders.types.mapping')}</option>
          <option value="blocklist">{t('betaHeaders.types.blocklist')}</option>
        </select>
      </div>

      {/* Table */}
      <div className="bg-surface-dark border border-border-dark rounded-xl overflow-hidden">
        <table className="w-full">
          <thead>
            <tr className="border-b border-border-dark">
              <th className="text-left px-6 py-4 text-sm font-semibold text-slate-300">
                {t('betaHeaders.headerName')}
              </th>
              <th className="text-left px-6 py-4 text-sm font-semibold text-slate-300">
                {t('betaHeaders.type')}
              </th>
              <th className="text-left px-6 py-4 text-sm font-semibold text-slate-300">
                {t('betaHeaders.mappedTo')}
              </th>
              <th className="text-left px-6 py-4 text-sm font-semibold text-slate-300">
                {t('betaHeaders.description')}
              </th>
              <th className="text-right px-6 py-4 text-sm font-semibold text-slate-300">
                {t('common.actions')}
              </th>
            </tr>
          </thead>
          <tbody>
            {isLoading ? (
              <tr>
                <td colSpan={5} className="px-6 py-12 text-center text-slate-400">
                  {t('common.loading')}
                </td>
              </tr>
            ) : filteredItems.length === 0 ? (
              <tr>
                <td colSpan={5} className="px-6 py-12 text-center text-slate-400">
                  {t('betaHeaders.noResults')}
                </td>
              </tr>
            ) : (
              filteredItems.map((item) => (
                <tr
                  key={item.header_name}
                  className="border-b border-border-dark last:border-0 hover:bg-slate-800/50 group"
                >
                  <td className="px-6 py-4">
                    <code className="text-sm text-white bg-slate-800 px-2 py-1 rounded">
                      {item.header_name}
                    </code>
                  </td>
                  <td className="px-6 py-4">
                    <span
                      className={`inline-flex items-center px-2.5 py-1 rounded-full text-xs font-medium ${
                        item.header_type === 'mapping'
                          ? 'bg-blue-500/20 text-blue-400'
                          : 'bg-red-500/20 text-red-400'
                      }`}
                    >
                      {t(`betaHeaders.types.${item.header_type}`)}
                    </span>
                  </td>
                  <td className="px-6 py-4">
                    {item.header_type === 'mapping' && item.mapped_to?.length > 0 ? (
                      <div className="flex flex-wrap gap-1">
                        {item.mapped_to.map((tag) => (
                          <span
                            key={tag}
                            className="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-slate-800/50 text-slate-300"
                          >
                            {tag}
                          </span>
                        ))}
                      </div>
                    ) : (
                      <span className="text-xs text-slate-500">&mdash;</span>
                    )}
                  </td>
                  <td className="px-6 py-4">
                    <span className="text-sm text-slate-400 truncate block max-w-xs">
                      {item.description || <span className="text-slate-500">&mdash;</span>}
                    </span>
                  </td>
                  <td className="px-6 py-4 text-right">
                    <div className="flex items-center justify-end gap-2 opacity-0 group-hover:opacity-100 transition-opacity">
                      <button
                        onClick={() => setEditingHeader(item)}
                        className="p-2 text-slate-400 hover:text-white hover:bg-slate-700 rounded-lg transition-colors"
                        title={t('common.edit')}
                      >
                        <span className="material-symbols-outlined text-[18px]">edit</span>
                      </button>
                      <button
                        onClick={() => setDeleteConfirm(item.header_name)}
                        className="p-2 text-slate-400 hover:text-red-400 hover:bg-red-500/10 rounded-lg transition-colors"
                        title={t('common.delete')}
                      >
                        <span className="material-symbols-outlined text-[18px]">delete</span>
                      </button>
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
        title={t('betaHeaders.form.createTitle')}
      >
        <BetaHeaderForm
          onSubmit={handleCreate}
          onCancel={() => setShowCreatePanel(false)}
          isLoading={createMutation.isPending}
        />
      </SlideOver>

      {/* Edit SlideOver */}
      <SlideOver
        isOpen={!!editingHeader}
        onClose={() => setEditingHeader(null)}
        title={t('betaHeaders.form.editTitle')}
      >
        {editingHeader && (
          <BetaHeaderForm
            initialData={editingHeader}
            onSubmit={(data) =>
              handleUpdate(editingHeader.header_name, {
                header_type: data.header_type,
                mapped_to: data.mapped_to,
                description: data.description,
              })
            }
            onCancel={() => setEditingHeader(null)}
            isLoading={updateMutation.isPending}
            isEdit
          />
        )}
      </SlideOver>

      {/* Delete Confirmation */}
      {deleteConfirm && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
          <div className="bg-surface-dark border border-border-dark rounded-xl p-6 max-w-md w-full mx-4">
            <h3 className="text-lg font-semibold text-white mb-2">{t('common.confirm')}</h3>
            <p className="text-slate-400 mb-6">{t('betaHeaders.confirmDelete')}</p>
            <div className="flex justify-end gap-3">
              <button
                onClick={() => setDeleteConfirm(null)}
                className="px-4 py-2 border border-border-dark text-slate-300 rounded-lg hover:bg-slate-800 transition-colors"
              >
                {t('common.cancel')}
              </button>
              <button
                onClick={() => handleDelete(deleteConfirm)}
                disabled={deleteMutation.isPending}
                className="px-4 py-2 bg-red-600 hover:bg-red-700 text-white rounded-lg transition-colors disabled:opacity-50"
              >
                {deleteMutation.isPending ? t('common.loading') : t('common.delete')}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

// Form Component
interface BetaHeaderFormProps {
  initialData?: BetaHeader;
  onSubmit: (data: BetaHeaderCreate) => void;
  onCancel: () => void;
  isLoading: boolean;
  isEdit?: boolean;
}

function BetaHeaderForm({ initialData, onSubmit, onCancel, isLoading, isEdit }: BetaHeaderFormProps) {
  const { t } = useTranslation();
  const [headerName, setHeaderName] = useState(initialData?.header_name || '');
  const [headerType, setHeaderType] = useState<'mapping' | 'blocklist'>(initialData?.header_type || 'mapping');
  const [mappedToTags, setMappedToTags] = useState<string[]>(initialData?.mapped_to || []);
  const [tagInput, setTagInput] = useState('');
  const [description, setDescription] = useState(initialData?.description || '');

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    onSubmit({
      header_name: headerName,
      header_type: headerType,
      mapped_to: headerType === 'mapping' ? mappedToTags : undefined,
      description: description || undefined,
    });
  };

  const handleTagKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      const value = tagInput.trim();
      if (value && !mappedToTags.includes(value)) {
        setMappedToTags([...mappedToTags, value]);
        setTagInput('');
      }
    }
  };

  const removeTag = (tag: string) => {
    setMappedToTags(mappedToTags.filter((t) => t !== tag));
  };

  const isValid =
    headerName.trim() &&
    (headerType === 'blocklist' || (headerType === 'mapping' && mappedToTags.length > 0));

  return (
    <form onSubmit={handleSubmit} className="space-y-6">
      <div>
        <label className="block text-sm font-medium text-slate-300 mb-2">
          {t('betaHeaders.headerName')} *
        </label>
        <input
          type="text"
          value={headerName}
          onChange={(e) => setHeaderName(e.target.value)}
          placeholder={t('betaHeaders.form.headerNamePlaceholder')}
          disabled={isEdit}
          className="w-full px-4 py-2.5 bg-input-bg border border-border-dark rounded-lg text-white placeholder-slate-500 focus:outline-none focus:ring-2 focus:ring-primary/50 focus:border-primary disabled:opacity-50 disabled:cursor-not-allowed"
        />
      </div>

      <div>
        <label className="block text-sm font-medium text-slate-300 mb-2">
          {t('betaHeaders.type')} *
        </label>
        <select
          value={headerType}
          onChange={(e) => setHeaderType(e.target.value as 'mapping' | 'blocklist')}
          className="w-full px-4 py-2.5 bg-input-bg border border-border-dark rounded-lg text-white focus:outline-none focus:ring-2 focus:ring-primary/50 focus:border-primary"
        >
          <option value="mapping">{t('betaHeaders.types.mapping')}</option>
          <option value="blocklist">{t('betaHeaders.types.blocklist')}</option>
        </select>
      </div>

      {headerType === 'mapping' && (
        <div>
          <label className="block text-sm font-medium text-slate-300 mb-2">
            {t('betaHeaders.mappedTo')} *
          </label>
          {mappedToTags.length > 0 && (
            <div className="flex flex-wrap gap-2 mb-2">
              {mappedToTags.map((tag) => (
                <span
                  key={tag}
                  className="inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-xs font-medium bg-blue-500/20 text-blue-400"
                >
                  {tag}
                  <button
                    type="button"
                    onClick={() => removeTag(tag)}
                    className="hover:text-blue-300 transition-colors"
                  >
                    <span className="material-symbols-outlined text-[14px]">close</span>
                  </button>
                </span>
              ))}
            </div>
          )}
          <input
            type="text"
            value={tagInput}
            onChange={(e) => setTagInput(e.target.value)}
            onKeyDown={handleTagKeyDown}
            placeholder={t('betaHeaders.form.mappedToPlaceholder')}
            className="w-full px-4 py-2.5 bg-input-bg border border-border-dark rounded-lg text-white placeholder-slate-500 focus:outline-none focus:ring-2 focus:ring-primary/50 focus:border-primary"
          />
        </div>
      )}

      <div>
        <label className="block text-sm font-medium text-slate-300 mb-2">
          {t('betaHeaders.description')}
        </label>
        <textarea
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          placeholder={t('betaHeaders.form.descriptionPlaceholder')}
          rows={3}
          className="w-full px-4 py-2.5 bg-input-bg border border-border-dark rounded-lg text-white placeholder-slate-500 focus:outline-none focus:ring-2 focus:ring-primary/50 focus:border-primary resize-none"
        />
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
          {isLoading ? t('common.loading') : t('betaHeaders.form.save')}
        </button>
      </div>
    </form>
  );
}
