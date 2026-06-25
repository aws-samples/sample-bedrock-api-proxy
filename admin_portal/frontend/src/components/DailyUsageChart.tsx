import { useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  ResponsiveContainer,
} from 'recharts';
import { useDailyUsage } from '../hooks';
import { formatTokens, formatCurrency } from '../utils';

type Metric = 'tokens' | 'cost';

// Stable, readable palette assigned per model (cycles if there are many).
const MODEL_COLORS = [
  '#22d3ee', // cyan
  '#a78bfa', // violet
  '#34d399', // emerald
  '#fbbf24', // amber
  '#f87171', // red
  '#60a5fa', // blue
  '#f472b6', // pink
  '#a3e635', // lime
];

const RANGE_OPTIONS = [7, 14, 30];

interface ChartRow {
  date: string;
  [model: string]: number | string;
}

export default function DailyUsageChart() {
  const { t } = useTranslation();
  const [days, setDays] = useState(30);
  const [metric, setMetric] = useState<Metric>('cost');
  const { data, isLoading, error } = useDailyUsage(days);

  // Flatten { date, models[] } into recharts rows keyed by model, and collect
  // the distinct model set so each gets its own stacked <Bar>.
  const { rows, models } = useMemo(() => {
    const modelSet = new Set<string>();
    const rows: ChartRow[] = (data?.daily ?? []).map((day) => {
      const row: ChartRow = { date: day.date.slice(5) }; // MM-DD for axis
      for (const m of day.models) {
        modelSet.add(m.model);
        row[m.model] = metric === 'tokens' ? m.tokens : m.cost;
      }
      return row;
    });
    return { rows, models: Array.from(modelSet) };
  }, [data, metric]);

  const yTickFormatter = (v: number) =>
    metric === 'tokens' ? formatTokens(v) : formatCurrency(v, 2);

  return (
    <div className="bg-surface-dark border border-border-dark rounded-xl p-5 shadow-sm">
      {/* Header: title + controls */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 mb-4">
        <div className="flex items-center gap-2">
          <span className="material-symbols-outlined text-cyan-500">bar_chart</span>
          <span className="text-slate-400 text-sm font-medium">
            {t('dashboard.dailyUsage')}
          </span>
        </div>
        <div className="flex items-center gap-3">
          {/* token / cost toggle */}
          <div className="flex rounded-lg border border-border-dark overflow-hidden">
            <button
              type="button"
              onClick={() => setMetric('cost')}
              className={`px-3 py-1 text-xs font-medium transition-colors ${
                metric === 'cost'
                  ? 'bg-primary text-white'
                  : 'text-slate-400 hover:text-white'
              }`}
            >
              {t('dashboard.cost')}
            </button>
            <button
              type="button"
              onClick={() => setMetric('tokens')}
              className={`px-3 py-1 text-xs font-medium transition-colors ${
                metric === 'tokens'
                  ? 'bg-primary text-white'
                  : 'text-slate-400 hover:text-white'
              }`}
            >
              {t('dashboard.tokens')}
            </button>
          </div>
          {/* range dropdown */}
          <select
            value={days}
            onChange={(e) => setDays(Number(e.target.value))}
            className="bg-background-dark border border-border-dark rounded-lg px-2 py-1 text-xs text-white"
          >
            {RANGE_OPTIONS.map((d) => (
              <option key={d} value={d}>
                {t('dashboard.lastNDays', { count: d })}
              </option>
            ))}
          </select>
        </div>
      </div>

      {isLoading && (
        <div className="flex items-center justify-center h-72">
          <span className="material-symbols-outlined animate-spin text-3xl text-primary">
            progress_activity
          </span>
        </div>
      )}

      {error && !isLoading && (
        <div className="flex items-center justify-center h-72 text-red-400 text-sm">
          {t('dashboard.dailyUsageError')}
        </div>
      )}

      {!isLoading && !error && (
        <ResponsiveContainer width="100%" height={300}>
          <BarChart data={rows} margin={{ top: 8, right: 8, left: 8, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#334155" vertical={false} />
            <XAxis dataKey="date" tick={{ fill: '#94a3b8', fontSize: 11 }} />
            <YAxis tick={{ fill: '#94a3b8', fontSize: 11 }} tickFormatter={yTickFormatter} width={56} />
            <Tooltip
              contentStyle={{
                background: '#0f172a',
                border: '1px solid #334155',
                borderRadius: 8,
                fontSize: 12,
              }}
              labelStyle={{ color: '#e2e8f0' }}
              formatter={(value, name) => {
                const n = typeof value === 'number' ? value : Number(value) || 0;
                return [
                  metric === 'tokens' ? formatTokens(n) : formatCurrency(n, 4),
                  String(name),
                ];
              }}
            />
            <Legend wrapperStyle={{ fontSize: 11 }} />
            {models.map((model, i) => (
              <Bar
                key={model}
                dataKey={model}
                stackId="usage"
                fill={MODEL_COLORS[i % MODEL_COLORS.length]}
              />
            ))}
          </BarChart>
        </ResponsiveContainer>
      )}
    </div>
  );
}
