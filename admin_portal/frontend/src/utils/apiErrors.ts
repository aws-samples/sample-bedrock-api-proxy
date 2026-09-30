/** Render FastAPI validation messages without echoing input/ctx (may contain secrets). */
export function apiErrorMessage(detail: unknown, fallback: string): string {
  if (typeof detail === 'string') return detail;
  if (!Array.isArray(detail)) return fallback;
  const messages = detail.flatMap((item: unknown) => {
    if (!item || typeof item !== 'object' || !('msg' in item) || typeof item.msg !== 'string') return [];
    const location = 'loc' in item && Array.isArray(item.loc)
      ? item.loc.filter((part: unknown) => typeof part === 'string' || typeof part === 'number').join('.')
      : '';
    return [`${location ? `${location}: ` : ''}${item.msg}`];
  });
  return messages.join('\n') || fallback;
}
