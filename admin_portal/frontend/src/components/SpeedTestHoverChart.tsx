import { useMemo } from 'react';
import { createPortal } from 'react-dom';
import { useTranslation } from 'react-i18next';
import {
  ComposedChart,
  Bar,
  Line,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  type TooltipContentProps,
} from 'recharts';
import { useHoverPopover } from '../hooks/useHoverPopover';
import { useSpeedTestHistory } from '../hooks/useModelMapping';
import { formatLatencyMs, formatTokensPerSecond, formatNumber } from '../utils';
import type { SpeedTestRecord } from '../types';

const POPOVER_WIDTH = 340;
const POPOVER_HEIGHT = 250; // approximate, used for edge flipping
const OPEN_DELAY_MS = 200; // don't fire requests while scanning across rows
const CLOSE_DELAY_MS = 150; // let the pointer cross the gap into the interactive popover
const HISTORY_LIMIT = 10;

const TTFT_COLOR = '#f59e0b'; // amber-500
const OTPS_COLOR = '#60a5fa'; // blue-400

interface SpeedTestHoverChartProps {
  bedrockModelId: string;
  children: React.ReactNode;
}

/** One x slot per run, oldest -> newest. Failed runs keep their slot with null values (gaps). */
interface ChartRow {
  run: number;
  record: SpeedTestRecord;
  ttft_ms: number | null;
  otps: number | null;
}

function formatTestedAt(epochMs: number): string {
  const d = new Date(epochMs);
  return d.toLocaleString(undefined, {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  });
}

/**
 * Wraps the Speed cell on the Model Mapping page and shows the last 10
 * speed-test runs for a Bedrock model ID on hover.
 *
 * Same portal / fixed-positioning / lazy-fetch approach as UsageHoverChart
 * (shared via useHoverPopover). Unlike UsageHoverChart the popover is
 * interactive so the recharts point tooltip works; a short close delay keeps
 * it open while the pointer moves from the cell into the popover (portal
 * children count as inside the anchor for React's onMouseEnter/Leave).
 */
