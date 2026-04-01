import { useState } from 'react';
import {
  useProviders,
  useCreateProvider,
  useUpdateProvider,
  useDeleteProvider,
  useTestProvider,
} from '../hooks';
import type { Provider, ProviderCreate, ProviderUpdate, ProviderTestResult } from '../types';

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

// Provider Form Component
function ProviderForm({
  initialData,
  onSubmit,
  onCancel,
  isLoading,
}: {
  initialData?: Provider;
  onSubmit: (data: ProviderCreate | ProviderUpdate) => void;
  onCancel: () => void;
  isLoading: boolean;
}) {
  const isEdit = !!initialData;

  const [formData, setFormData] = useState({
    name: initialData?.name || '',
    aws_region: initialData?.aws_region || 'us-east-1',
    auth_type: (initialData?.auth_type || 'bearer_token') as 'bearer_token' | 'ak_sk',
    endpoint_url: initialData?.endpoint_url || '',
    bearer_token: '',
    access_key_id: '',
    secret_access_key: '',
    session_token: '',
  });

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();

    const credentials: Record<string, string> = {};
    if (formData.auth_type === 'bearer_token') {
      if (formData.bearer_token) {
        credentials.bearer_token = formData.bearer_token;
      }
    } else {
      if (formData.access_key_id) {
        credentials.access_key_id = formData.access_key_id;
      }
      if (formData.secret_access_key) {
        credentials.secret_access_key = formData.secret_access_key;
      }
      if (formData.session_token) {
        credentials.session_token = formData.session_token;
      }
    }

    if (isEdit) {
      const update: ProviderUpdate = {
        name: formData.name,
        aws_region: formData.aws_region,
        auth_type: formData.auth_type,
        endpoint_url: formData.endpoint_url || undefined,
      };
      if (Object.keys(credentials).length > 0) {
        update.credentials = credentials;
      }
      onSubmit(update);
    } else {
      const create: ProviderCreate = {
        name: formData.name,
        aws_region: formData.aws_region,
        auth_type: formData.auth_type,
        credentials,
        endpoint_url: formData.endpoint_url || undefined,
      };
      onSubmit(create);
    }
  };

  return (
    <form onSubmit={handleSubmit} className="flex flex-col gap-4">
      <div>
        <label className="block text-sm font-medium text-slate-300 mb-1">Name</label>
        <input
          type="text"
          value={formData.name}
          onChange={(e) => setFormData({ ...formData, name: e.target.value })}
          className="w-full px-3 py-2 bg-input-bg border border-border-dark rounded-lg text-white focus:border-primary focus:ring-1 focus:ring-primary"
          placeholder="e.g. Production US East"
          required
        />
      </div>

      <div>
        <label className="block text-sm font-medium text-slate-300 mb-1">AWS Region</label>
        <input
          type="text"
          value={formData.aws_region}
          onChange={(e) => setFormData({ ...formData, aws_region: e.target.value })}
          className="w-full px-3 py-2 bg-input-bg border border-border-dark rounded-lg text-white focus:border-primary focus:ring-1 focus:ring-primary"
          placeholder="us-east-1"
          required
        />
      </div>

      <div>
        <label className="block text-sm font-medium text-slate-300 mb-1">Auth Type</label>
        <select
          value={formData.auth_type}
          onChange={(e) =>
            setFormData({ ...formData, auth_type: e.target.value as 'bearer_token' | 'ak_sk' })
          }
          className="w-full px-3 py-2 bg-input-bg border border-border-dark rounded-lg text-white focus:border-primary focus:ring-1 focus:ring-primary"
        >
          <option value="bearer_token">Bearer Token</option>
          <option value="ak_sk">Access Key / Secret Key</option>
        </select>
      </div>

      {/* Dynamic credential fields */}
      {formData.auth_type === 'bearer_token' ? (
        <div>
          <label className="block text-sm font-medium text-slate-300 mb-1">
            Bearer Token{isEdit ? ' (leave blank to keep current)' : ''}
          </label>
          <input
            type="password"
            value={formData.bearer_token}
            onChange={(e) => setFormData({ ...formData, bearer_token: e.target.value })}
            className="w-full px-3 py-2 bg-input-bg border border-border-dark rounded-lg text-white focus:border-primary focus:ring-1 focus:ring-primary"
            placeholder={isEdit ? '********' : 'Enter bearer token'}
            required={!isEdit}
          />
        </div>
      ) : (
        <>
          <div>
            <label className="block text-sm font-medium text-slate-300 mb-1">
              Access Key ID{isEdit ? ' (leave blank to keep current)' : ''}
            </label>
            <input
              type="text"
              value={formData.access_key_id}
              onChange={(e) => setFormData({ ...formData, access_key_id: e.target.value })}
              className="w-full px-3 py-2 bg-input-bg border border-border-dark rounded-lg text-white focus:border-primary focus:ring-1 focus:ring-primary"
              placeholder={isEdit ? '********' : 'AKIA...'}
              required={!isEdit}
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-slate-300 mb-1">
              Secret Access Key{isEdit ? ' (leave blank to keep current)' : ''}
            </label>
            <input
              type="password"
              value={formData.secret_access_key}
              onChange={(e) => setFormData({ ...formData, secret_access_key: e.target.value })}
              className="w-full px-3 py-2 bg-input-bg border border-border-dark rounded-lg text-white focus:border-primary focus:ring-1 focus:ring-primary"
              placeholder={isEdit ? '********' : 'Enter secret access key'}
              required={!isEdit}
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-slate-300 mb-1">
              Session Token (optional)
            </label>
            <input
              type="password"
              value={formData.session_token}
              onChange={(e) => setFormData({ ...formData, session_token: e.target.value })}
              className="w-full px-3 py-2 bg-input-bg border border-border-dark rounded-lg text-white focus:border-primary focus:ring-1 focus:ring-primary"
              placeholder={isEdit ? '********' : 'Enter session token (optional)'}
            />
          </div>
        </>
      )}

      <div>
        <label className="block text-sm font-medium text-slate-300 mb-1">
          Endpoint URL (optional)
        </label>
        <input
          type="text"
          value={formData.endpoint_url}
          onChange={(e) => setFormData({ ...formData, endpoint_url: e.target.value })}
          className="w-full px-3 py-2 bg-input-bg border border-border-dark rounded-lg text-white focus:border-primary focus:ring-1 focus:ring-primary"
          placeholder="https://bedrock-runtime.us-east-1.amazonaws.com"
        />
        <p className="mt-1 text-xs text-slate-500">
          Custom Bedrock endpoint URL. Leave blank to use the default endpoint for the region.
        </p>
      </div>

      <div className="flex gap-3 mt-4">
        <button
          type="button"
          onClick={onCancel}
          className="flex-1 px-4 py-2 border border-border-dark rounded-lg text-slate-300 hover:bg-surface-dark transition-colors"
        >
          Cancel
        </button>
        <button
          type="submit"
          disabled={isLoading}
          className="flex-1 px-4 py-2 bg-primary hover:bg-blue-600 text-white rounded-lg font-medium transition-colors disabled:opacity-50"
        >
          {isLoading ? 'Saving...' : 'Save'}
        </button>
      </div>
    </form>
  );
}

