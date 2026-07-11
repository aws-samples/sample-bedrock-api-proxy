import { useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { useTranslation } from 'react-i18next';
import { BarChart, Bar, XAxis, YAxis, ResponsiveContainer } from 'recharts';
import { useApiKeyDailyUsage } from '../hooks';
import { formatTokens, formatCurrency } from '../utils';

type Metric = 'cost' | 'tokens';

const POPOVER_WIDTH = 300;
const POPOVER_HEIGHT = 190; // approximate, used for edge flipping
const OPEN_DELAY_MS = 200; // don't fire requests while scanning across rows
const DAYS = 7;

interface UsageHoverChartProps {
  apiKey: string;
  metric: Metric;
  children: React.ReactNode;
}

interface ChartRow {
  date: string;
  cost?: number;
  input?: number;
  output?: number;
}

/**
 * Wraps a table cell and shows a 7-day usage thumbnail chart on hover.
 *
 * - metric="cost": daily total cost bars (for the Monthly Budget column)
 * - metric="tokens": daily input/output token stacked bars (for the Token Usage column)
 *
 * The popover renders through a portal with fixed positioning so it is not
 * clipped by the table's overflow containers. It is non-interactive
 * (pointer-events: none) to avoid hover flicker. Data is fetched lazily on
 * first hover and cached by react-query.
 */
export default function UsageHoverChart({ apiKey, metric, children }: UsageHoverChartProps) {
  const { t } = useTranslation();
  const anchorRef = useRef<HTMLDivElement>(null);
  const openTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [pos, setPos] = useState<{ top: number; left: number } | null>(null);

  const open = pos !== null;
  const { data, isLoading } = useApiKeyDailyUsage(apiKey, DAYS, open);

  useEffect(() => {
    // Clear any pending open timer on unmount
    return () => {
      if (openTimerRef.current) clearTimeout(openTimerRef.current);
    };
  }, []);

  const handleMouseEnter = () => {
    if (openTimerRef.current) clearTimeout(openTimerRef.current);
    openTimerRef.current = setTimeout(() => {
      const rect = anchorRef.current?.getBoundingClientRect();
      if (!rect) return;

      // Prefer below the cell; flip above when there is not enough room.
      let top = rect.bottom + 8;
      if (top + POPOVER_HEIGHT > window.innerHeight - 8) {
        top = rect.top - POPOVER_HEIGHT - 8;
      }
      // Clamp horizontally inside the viewport.
      let left = rect.left;
      if (left + POPOVER_WIDTH > window.innerWidth - 8) {
        left = window.innerWidth - POPOVER_WIDTH - 8;
      }
      setPos({ top: Math.max(8, top), left: Math.max(8, left) });
    }, OPEN_DELAY_MS);
  };

  const handleMouseLeave = () => {
    if (openTimerRef.current) {
      clearTimeout(openTimerRef.current);
      openTimerRef.current = null;
    }
    setPos(null);
  };

  const { rows, total, hasData } = useMemo(() => {
    const daily = data?.daily ?? [];
    if (metric === 'cost') {
      const rows: ChartRow[] = daily.map((day) => ({
        date: day.date.slice(5), // MM-DD
        cost: day.total_cost,
      }));
      const total = rows.reduce((sum, r) => sum + (r.cost ?? 0), 0);
      return { rows, total, hasData: total > 0 };
    }
    const rows: ChartRow[] = daily.map((day) => ({
      date: day.date.slice(5),
      input: day.models.reduce((sum, m) => sum + m.input_tokens, 0),
      output: day.models.reduce((sum, m) => sum + m.output_tokens, 0),
    }));
    const total = rows.reduce((sum, r) => sum + (r.input ?? 0) + (r.output ?? 0), 0);
    return { rows, total, hasData: total > 0 };
  }, [data, metric]);

  const title =
    metric === 'cost' ? t('apiKeys.hoverChart.costTitle') : t('apiKeys.hoverChart.tokensTitle');
  const totalLabel = metric === 'cost' ? formatCurrency(total, 2) : formatTokens(total);

  return (
    <div ref={anchorRef} onMouseEnter={handleMouseEnter} onMouseLeave={handleMouseLeave}>
      {children}
      {open &&
        createPortal(
          <div
            className="fixed z-[100] pointer-events-none"
            style={{ top: pos.top, left: pos.left, width: POPOVER_WIDTH }}
            role="tooltip"
          >
            <div className="bg-surface-dark border border-border-dark rounded-xl shadow-2xl p-3">
              {/* Header: title + 7-day total */}
              <div className="flex items-center justify-between mb-2">
                <span className="text-xs font-medium text-slate-400">{title}</span>
                {!isLoading && hasData && (
                  <span className="text-xs font-semibold text-white">{totalLabel}</span>
                )}
              </div>

              {isLoading ? (
                <div className="flex items-center justify-center h-[120px]">
                  <span className="material-symbols-outlined animate-spin text-2xl text-primary">
                    progress_activity
                  </span>
                </div>
              ) : !hasData ? (
                <div className="flex items-center justify-center h-[120px]">
                  <span className="text-xs text-slate-500">
                    {t('apiKeys.hoverChart.noData')}
                  </span>
                </div>
              ) : (
                <>
                  <div className="h-[120px]">
                    <ResponsiveContainer width="100%" height="100%">
                      <BarChart data={rows} margin={{ top: 4, right: 0, bottom: 0, left: 0 }}>
                        <XAxis
                          dataKey="date"
                          tick={{ fill: '#64748b', fontSize: 9 }}
                          axisLine={{ stroke: '#334155' }}
                          tickLine={false}
                          interval={0}
                        />
                        <YAxis hide />
                        {metric === 'cost' ? (
                          <Bar dataKey="cost" fill="#34d399" radius={[2, 2, 0, 0]} />
                        ) : (
                          <>
                            <Bar dataKey="input" stackId="tokens" fill="#34d399" />
                            <Bar
                              dataKey="output"
                              stackId="tokens"
                              fill="#60a5fa"
                              radius={[2, 2, 0, 0]}
                            />
                          </>
                        )}
                      </BarChart>
                    </ResponsiveContainer>
                  </div>
                  {metric === 'tokens' && (
                    <div className="flex items-center gap-3 mt-1.5">
                      <span className="flex items-center gap-1 text-[10px] text-slate-400">
                        <span className="size-2 rounded-sm bg-emerald-400"></span>
                        {t('apiKeys.inputTokens')}
                      </span>
                      <span className="flex items-center gap-1 text-[10px] text-slate-400">
                        <span className="size-2 rounded-sm bg-blue-400"></span>
                        {t('apiKeys.outputTokens')}
                      </span>
                    </div>
                  )}
                </>
              )}
            </div>
          </div>,
          document.body
        )}
    </div>
  );
}
