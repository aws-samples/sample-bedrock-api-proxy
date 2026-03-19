import { useState } from 'react';
import {
  useRoutingRules,
  useCreateRoutingRule,
  useUpdateRoutingRule,
  useDeleteRoutingRule,
  useReorderRoutingRules,
  useSmartRoutingConfig,
  useUpdateSmartRoutingConfig,
} from '../hooks';
import type { RoutingRule, RoutingRuleCreate } from '../types';

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

function RuleForm({ onSubmit, onCancel, isLoading }: {
  onSubmit: (data: RoutingRuleCreate) => void; onCancel: () => void; isLoading: boolean;
}) {
  const [ruleName, setRuleName] = useState('');
  const [ruleType, setRuleType] = useState<'keyword' | 'regex' | 'model'>('keyword');
  const [pattern, setPattern] = useState('');
  const [targetModel, setTargetModel] = useState('');
  const [targetProvider, setTargetProvider] = useState('bedrock');

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    onSubmit({ rule_name: ruleName, rule_type: ruleType, pattern, target_model: targetModel, target_provider: targetProvider });
  };

  const patternHelp: Record<string, string> = {
    keyword: 'Comma-separated keywords (case-insensitive)',
    regex: 'Regular expression pattern',
    model: 'Comma-separated source model IDs',
  };

  return (
    <form onSubmit={handleSubmit} className="space-y-4">
      <div>
        <label className="block text-sm font-medium text-slate-300 mb-1">Rule Name</label>
        <input value={ruleName} onChange={e => setRuleName(e.target.value)} required
          className="w-full px-3 py-2 bg-slate-800 border border-border-dark rounded-lg text-white" />
      </div>
      <div>
        <label className="block text-sm font-medium text-slate-300 mb-1">Rule Type</label>
        <select value={ruleType} onChange={e => setRuleType(e.target.value as typeof ruleType)}
          className="w-full px-3 py-2 bg-slate-800 border border-border-dark rounded-lg text-white">
          <option value="keyword">Keyword</option>
          <option value="regex">Regex</option>
          <option value="model">Model</option>
        </select>
      </div>
      <div>
        <label className="block text-sm font-medium text-slate-300 mb-1">Pattern</label>
        <input value={pattern} onChange={e => setPattern(e.target.value)} required
          placeholder={patternHelp[ruleType]}
          className="w-full px-3 py-2 bg-slate-800 border border-border-dark rounded-lg text-white" />
        <p className="text-xs text-slate-500 mt-1">{patternHelp[ruleType]}</p>
      </div>
      <div>
        <label className="block text-sm font-medium text-slate-300 mb-1">Target Model</label>
        <input value={targetModel} onChange={e => setTargetModel(e.target.value)} required
          className="w-full px-3 py-2 bg-slate-800 border border-border-dark rounded-lg text-white" />
      </div>
      <div>
        <label className="block text-sm font-medium text-slate-300 mb-1">Target Provider</label>
        <input value={targetProvider} onChange={e => setTargetProvider(e.target.value)}
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

function SmartConfigPanel() {
  const { data: config } = useSmartRoutingConfig();
  const updateMutation = useUpdateSmartRoutingConfig();
  const [strongModel, setStrongModel] = useState('');
  const [weakModel, setWeakModel] = useState('');
  const [threshold, setThreshold] = useState(0.5);
  const [initialized, setInitialized] = useState(false);

  if (config && !initialized) {
    setStrongModel(config.strong_model);
    setWeakModel(config.weak_model);
    setThreshold(config.threshold);
    setInitialized(true);
  }

  const handleSave = () => {
    updateMutation.mutate({ strong_model: strongModel, weak_model: weakModel, threshold });
  };

  return (
    <div className="bg-surface-dark rounded-xl border border-border-dark p-6 space-y-4">
      <h2 className="text-lg font-bold text-white">Smart Routing Config</h2>
      <div className="grid grid-cols-2 gap-4">
        <div>
          <label className="block text-sm font-medium text-slate-300 mb-1">Strong Model</label>
          <input value={strongModel} onChange={e => setStrongModel(e.target.value)}
            className="w-full px-3 py-2 bg-slate-800 border border-border-dark rounded-lg text-white" />
        </div>
        <div>
          <label className="block text-sm font-medium text-slate-300 mb-1">Weak Model</label>
          <input value={weakModel} onChange={e => setWeakModel(e.target.value)}
            className="w-full px-3 py-2 bg-slate-800 border border-border-dark rounded-lg text-white" />
        </div>
      </div>
      <div>
        <label className="block text-sm font-medium text-slate-300 mb-1">
          Threshold: {threshold.toFixed(2)}
        </label>
        <input type="range" min="0" max="1" step="0.05" value={threshold}
          onChange={e => setThreshold(parseFloat(e.target.value))}
          className="w-full" />
      </div>
      <button onClick={handleSave} disabled={updateMutation.isPending}
        className="px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-lg disabled:opacity-50">
        {updateMutation.isPending ? 'Saving...' : 'Save Config'}
      </button>
    </div>
  );
}

export default function RoutingConfig() {
  const [showCreate, setShowCreate] = useState(false);
  const { data: rules, isLoading } = useRoutingRules();
  const createMutation = useCreateRoutingRule();
  const updateMutation = useUpdateRoutingRule();
  const deleteMutation = useDeleteRoutingRule();
  const reorderMutation = useReorderRoutingRules();

  const handleCreate = (data: RoutingRuleCreate) => {
    createMutation.mutate(data, { onSuccess: () => setShowCreate(false) });
  };

  const handleToggle = (rule: RoutingRule) => {
    updateMutation.mutate({ ruleId: rule.rule_id, data: { is_enabled: !rule.is_enabled } });
  };

  const handleMoveUp = (index: number) => {
    if (!rules || index === 0) return;
    const ids = rules.map(r => r.rule_id);
    [ids[index - 1], ids[index]] = [ids[index], ids[index - 1]];
    reorderMutation.mutate(ids);
  };

  const handleMoveDown = (index: number) => {
    if (!rules || index >= rules.length - 1) return;
    const ids = rules.map(r => r.rule_id);
    [ids[index], ids[index + 1]] = [ids[index + 1], ids[index]];
    reorderMutation.mutate(ids);
  };

  const typeColors: Record<string, string> = {
    keyword: 'bg-purple-900/50 text-purple-400',
    regex: 'bg-orange-900/50 text-orange-400',
    model: 'bg-cyan-900/50 text-cyan-400',
  };

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-white">Routing Configuration</h1>
          <p className="text-slate-400 mt-1">Manage routing rules and smart routing settings</p>
        </div>
        <button onClick={() => setShowCreate(true)}
          className="px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-lg flex items-center gap-2">
          <span className="material-symbols-outlined text-sm">add</span> Add Rule
        </button>
      </div>

      <div className="bg-surface-dark rounded-xl border border-border-dark overflow-hidden">
        <table className="w-full">
          <thead>
            <tr className="border-b border-border-dark">
              <th className="px-4 py-3 text-left text-xs font-medium text-slate-400 uppercase w-8">#</th>
              <th className="px-4 py-3 text-left text-xs font-medium text-slate-400 uppercase">Name</th>
              <th className="px-4 py-3 text-left text-xs font-medium text-slate-400 uppercase">Type</th>
              <th className="px-4 py-3 text-left text-xs font-medium text-slate-400 uppercase">Pattern</th>
              <th className="px-4 py-3 text-left text-xs font-medium text-slate-400 uppercase">Target</th>
              <th className="px-4 py-3 text-right text-xs font-medium text-slate-400 uppercase">Actions</th>
            </tr>
          </thead>
          <tbody>
            {isLoading ? (
              <tr><td colSpan={6} className="px-4 py-8 text-center text-slate-400">Loading...</td></tr>
            ) : !rules?.length ? (
              <tr><td colSpan={6} className="px-4 py-8 text-center text-slate-400">No routing rules configured</td></tr>
            ) : rules.map((rule, idx) => (
              <tr key={rule.rule_id} className="border-b border-border-dark/50 hover:bg-slate-800/30">
                <td className="px-4 py-3 text-slate-500">{idx + 1}</td>
                <td className="px-4 py-3 text-white font-medium">{rule.rule_name}</td>
                <td className="px-4 py-3">
                  <span className={`px-2 py-0.5 text-xs rounded-full ${typeColors[rule.rule_type] || ''}`}>{rule.rule_type}</span>
                </td>
                <td className="px-4 py-3 text-slate-300 font-mono text-sm max-w-[200px] truncate">{rule.pattern}</td>
                <td className="px-4 py-3 text-slate-300">{rule.target_model}</td>
                <td className="px-4 py-3 text-right space-x-1">
                  <button onClick={() => handleMoveUp(idx)} disabled={idx === 0} className="text-slate-400 hover:text-white disabled:opacity-30">
                    <span className="material-symbols-outlined text-sm">arrow_upward</span>
                  </button>
                  <button onClick={() => handleMoveDown(idx)} disabled={idx === (rules?.length ?? 0) - 1} className="text-slate-400 hover:text-white disabled:opacity-30">
                    <span className="material-symbols-outlined text-sm">arrow_downward</span>
                  </button>
                  <button onClick={() => handleToggle(rule)} className="text-slate-400 hover:text-white">
                    <span className="material-symbols-outlined text-sm">{rule.is_enabled ? 'toggle_on' : 'toggle_off'}</span>
                  </button>
                  <button onClick={() => deleteMutation.mutate(rule.rule_id)} className="text-red-400 hover:text-red-300">
                    <span className="material-symbols-outlined text-sm">delete</span>
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <SmartConfigPanel />

      <SlideOver isOpen={showCreate} onClose={() => setShowCreate(false)} title="Add Routing Rule">
        <RuleForm onSubmit={handleCreate} onCancel={() => setShowCreate(false)} isLoading={createMutation.isPending} />
      </SlideOver>
    </div>
  );
}
