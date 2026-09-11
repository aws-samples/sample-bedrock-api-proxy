import type { AccessPolicy } from '../types';

// Keep in sync with app/schemas/access_policy.py. The server is authoritative.
export const MAX_POLICY_ENTRIES = 100;
export const MAX_MODEL_ID_LENGTH = 2048;
export const MAX_POLICY_BYTES = 64 * 1024;

export interface AccessPolicyDraft {
  ipEnabled: boolean;
  ipText: string;
  modelEnabled: boolean;
  modelText: string;
}

export function policyDraft(policy?: AccessPolicy): AccessPolicyDraft {
  return {
    ipEnabled: policy?.ip.enabled ?? false,
    ipText: policy?.ip.allow.join('\n') ?? '',
    modelEnabled: policy?.model.enabled ?? false,
    modelText: policy?.model.allow.join('\n') ?? '',
  };
}

export function policyLines(text: string): string[] {
  return text.split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
}

export function draftPolicy(draft: AccessPolicyDraft): AccessPolicy {
  return {
    version: 1,
    ip: { enabled: draft.ipEnabled, allow: policyLines(draft.ipText) },
    model: { enabled: draft.modelEnabled, allow: policyLines(draft.modelText) },
  };
}

function ipv4Parts(address: string): number[] | undefined {
  const parts = address.split('.');
  if (parts.length !== 4 || parts.some((part) => !/^(0|[1-9]\d{0,2})$/.test(part) || Number(part) > 255)) {
    return undefined;
  }
  return parts.map(Number);
}

// Parse only address literals. Never use URL parsing (it can normalize invalid
// IPv4 forms) or mask off CIDR host bits to make invalid user input acceptable.
export function validIPRule(rule: string): boolean {
  if (rule.length > 64 || /[%\s\[\]]/.test(rule)) return false;
  const [address, prefix, ...extra] = rule.split('/');
  if (extra.length || (prefix !== undefined && !/^\d+$/.test(prefix))) return false;
  let width: number;
  let value = 0n;
  if (!address.includes(':')) {
    const parts = ipv4Parts(address);
    if (!parts) return false;
    width = 32;
    for (const part of parts) value = (value << 8n) | BigInt(part);
  } else {
    width = 128;
    let hexAddress = address;
    if (address.includes('.')) {
      const lastColon = address.lastIndexOf(':');
      const parts = ipv4Parts(address.slice(lastColon + 1));
      if (!parts) return false;
      hexAddress = `${address.slice(0, lastColon + 1)}${((parts[0] << 8) | parts[1]).toString(16)}:${((parts[2] << 8) | parts[3]).toString(16)}`;
    }
    const halves = hexAddress.split('::');
    if (halves.length > 2) return false;
    const left = halves[0] ? halves[0].split(':') : [];
    const right = halves[1] ? halves[1].split(':') : [];
    const count = left.length + right.length;
    if (halves.length === 1 ? count !== 8 : count >= 8) return false;
    const groups = [...left, ...Array(8 - count).fill('0') as string[], ...right];
    if (groups.some((group) => !/^[0-9a-f]{1,4}$/i.test(group))) return false;
    for (const group of groups) value = (value << 16n) | BigInt(parseInt(group, 16));
  }
  const bits = prefix === undefined ? width : Number(prefix);
  if (!Number.isInteger(bits) || bits < 0 || bits > width) return false;
  const hostMask = (1n << BigInt(width - bits)) - 1n;
  return (value & hostMask) === 0n;
}

export interface PolicyIssue {
  code: 'emptyIP' | 'emptyModel' | 'tooMany' | 'invalidIP' | 'invalidModel' | 'tooLarge';
  entry?: string;
}

export function validatePolicy(policy: AccessPolicy): PolicyIssue | undefined {
  if (policy.ip.enabled && !policy.ip.allow.length) return { code: 'emptyIP' };
  if (policy.model.enabled && !policy.model.allow.length) return { code: 'emptyModel' };
  // Disabled lists are deliberately retained and must still be valid on write.
  if (policy.ip.allow.length > MAX_POLICY_ENTRIES || policy.model.allow.length > MAX_POLICY_ENTRIES) {
    return { code: 'tooMany' };
  }
  for (const entry of policy.ip.allow) {
    if (!validIPRule(entry)) return { code: 'invalidIP', entry };
  }
  for (const entry of policy.model.allow) {
    const characters = [...entry];
    if (characters.length > MAX_MODEL_ID_LENGTH || characters.some((char) =>
      /\s/u.test(char) || char.charCodeAt(0) < 32 || char.charCodeAt(0) === 127 || char === '\u0085'
    )) {
      return { code: 'invalidModel', entry: entry.slice(0, 80) };
    }
    // Entries are literal wire IDs, even when also catalogued as aliases.
    // Legacy OpenAI compatibility can send the original name (e.g. gpt-5.4).
    // Mapping suggestions must never validate or rewrite stored permissions.
  }
  if (new TextEncoder().encode(JSON.stringify(policy)).length > MAX_POLICY_BYTES) {
    return { code: 'tooLarge' };
  }
  return undefined;
}
