import { useState } from 'react';
import {
  useFailoverChains,
  useCreateFailoverChain,
  useDeleteFailoverChain,
} from '../hooks';
import type { FailoverChainCreate, FailoverTarget } from '../types';

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

function ChainForm({ onSubmit, onCancel, isLoading }: {
  onSubmit: (data: FailoverChainCreate) => void; onCancel: () => void; isLoading: boolean;
}) {
  const [sourceModel, setSourceModel] = useState('');
  const [targets, setTargets] = useState<FailoverTarget[]>([{ provider: 'bedrock', model: '' }]);

  const addTarget = () => setTargets([...targets, { provider: 'bedrock', model: '' }]);
  const removeTarget = (idx: number) => setTargets(targets.filter((_, i) => i !== idx));
  const updateTarget = (idx: number, field: keyof FailoverTarget, value: string) => {
    const updated = [...targets];
    updated[idx] = { ...updated[idx], [field]: value };
    setTargets(updated);
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    onSubmit({ source_model: sourceModel, targets: targets.filter(t => t.model) });
  };

  return (
    <form onSubmit={handleSubmit} className="space-y-4">
      <div>
        <label className="block text-sm font-medium text-slate-300 mb-1">Source Model</label>
        <input value={sourceModel} onChange={e => setSourceModel(e.target.value)} required
          placeholder="claude-sonnet-4-5-20250929"
          className="w-full px-3 py-2 bg-slate-800 border border-border-dark rounded-lg text-white" />
      </div>
      <div>
        <label className="block text-sm font-medium text-slate-300 mb-2">Failover Targets (in order)</label>
        {targets.map((target, idx) => (
          <div key={idx} className="flex gap-2 mb-2">
            <input value={target.provider} onChange={e => updateTarget(idx, 'provider', e.target.value)}
              placeholder="provider" className="w-1/3 px-3 py-2 bg-slate-800 border border-border-dark rounded-lg text-white text-sm" />
            <input value={target.model} onChange={e => updateTarget(idx, 'model', e.target.value)}
              placeholder="model" className="flex-1 px-3 py-2 bg-slate-800 border border-border-dark rounded-lg text-white text-sm" />
            {targets.length > 1 && (
              <button type="button" onClick={() => removeTarget(idx)} className="text-red-400 hover:text-red-300">
                <span className="material-symbols-outlined text-sm">remove_circle</span>
              </button>
            )}
          </div>
        ))}
        <button type="button" onClick={addTarget}
          className="text-sm text-blue-400 hover:text-blue-300 flex items-center gap-1">
          <span className="material-symbols-outlined text-sm">add</span> Add target
        </button>
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

export default function FailoverConfig() {
  const [showCreate, setShowCreate] = useState(false);
  const [deleteConfirm, setDeleteConfirm] = useState<string | null>(null);
  const { data: chains, isLoading } = useFailoverChains();
  const createMutation = useCreateFailoverChain();
  const deleteMutation = useDeleteFailoverChain();

  const handleCreate = (data: FailoverChainCreate) => {
    createMutation.mutate(data, { onSuccess: () => setShowCreate(false) });
  };

  const handleDelete = (sourceModel: string) => {
    deleteMutation.mutate(sourceModel, { onSuccess: () => setDeleteConfirm(null) });
  };

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-white">Failover Configuration</h1>
          <p className="text-slate-400 mt-1">Configure model failover chains</p>
        </div>
        <button onClick={() => setShowCreate(true)}
          className="px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-lg flex items-center gap-2">
          <span className="material-symbols-outlined text-sm">add</span> Add Chain
        </button>
      </div>

      <div className="space-y-4">
        {isLoading ? (
          <div className="text-center text-slate-400 py-8">Loading...</div>
        ) : !chains?.length ? (
          <div className="bg-surface-dark rounded-xl border border-border-dark p-8 text-center text-slate-400">
            No failover chains configured
          </div>
        ) : chains.map(chain => (
          <div key={chain.source_model} className="bg-surface-dark rounded-xl border border-border-dark p-4">
            <div className="flex items-center justify-between mb-3">
              <h3 className="text-white font-medium">{chain.source_model}</h3>
              <button onClick={() => setDeleteConfirm(chain.source_model)} className="text-red-400 hover:text-red-300">
                <span className="material-symbols-outlined text-sm">delete</span>
              </button>
            </div>
            <div className="flex items-center gap-2 flex-wrap">
              <span className="text-slate-400 text-sm">Failover chain:</span>
              {chain.targets.map((t, idx) => (
                <span key={idx} className="flex items-center gap-1">
                  {idx > 0 && <span className="material-symbols-outlined text-slate-500 text-sm">arrow_forward</span>}
                  <span className="px-2 py-0.5 text-xs bg-slate-700 text-slate-300 rounded">
                    {t.provider}/{t.model}
                  </span>
                </span>
              ))}
            </div>
          </div>
        ))}
      </div>

      <SlideOver isOpen={showCreate} onClose={() => setShowCreate(false)} title="Add Failover Chain">
        <ChainForm onSubmit={handleCreate} onCancel={() => setShowCreate(false)} isLoading={createMutation.isPending} />
      </SlideOver>

      {deleteConfirm && (
        <div className="fixed inset-0 z-50 flex items-center justify-center">
          <div className="absolute inset-0 bg-black/50" onClick={() => setDeleteConfirm(null)} />
          <div className="relative bg-surface-dark rounded-xl border border-border-dark p-6 max-w-sm w-full">
            <h3 className="text-lg font-bold text-white mb-2">Delete Failover Chain?</h3>
            <p className="text-slate-400 mb-4">This will remove the failover chain for {deleteConfirm}.</p>
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
