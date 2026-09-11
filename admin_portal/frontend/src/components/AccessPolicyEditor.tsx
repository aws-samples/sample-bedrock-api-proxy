import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import type { AccessPolicy, ModelMapping } from '../types';
import { policyLines, type AccessPolicyDraft, type PolicyIssue } from '../utils/accessPolicy';

const inputClass = 'w-full px-3 py-2 bg-input-bg border border-border-dark rounded-lg text-white focus:border-primary focus:ring-1 focus:ring-primary';

export function AccessPolicyBadges({ policy }: { policy?: AccessPolicy }) {
  const { t } = useTranslation();
  return (
    <div className="flex flex-wrap gap-1 mt-2">
      {policy?.ip.enabled && (
        <span className="text-xs px-2 py-0.5 rounded bg-amber-500/10 text-amber-300 border border-amber-500/20">
          {t('apiKeys.policy.ipBadge', { count: policy.ip.allow.length })}
        </span>
      )}
      {policy?.model.enabled && (
        <span className="text-xs px-2 py-0.5 rounded bg-blue-500/10 text-blue-300 border border-blue-500/20">
          {t('apiKeys.policy.modelBadge', { count: policy.model.allow.length })}
        </span>
      )}
      {!policy?.ip.enabled && !policy?.model.enabled && (
        <span className="text-xs text-slate-400">{t('apiKeys.policy.unrestricted')}</span>
      )}
    </div>
  );
}

export default function AccessPolicyEditor({
  value, onChange, mappings, mappingsLoading, mappingsError, onRetryMappings, issue,
}: {
  value: AccessPolicyDraft;
  onChange: (value: AccessPolicyDraft) => void;
  mappings: ModelMapping[];
  mappingsLoading: boolean;
  mappingsError: boolean;
  onRetryMappings: () => void;
  issue?: PolicyIssue;
}) {
  const { t } = useTranslation();
  const [selection, setSelection] = useState('');
  const selected = mappings.find((mapping) => mapping.anthropic_model_id === selection);
  const models = policyLines(value.modelText);
  // Catalogue aliases are hints, not validation: some adapters send them literally.
  const aliasHints = mappings.filter((mapping) =>
    models.includes(mapping.anthropic_model_id) && mapping.anthropic_model_id !== mapping.bedrock_model_id,
  );
  const addTarget = () => {
    if (!selected || models.includes(selected.bedrock_model_id)) return;
    onChange({ ...value, modelText: [...models, selected.bedrock_model_id].join('\n') });
  };

  return (
    <fieldset className="border border-border-dark rounded-lg p-4 min-w-0 space-y-4">
      <legend className="px-1 text-sm font-semibold text-white">{t('apiKeys.policy.title')}</legend>
      <p className="text-xs text-slate-400">{t('apiKeys.policy.intro')}</p>
      <div className="space-y-2">
        <label className="flex items-center gap-2 text-sm font-medium text-slate-300">
          <input type="checkbox" role="switch" checked={value.ipEnabled}
            onChange={(e) => onChange({ ...value, ipEnabled: e.target.checked })}
            className="size-4 accent-primary" aria-controls="policy-ip-list" />
          {t('apiKeys.policy.ipEnabled')}
        </label>
        <label htmlFor="policy-ip-list" className="block text-sm text-slate-300">{t('apiKeys.policy.ipList')}</label>
        <textarea id="policy-ip-list" rows={3} spellCheck={false} value={value.ipText}
          onChange={(e) => onChange({ ...value, ipText: e.target.value })}
          className={`${inputClass} font-mono text-xs`}
          placeholder={'203.0.113.8\n2001:db8::/48'}
          aria-describedby="policy-ip-help" />
        <p id="policy-ip-help" className="text-xs text-slate-400">{t('apiKeys.policy.ipHelp')}</p>
      </div>
      <div className="space-y-2 border-t border-border-dark pt-4">
        <label className="flex items-center gap-2 text-sm font-medium text-slate-300">
          <input type="checkbox" role="switch" checked={value.modelEnabled}
            onChange={(e) => onChange({ ...value, modelEnabled: e.target.checked })}
            className="size-4 accent-primary" aria-controls="policy-model-list" />
          {t('apiKeys.policy.modelEnabled')}
        </label>
        <label htmlFor="policy-model-mapping" className="block text-sm text-slate-300">{t('apiKeys.policy.chooser')}</label>
        <select id="policy-model-mapping" value={selection} onChange={(e) => setSelection(e.target.value)}
          disabled={mappingsLoading || mappingsError} className={`${inputClass} text-sm`}>
          <option value="">{t(mappingsLoading ? 'common.loading' : 'apiKeys.policy.chooseModel')}</option>
          {mappings.map((mapping) => (
            <option key={mapping.anthropic_model_id} value={mapping.anthropic_model_id}>
              {mapping.anthropic_model_id} → {mapping.bedrock_model_id}
            </option>
          ))}
        </select>
        {mappingsError && (
          <p className="text-xs text-amber-300" role="status">
            {t('apiKeys.policy.mappingError')}{' '}
            <button type="button" onClick={onRetryMappings} className="underline">{t('apiKeys.policy.retry')}</button>
          </p>
        )}
        {selected && (
          <div className="bg-input-bg border border-border-dark rounded-lg p-3 space-y-2">
            <p className="text-xs text-slate-400">{t('apiKeys.policy.resolvedTarget')}</p>
            <code className="block text-xs text-blue-300 break-all">{selected.bedrock_model_id}</code>
            <button type="button" onClick={addTarget} disabled={models.includes(selected.bedrock_model_id)}
              className="text-sm text-primary hover:text-blue-300 disabled:text-slate-500">
              {t('apiKeys.policy.addTarget')}
            </button>
          </div>
        )}
        <label htmlFor="policy-model-list" className="block text-sm text-slate-300">{t('apiKeys.policy.modelList')}</label>
        <textarea id="policy-model-list" rows={4} spellCheck={false} value={value.modelText}
          onChange={(e) => onChange({ ...value, modelText: e.target.value })}
          className={`${inputClass} font-mono text-xs`}
          aria-describedby={`policy-model-help${aliasHints.length ? ' policy-model-alias-warning' : ''}`} />
        <p id="policy-model-help" className="text-xs text-slate-400">{t('apiKeys.policy.modelHelp')}</p>
        {aliasHints.length > 0 && (
          <div id="policy-model-alias-warning" role="status" className="text-xs text-amber-300 break-words space-y-1">
            {aliasHints.map((mapping) => (
              <p key={mapping.anthropic_model_id}>
                {t('apiKeys.policy.aliasWarning', { entry: mapping.anthropic_model_id, target: mapping.bedrock_model_id })}
              </p>
            ))}
          </div>
        )}
        <p className="text-xs text-slate-400">{t('apiKeys.policy.literalNotice')}</p>
      </div>
      {issue && (
        <p id="policy-validation-error" role="alert" className="text-sm text-red-300 break-words">
          {t(`apiKeys.policy.errors.${issue.code}`, { entry: issue.entry })}
        </p>
      )}
      <div className="border-t border-border-dark pt-3 space-y-2 text-xs text-slate-400">
        <p>{t('apiKeys.policy.limits')}</p>
        <p>{t('apiKeys.policy.activation')}</p>
        <p>{t('apiKeys.policy.retention')}</p>
        <p>{t('apiKeys.policy.deployment')}</p>
      </div>
    </fieldset>
  );
}
