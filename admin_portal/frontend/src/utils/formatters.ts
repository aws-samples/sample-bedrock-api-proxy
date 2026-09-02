/**
 * Utility functions for formatting numbers and values.
 */

/**
 * Format token count with appropriate suffix (k, M, B).
 *
 * Examples:
 * - 500 -> "500"
 * - 1500 -> "1.5k"
 * - 198399900 -> "198.400M"
 * - 1234567890 -> "1.235B"
 *
 * @param tokens - The token count to format
 * @param decimals - Number of decimal places (default: 3 for M/B, 1 for k)
 * @returns Formatted string with appropriate suffix
 */
export function formatTokens(tokens: number | undefined | null): string {
  const value = tokens || 0;

  const BILLION = 1_000_000_000;
  const MILLION = 1_000_000;
  const THOUSAND = 1_000;

  if (value >= BILLION) {
    return `${(value / BILLION).toFixed(3)}B`;
  }

  if (value >= MILLION) {
    return `${(value / MILLION).toFixed(3)}M`;
  }

  if (value >= THOUSAND) {
    return `${(value / THOUSAND).toFixed(1)}k`;
  }

  return value.toString();
}

/**
 * Format a number with locale-aware thousands separators.
 *
 * @param value - The number to format
 * @returns Formatted string with thousands separators
 */
export function formatNumber(value: number | undefined | null): string {
  return (value || 0).toLocaleString();
}

/**
 * Format currency value.
 *
 * @param value - The currency amount
 * @param decimals - Number of decimal places (default: 2)
 * @returns Formatted string with $ prefix
 */
export function formatCurrency(value: number | undefined | null, decimals: number = 2): string {
  return `$${(value || 0).toFixed(decimals)}`;
}

/**
 * Compute the prompt cache hit rate as a percentage (0-100).
 *
 * Formula: cacheRead / (cacheRead + cacheWrite + input)
 *
 * Here `input` is the cache-exclusive (uncached) input token count, so the
 * denominator is the total number of prompt-side tokens processed. Returns
 * `null` when there were no prompt-side tokens at all (avoids divide-by-zero),
 * which callers can render as a placeholder (e.g. "—").
 *
 * @param cacheRead - Cache read (cached) input tokens
 * @param cacheWrite - Cache write (cache creation) tokens
 * @param input - Uncached input tokens
 * @returns Hit rate percentage (0-100), or null when no prompt tokens exist
 */
export function cacheHitRate(
  cacheRead: number | undefined | null,
  cacheWrite: number | undefined | null,
  input: number | undefined | null
): number | null {
  const read = cacheRead || 0;
  const write = cacheWrite || 0;
  const inp = input || 0;
  const denominator = read + write + inp;
  if (denominator <= 0) return null;
  return (read / denominator) * 100;
}

/**
 * Format a prompt cache hit rate for display.
 *
 * @param rate - Hit rate percentage (0-100) or null
 * @param decimals - Number of decimal places (default: 1)
 * @returns Formatted string like "85.3%", or "—" when rate is null
 */
export function formatCacheHitRate(rate: number | null, decimals: number = 1): string {
  if (rate === null) return '—';
  return `${rate.toFixed(decimals)}%`;
}

/**
 * Format a latency in milliseconds for the speed-test column, e.g. "812 ms".
 * Returns an em dash when the value is missing.
 */
export function formatLatencyMs(ms: number | null | undefined): string {
  if (ms === null || ms === undefined || !Number.isFinite(ms)) return '—';
  return `${Math.round(ms).toLocaleString()} ms`;
}

/**
 * Format an output-tokens-per-second value, e.g. "48.3 tok/s".
 * Returns an em dash when the value is missing.
 */
export function formatTokensPerSecond(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '—';
  return `${value.toFixed(1)} tok/s`;
}
