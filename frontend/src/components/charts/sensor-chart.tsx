"use client";

/** One live sensor chart (rolling buffer from the store), memoized so it redraws only on new data. */

import { memo } from "react";
import { Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

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
  return (
    <div className="rounded-lg border border-line bg-white/[0.02] p-3">
      <div className="mb-1 flex items-baseline justify-between gap-2">
        <span className="min-w-0 truncate text-xs text-muted-foreground">{humanize(name)}</span>
        <span className="tabular shrink-0 whitespace-nowrap text-sm text-slate-100" title={unit ?? undefined}>
          {formatNumber(latest)} <span className="text-[10px] text-muted-foreground">{shortUnit(unit)}</span>
        </span>
      </div>
      <div className="h-20">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={data} margin={{ top: 2, right: 2, bottom: 0, left: 0 }}>
            <XAxis dataKey="step" hide />
            <YAxis domain={["auto", "auto"]} hide />
            <Tooltip
              contentStyle={{ background: "#0b1120", border: "1px solid rgb(148 163 184 / 0.2)", fontSize: 11 }}
              labelFormatter={(step) => `step ${step}`}
              formatter={(value) => [formatNumber(Number(value)), unit ?? ""]}
            />
            <Line type="monotone" dataKey="value" stroke={color} dot={false} strokeWidth={1.5} isAnimationActive={false} connectNulls={false} />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

export const SensorChart = memo(SensorChartBase);
