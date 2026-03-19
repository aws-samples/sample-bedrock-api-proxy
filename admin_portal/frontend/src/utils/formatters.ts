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
