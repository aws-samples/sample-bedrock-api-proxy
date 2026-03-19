import { useState } from 'react';
import {
  useProviderKeys,
  useCreateProviderKey,
  useUpdateProviderKey,
  useDeleteProviderKey,
} from '../hooks';
import type { ProviderKey, ProviderKeyCreate } from '../types';

const PROVIDERS = ['bedrock', 'openai', 'anthropic', 'deepseek'];

function SlideOver({ isOpen, onClose, title, children }: {
  isOpen: boolean; onClose: () => void; title: string; children: React.ReactNode;
}) {
  if (!isOpen) return null;
  return (
    <div className="fixed inset-0 z-50 overflow-hidden">
      <div className="absolute inset-0 bg-black/50 backdrop-blur-sm" onClick={onClose} />
      <div className="absolute inset-y-0 right-0 max-w-md w-full bg-surface-dark shadow-2xl border-l border-border-dark flex flex-col">
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

function ProviderKeyForm({ onSubmit, onCancel, isLoading }: {
  onSubmit: (data: ProviderKeyCreate) => void; onCancel: () => void; isLoading: boolean;
}) {
  const [provider, setProvider] = useState('bedrock');
  const [apiKey, setApiKey] = useState('');
  const [models, setModels] = useState('');

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    onSubmit({
      provider,
      api_key: apiKey,
      models: models.split(',').map(m => m.trim()).filter(Boolean),
    });
  };

  return (
    <form onSubmit={handleSubmit} className="space-y-4">
      <div>
        <label className="block text-sm font-medium text-slate-300 mb-1">Provider</label>
        <select value={provider} onChange={e => setProvider(e.target.value)}
          className="w-full px-3 py-2 bg-slate-800 border border-border-dark rounded-lg text-white">
          {PROVIDERS.map(p => <option key={p} value={p}>{p}</option>)}
        </select>
      </div>
      <div>
        <label className="block text-sm font-medium text-slate-300 mb-1">API Key</label>
        <input type="password" value={apiKey} onChange={e => setApiKey(e.target.value)} required
          className="w-full px-3 py-2 bg-slate-800 border border-border-dark rounded-lg text-white" />
      </div>
      <div>
        <label className="block text-sm font-medium text-slate-300 mb-1">Models (comma-separated)</label>
        <input value={models} onChange={e => setModels(e.target.value)} required
          placeholder="claude-sonnet-4-5-20250929, claude-haiku-4-5-20251001"
          className="w-full px-3 py-2 bg-slate-800 border border-border-dark rounded-lg text-white" />
      </div>
      <div className="flex gap-3 pt-4">
        <button type="submit" disabled={isLoading}
          className="flex-1 px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-lg disabled:opacity-50">
          {isLoading ? 'Saving...' : 'Save'}
        </button>
        <button type="button" onClick={onCancel}
          className="px-4 py-2 bg-slate-700 hover:bg-slate-600 text-white rounded-lg">Cancel</button>
      </div>
    </form>
  );
}

function StatusBadge({ status, isEnabled }: { status: string; isEnabled: boolean }) {
  if (!isEnabled) return <span className="px-2 py-0.5 text-xs rounded-full bg-slate-700 text-slate-400">Disabled</span>;
  const colors: Record<string, string> = {
    available: 'bg-green-900/50 text-green-400',
    cooldown: 'bg-yellow-900/50 text-yellow-400',
    disabled: 'bg-slate-700 text-slate-400',
  };
  return <span className={`px-2 py-0.5 text-xs rounded-full ${colors[status] || colors.disabled}`}>{status}</span>;
}

export default function ProviderKeys() {
  const [showCreate, setShowCreate] = useState(false);
  const [deleteConfirm, setDeleteConfirm] = useState<string | null>(null);
  const { data: keys, isLoading } = useProviderKeys();
  const createMutation = useCreateProviderKey();
  const updateMutation = useUpdateProviderKey();
  const deleteMutation = useDeleteProviderKey();

  const handleCreate = (data: ProviderKeyCreate) => {
    createMutation.mutate(data, { onSuccess: () => setShowCreate(false) });
  };

  const handleToggle = (key: ProviderKey) => {
    updateMutation.mutate({ keyId: key.key_id, data: { is_enabled: !key.is_enabled } });
  };

  const handleDelete = (keyId: string) => {
    deleteMutation.mutate(keyId, { onSuccess: () => setDeleteConfirm(null) });
  };

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-white">Provider Keys</h1>
          <p className="text-slate-400 mt-1">Manage API keys for LLM providers</p>
        </div>
        <button onClick={() => setShowCreate(true)}
          className="px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-lg flex items-center gap-2">
          <span className="material-symbols-outlined text-sm">add</span> Add Key
        </button>
      </div>

      <div className="bg-surface-dark rounded-xl border border-border-dark overflow-hidden">
        <table className="w-full">
          <thead>
            <tr className="border-b border-border-dark">
              <th className="px-4 py-3 text-left text-xs font-medium text-slate-400 uppercase">Provider</th>
              <th className="px-4 py-3 text-left text-xs font-medium text-slate-400 uppercase">Key</th>
              <th className="px-4 py-3 text-left text-xs font-medium text-slate-400 uppercase">Models</th>
              <th className="px-4 py-3 text-left text-xs font-medium text-slate-400 uppercase">Status</th>
              <th className="px-4 py-3 text-right text-xs font-medium text-slate-400 uppercase">Actions</th>
            </tr>
          </thead>
          <tbody>
            {isLoading ? (
              <tr><td colSpan={5} className="px-4 py-8 text-center text-slate-400">Loading...</td></tr>
            ) : !keys?.length ? (
              <tr><td colSpan={5} className="px-4 py-8 text-center text-slate-400">No provider keys configured</td></tr>
            ) : keys.map(key => (
              <tr key={key.key_id} className="border-b border-border-dark/50 hover:bg-slate-800/30">
                <td className="px-4 py-3 text-white font-medium">{key.provider}</td>
                <td className="px-4 py-3 text-slate-300 font-mono text-sm">{key.api_key_masked}</td>
                <td className="px-4 py-3">
                  <div className="flex flex-wrap gap-1">
                    {key.models.map(m => (
                      <span key={m} className="px-1.5 py-0.5 text-xs bg-slate-700 text-slate-300 rounded">{m}</span>
                    ))}
                  </div>
                </td>
                <td className="px-4 py-3"><StatusBadge status={key.status} isEnabled={key.is_enabled} /></td>
                <td className="px-4 py-3 text-right">
                  <button onClick={() => handleToggle(key)} className="text-slate-400 hover:text-white mr-2" title={key.is_enabled ? 'Disable' : 'Enable'}>
                    <span className="material-symbols-outlined text-sm">{key.is_enabled ? 'toggle_on' : 'toggle_off'}</span>
                  </button>
                  <button onClick={() => setDeleteConfirm(key.key_id)} className="text-red-400 hover:text-red-300" title="Delete">
                    <span className="material-symbols-outlined text-sm">delete</span>
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <SlideOver isOpen={showCreate} onClose={() => setShowCreate(false)} title="Add Provider Key">
        <ProviderKeyForm onSubmit={handleCreate} onCancel={() => setShowCreate(false)} isLoading={createMutation.isPending} />
      </SlideOver>

      {deleteConfirm && (
        <div className="fixed inset-0 z-50 flex items-center justify-center">
          <div className="absolute inset-0 bg-black/50" onClick={() => setDeleteConfirm(null)} />
          <div className="relative bg-surface-dark rounded-xl border border-border-dark p-6 max-w-sm w-full">
            <h3 className="text-lg font-bold text-white mb-2">Delete Provider Key?</h3>
            <p className="text-slate-400 mb-4">This action cannot be undone.</p>
            <div className="flex gap-3">
              <button onClick={() => handleDelete(deleteConfirm)}
                className="flex-1 px-4 py-2 bg-red-600 hover:bg-red-700 text-white rounded-lg">Delete</button>
              <button onClick={() => setDeleteConfirm(null)}
                className="px-4 py-2 bg-slate-700 hover:bg-slate-600 text-white rounded-lg">Cancel</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
