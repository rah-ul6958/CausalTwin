"use client";

/** One live sensor chart (rolling buffer from the store), memoized so it redraws only on new data. */

import { memo } from "react";
import { Area, ComposedChart, Line, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { formatNumber, humanize, shortUnit } from "@/lib/format";

export interface SeriesPoint {
  step: number;
  value: number | null;
}

interface SensorChartProps {
  name: string;
  unit: string | null;
  data: SeriesPoint[];
  color: string;
  latest: number | null;
}

function SensorChartBase({ name, unit, data, color, latest }: SensorChartProps) {
  const gradientId = `sensor-fill-${name}`;
  return (
    <div className="group relative overflow-hidden rounded-xl border border-line bg-gradient-to-b from-white/[0.035] to-transparent p-3 transition-colors hover:border-white/15">
      <span className="absolute inset-x-3 top-0 h-px opacity-70" style={{ background: `linear-gradient(90deg, transparent, ${color}, transparent)` }} aria-hidden />
      <div className="mb-1 flex items-baseline justify-between gap-2">
        <span className="flex min-w-0 items-center gap-1.5 truncate text-xs text-muted-foreground">
          <span className="status-light size-1.5 shrink-0" style={{ background: color }} aria-hidden />
          {humanize(name)}
        </span>
        <span className="tabular shrink-0 whitespace-nowrap text-sm text-slate-100" title={unit ?? undefined}>
          {formatNumber(latest)} <span className="text-[10px] text-muted-foreground">{shortUnit(unit)}</span>
        </span>
      </div>
      <div className="h-20">
        <ResponsiveContainer width="100%" height="100%">
          <ComposedChart data={data} margin={{ top: 2, right: 2, bottom: 0, left: 0 }}>
            <defs>
              <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor={color} stopOpacity={0.28} />
                <stop offset="100%" stopColor={color} stopOpacity={0} />
              </linearGradient>
            </defs>
            <XAxis dataKey="step" hide />
            <YAxis domain={["auto", "auto"]} hide />
            <Tooltip
              contentStyle={{ background: "#0b1120", border: "1px solid rgb(148 163 184 / 0.2)", fontSize: 11 }}
              labelFormatter={(step) => `step ${step}`}
              formatter={(value) => [formatNumber(Number(value)), unit ?? ""]}
            />
            <Area type="monotone" dataKey="value" stroke="none" fill={`url(#${gradientId})`} isAnimationActive={false} connectNulls={false} tooltipType="none" />
            <Line type="monotone" dataKey="value" stroke={color} dot={false} strokeWidth={1.6} isAnimationActive={false} connectNulls={false} />
          </ComposedChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

export const SensorChart = memo(SensorChartBase);