export default function Providers() {
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [editingProvider, setEditingProvider] = useState<Provider | null>(null);
  const [testResults, setTestResults] = useState<Record<string, ProviderTestResult | 'loading'>>({});

  const { data, isLoading, error } = useProviders();
  const createMutation = useCreateProvider();
  const updateMutation = useUpdateProvider();
  const deleteMutation = useDeleteProvider();
  const testMutation = useTestProvider();

  const handleCreate = async (formData: ProviderCreate | ProviderUpdate) => {
    await createMutation.mutateAsync(formData as ProviderCreate);
    setShowCreateModal(false);
  };

  const handleUpdate = async (formData: ProviderCreate | ProviderUpdate) => {
    if (editingProvider) {
      await updateMutation.mutateAsync({
        providerId: editingProvider.provider_id,
        data: formData as ProviderUpdate,
      });
      setEditingProvider(null);
    }
  };

  const handleDelete = async (providerId: string) => {
    if (confirm('Are you sure you want to delete this provider? This action cannot be undone.')) {
      await deleteMutation.mutateAsync(providerId);
    }
  };

  const handleToggleActive = async (provider: Provider) => {
    await updateMutation.mutateAsync({
      providerId: provider.provider_id,
      data: { is_active: !provider.is_active },
    });
  };

  const handleTest = async (providerId: string) => {
    setTestResults((prev) => ({ ...prev, [providerId]: 'loading' }));
    try {
      const result = await testMutation.mutateAsync(providerId);
      setTestResults((prev) => ({ ...prev, [providerId]: result }));
    } catch (err) {
      setTestResults((prev) => ({
        ...prev,
        [providerId]: { status: 'error', message: err instanceof Error ? err.message : 'Test failed' },
      }));
    }
    // Clear result after 5 seconds
    setTimeout(() => {
      setTestResults((prev) => {
        const next = { ...prev };
        delete next[providerId];
        return next;
      });
    }, 5000);
  };

  if (error) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="text-center text-red-400">
          <span className="material-symbols-outlined text-4xl mb-2">error</span>
          <p>Failed to load providers</p>
        </div>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-8">
      {/* Page Heading */}
      <div className="flex flex-col md:flex-row md:items-end justify-between gap-4">
        <div className="flex flex-col gap-2">
          <h1 className="text-3xl md:text-4xl font-bold text-white tracking-tight">Providers</h1>
          <p className="text-slate-400 text-base">
            Manage Bedrock account providers for multi-account routing
          </p>
        </div>
        <button
          onClick={() => setShowCreateModal(true)}
          className="flex items-center justify-center gap-2 h-10 px-4 rounded-lg bg-primary text-white text-sm font-bold shadow-lg shadow-primary/25 hover:bg-primary/90 transition-all"
        >
          <span className="material-symbols-outlined text-[20px]">add</span>
          Add Provider
        </button>
      </div>

      {/* Stats Cards */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <div className="bg-surface-dark border border-border-dark rounded-xl p-5 flex flex-col gap-1 shadow-sm">
          <div className="flex items-center justify-between">
            <span className="text-slate-400 text-sm font-medium">Total Providers</span>
            <span className="material-symbols-outlined text-primary">cloud</span>
          </div>
          <span className="text-2xl font-bold text-white mt-2">{data?.count || 0}</span>
        </div>
        <div className="bg-surface-dark border border-border-dark rounded-xl p-5 flex flex-col gap-1 shadow-sm">
          <div className="flex items-center justify-between">
            <span className="text-slate-400 text-sm font-medium">Active</span>
            <span className="material-symbols-outlined text-emerald-500">check_circle</span>
          </div>
          <span className="text-2xl font-bold text-white mt-2">
            {data?.items.filter((p) => p.is_active).length || 0}
          </span>
        </div>
        <div className="bg-surface-dark border border-border-dark rounded-xl p-5 flex flex-col gap-1 shadow-sm">
          <div className="flex items-center justify-between">
            <span className="text-slate-400 text-sm font-medium">Inactive</span>
            <span className="material-symbols-outlined text-slate-500">cancel</span>
          </div>
          <span className="text-2xl font-bold text-white mt-2">
            {data?.items.filter((p) => !p.is_active).length || 0}
          </span>
        </div>
      </div>

      {/* Data Table */}
      <div className="overflow-hidden rounded-xl border border-border-dark bg-surface-dark shadow-sm">
        <div className="overflow-x-auto">
          <table className="w-full text-left border-collapse">
            <thead className="bg-[#151b28] border-b border-border-dark">
              <tr>
                <th className="px-6 py-4 text-xs font-semibold text-slate-400 uppercase tracking-wider">
                  Name
                </th>
                <th className="px-6 py-4 text-xs font-semibold text-slate-400 uppercase tracking-wider">
                  Region
                </th>
                <th className="px-6 py-4 text-xs font-semibold text-slate-400 uppercase tracking-wider">
                  Auth Type
                </th>
                <th className="px-6 py-4 text-xs font-semibold text-slate-400 uppercase tracking-wider">
                  Credentials
                </th>
                <th className="px-6 py-4 text-xs font-semibold text-slate-400 uppercase tracking-wider">
                  Status
                </th>
                <th className="px-6 py-4 text-xs font-semibold text-slate-400 uppercase tracking-wider">
                  API Keys
                </th>
                <th className="px-6 py-4 text-xs font-semibold text-slate-400 uppercase tracking-wider text-right">
                  Actions
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border-dark">
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
                    No providers configured. Add a provider to get started.
                  </td>
                </tr>
              ) : (
                data?.items.map((provider) => {
                  const testResultRaw = testResults[provider.provider_id];
                  const isTestLoading = testResultRaw === 'loading';
                  const testResult = isTestLoading ? null : testResultRaw;
                  return (
                    <tr
                      key={provider.provider_id}
                      className={`group hover:bg-[#1e2536] transition-colors ${
                        !provider.is_active ? 'opacity-60' : ''
                      }`}
                    >
                      <td className="px-6 py-4 whitespace-nowrap">
                        <div className="flex flex-col">
                          <span className="text-sm font-bold text-white">{provider.name}</span>
                          <span className="text-xs font-mono text-slate-500 mt-0.5">
                            {provider.provider_id.slice(0, 8)}...
                          </span>
                        </div>
                      </td>
                      <td className="px-6 py-4 whitespace-nowrap">
                        <span className="text-sm text-white font-mono">{provider.aws_region}</span>
                      </td>
                      <td className="px-6 py-4 whitespace-nowrap">
                        <span
                          className={`px-2 py-0.5 rounded text-xs font-medium ${
                            provider.auth_type === 'bearer_token'
                              ? 'bg-purple-900/30 text-purple-400 border border-purple-800'
                              : 'bg-cyan-900/30 text-cyan-400 border border-cyan-800'
                          }`}
                        >
                          {provider.auth_type === 'bearer_token' ? 'Bearer Token' : 'AK/SK'}
                        </span>
                      </td>
                      <td className="px-6 py-4 whitespace-nowrap">
                        <span className="text-xs font-mono text-slate-500">
                          {provider.masked_credentials}
                        </span>
                      </td>
                      <td className="px-6 py-4 whitespace-nowrap">
                        <button
                          onClick={() => handleToggleActive(provider)}
                          className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium border cursor-pointer transition-colors ${
                            provider.is_active
                              ? 'bg-emerald-500/10 text-emerald-500 border-emerald-500/20 hover:bg-emerald-500/20'
                              : 'bg-slate-100 dark:bg-border-dark text-slate-500 border-slate-200 dark:border-slate-700 hover:bg-slate-600/20'
                          }`}
                          title={provider.is_active ? 'Click to deactivate' : 'Click to activate'}
                        >
                          <span
                            className={`size-1.5 rounded-full ${
                              provider.is_active ? 'bg-emerald-500' : 'bg-slate-500'
                            }`}
                          ></span>
                          {provider.is_active ? 'Active' : 'Inactive'}
                        </button>
                      </td>
                      <td className="px-6 py-4 whitespace-nowrap">
                        <span className="text-sm text-white">{provider.api_key_count ?? 0}</span>
                      </td>
                      <td className="px-6 py-4 whitespace-nowrap text-right">
                        <div className="flex items-center justify-end gap-2 opacity-0 group-hover:opacity-100 transition-opacity">
                          {/* Test Connection */}
                          <button
                            onClick={() => handleTest(provider.provider_id)}
                            disabled={isTestLoading}
                            className={`p-2 rounded-lg transition-colors ${
                              isTestLoading
                                ? 'text-slate-500'
                                : testResult?.status === 'ok'
                                ? 'text-emerald-400 bg-emerald-500/10'
                                : testResult?.status === 'error'
                                ? 'text-red-400 bg-red-500/10'
                                : 'text-slate-400 hover:text-white hover:bg-border-dark'
                            }`}
                            title={
                              isTestLoading
                                ? 'Testing...'
                                : testResult
                                ? testResult.message
                                : 'Test connection'
                            }
                          >
                            <span className="material-symbols-outlined text-[20px]">
                              {isTestLoading
                                ? 'progress_activity'
                                : testResult?.status === 'ok'
                                ? 'check_circle'
                                : testResult?.status === 'error'
                                ? 'error'
                                : 'network_check'}
                            </span>
                          </button>
                          {/* Edit */}
                          <button
                            onClick={() => setEditingProvider(provider)}
                            className="p-2 text-slate-400 hover:text-white hover:bg-border-dark rounded-lg transition-colors"
                            title="Edit provider"
                          >
                            <span className="material-symbols-outlined text-[20px]">edit</span>
                          </button>
                          {/* Delete */}
                          <button
                            onClick={() => handleDelete(provider.provider_id)}
                            className="p-2 text-slate-400 hover:text-red-400 hover:bg-red-500/10 rounded-lg transition-colors"
                            title="Delete provider"
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

        {/* Footer */}
        <div className="flex items-center justify-between px-6 py-4 border-t border-border-dark bg-[#151b28]">
          <span className="text-sm text-slate-400">
            Showing {data?.items.length || 0} of {data?.count || 0} providers
          </span>
        </div>
      </div>

      {/* Create Modal */}
      <Modal
        isOpen={showCreateModal}
        onClose={() => setShowCreateModal(false)}
        title="Add Provider"
      >
        <ProviderForm
          onSubmit={handleCreate}
          onCancel={() => setShowCreateModal(false)}
          isLoading={createMutation.isPending}
        />
      </Modal>

      {/* Edit Modal */}
      <Modal
        isOpen={!!editingProvider}
        onClose={() => setEditingProvider(null)}
        title="Edit Provider"
      >
        {editingProvider && (
          <ProviderForm
            initialData={editingProvider}
            onSubmit={handleUpdate}
            onCancel={() => setEditingProvider(null)}
            isLoading={updateMutation.isPending}
          />
        )}
      </Modal>
    </div>
  );
}