export default function SpeedTestHoverChart({ bedrockModelId, children }: SpeedTestHoverChartProps) {
  const { t } = useTranslation();
  const { anchorRef, pos, handleMouseEnter, handleMouseLeave } = useHoverPopover({
    width: POPOVER_WIDTH,
    height: POPOVER_HEIGHT,
    openDelayMs: OPEN_DELAY_MS,
    closeDelayMs: CLOSE_DELAY_MS,
  });

  const open = pos !== null;
  const { data, isLoading } = useSpeedTestHistory(bedrockModelId, open, HISTORY_LIMIT);

  const { rows, failed, hasData } = useMemo(() => {
    // API returns newest first; chart runs oldest -> newest.
    const ordered = [...(data?.items ?? [])].reverse();
    const rows: ChartRow[] = ordered.map((record, i) => ({
      run: i + 1,
      record,
      ttft_ms: record.status === 'ok' ? record.ttft_ms : null,
      otps: record.status === 'ok' ? record.otps : null,
    }));
    const failed = rows.filter((r) => r.record.status === 'error');
    return { rows, failed, hasData: rows.length > 0 };
  }, [data]);

  const renderTooltip = ({ active, payload }: TooltipContentProps) => {
    if (!active || !payload || payload.length === 0) return null;
    const row = payload[0].payload as ChartRow | undefined;
    if (!row) return null;
    const rec = row.record;
    return (
      <div className="bg-slate-900 border border-border-dark rounded-lg shadow-xl px-3 py-2 text-xs space-y-0.5">
        <div className="text-slate-400">
          #{row.run} · {formatTestedAt(rec.tested_at)}
        </div>
        {rec.status === 'error' ? (
          <div className="text-red-400 max-w-[260px] break-words">
            {t('modelMapping.speed.failed')}: {rec.error ?? '—'}
          </div>
        ) : (
          <>
            <div className="text-white">
              <span className="text-slate-400">{t('modelMapping.speed.ttft')}: </span>
              {formatLatencyMs(rec.ttft_ms)}
            </div>
            <div className="text-white">
              <span className="text-slate-400">{t('modelMapping.speed.otps')}: </span>
              {formatTokensPerSecond(rec.otps)}
            </div>
            <div className="text-white">
              <span className="text-slate-400">{t('modelMapping.speed.tokens')}: </span>
              {rec.output_tokens === null ? '—' : formatNumber(rec.output_tokens)}
            </div>
            {rec.reasoning_tokens != null && rec.reasoning_tokens > 0 && (
              <div className="text-white">
                <span className="text-slate-400">{t('modelMapping.speed.hiddenReasoningTokens')}: </span>
                {formatNumber(rec.reasoning_tokens)}
              </div>
            )}
            {rec.has_reasoning && (
              <div className="text-violet-300">{t('modelMapping.speed.reasoning')}</div>
            )}
          </>
        )}
      </div>
    );
  };

  return (
    <div ref={anchorRef} onMouseEnter={handleMouseEnter} onMouseLeave={handleMouseLeave}>
      {children}
      {open &&
        createPortal(
          <div
            className="fixed z-[100]"
            style={{ top: pos.top, left: pos.left, width: POPOVER_WIDTH }}
            role="tooltip"
          >
            <div className="bg-surface-dark border border-border-dark rounded-xl shadow-2xl p-3">
              {/* Header: title + run count */}
              <div className="flex items-center justify-between mb-2">
                <span className="text-xs font-medium text-slate-400">
                  {t('modelMapping.speed.hoverTitle')}
                </span>
                {!isLoading && hasData && (
                  <span className="text-xs font-semibold text-white">{rows.length}</span>
                )}
              </div>

              {isLoading ? (
                <div className="flex items-center justify-center h-[140px]">
                  <span className="material-symbols-outlined animate-spin text-2xl text-primary">
                    progress_activity
                  </span>
                </div>
              ) : !hasData ? (
                <div className="flex items-center justify-center h-[140px]">
                  <span className="text-xs text-slate-500">{t('modelMapping.speed.noData')}</span>
                </div>
              ) : (
                <>
                  <div className="h-[140px]">
                    <ResponsiveContainer width="100%" height="100%">
                      <ComposedChart data={rows} margin={{ top: 4, right: 0, bottom: 0, left: 0 }}>
                        <XAxis
                          dataKey="run"
                          tick={{ fill: '#64748b', fontSize: 9 }}
                          axisLine={{ stroke: '#334155' }}
                          tickLine={false}
                          interval={0}
                        />
                        <YAxis
                          yAxisId="left"
                          tick={{ fill: TTFT_COLOR, fontSize: 9 }}
                          axisLine={false}
                          tickLine={false}
                          width={36}
                        />
                        <YAxis
                          yAxisId="right"
                          orientation="right"
                          tick={{ fill: OTPS_COLOR, fontSize: 9 }}
                          axisLine={false}
                          tickLine={false}
                          width={32}
                        />
                        <Tooltip
                          content={renderTooltip}
                          cursor={{ fill: 'rgba(148, 163, 184, 0.08)' }}
                          isAnimationActive={false}
                        />
                        <Bar
                          yAxisId="left"
                          dataKey="ttft_ms"
                          fill={TTFT_COLOR}
                          radius={[2, 2, 0, 0]}
                          isAnimationActive={false}
                        />
                        <Line
                          yAxisId="right"
                          type="monotone"
                          dataKey="otps"
                          stroke={OTPS_COLOR}
                          strokeWidth={2}
                          dot={{ r: 2.5, fill: OTPS_COLOR, strokeWidth: 0 }}
                          connectNulls={false}
                          isAnimationActive={false}
                        />
                      </ComposedChart>
                    </ResponsiveContainer>
                  </div>
                  <div className="flex items-center gap-3 mt-1.5">
                    <span className="flex items-center gap-1 text-[10px] text-slate-400">
                      <span className="size-2 rounded-sm bg-amber-500"></span>
                      {t('modelMapping.speed.ttft')} (ms)
                    </span>
                    <span className="flex items-center gap-1 text-[10px] text-slate-400">
                      <span className="size-2 rounded-full bg-blue-400"></span>
                      {t('modelMapping.speed.otps')} (tok/s)
                    </span>
                  </div>
                  {failed.length > 0 && (
                    <ul className="mt-2 pt-2 border-t border-border-dark space-y-0.5 max-h-[72px] overflow-y-auto">
                      {failed.map((row) => (
                        <li
                          key={row.record.tested_at}
                          className="text-[10px] text-red-400 truncate"
                          title={row.record.error ?? undefined}
                        >
                          #{row.run} {formatTestedAt(row.record.tested_at)} —{' '}
                          {row.record.error ?? t('modelMapping.speed.failed')}
                        </li>
                      ))}
                    </ul>
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
