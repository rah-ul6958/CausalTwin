"use client";

/** Before/after forecast of one variable with its 90% uncertainty band (What-If Lab). */

import { memo, useMemo } from "react";
import { Area, ComposedChart, Line, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { formatNumber, formatSigned, humanize } from "@/lib/format";
import type { VariableForecast } from "@/lib/types";

function ForecastChartBase({ forecast, unit, secondsPerStep }: {
  forecast: VariableForecast;
  unit: string | null;
  secondsPerStep: number;
}) {
  const data = useMemo(
    () =>
      forecast.median.map((median, index) => ({
        seconds: (index + 1) * secondsPerStep,
        median,
        band: [forecast.lower[index], forecast.upper[index]] as [number, number],
        baseline: forecast.baseline_median[index],
      })),
    [forecast, secondsPerStep],
  );
  return (
    <div className="rounded-lg border border-line bg-white/[0.02] p-3">
      <div className="mb-1 flex items-baseline justify-between gap-2">
        <span className="text-xs text-muted-foreground">{humanize(forecast.variable)}</span>
        <span className="tabular text-xs text-violet-200">
          {formatSigned(forecast.change_at_end)} {unit} at {data.length * secondsPerStep} s
        </span>
      </div>
      <div className="h-28">
        <ResponsiveContainer width="100%" height="100%">
          <ComposedChart data={data} margin={{ top: 4, right: 4, bottom: 0, left: -18 }}>
            <XAxis dataKey="seconds" tick={{ fontSize: 9, fill: "#64748b" }} tickFormatter={(value) => `${value}s`} />
            <YAxis domain={["auto", "auto"]} tick={{ fontSize: 9, fill: "#64748b" }} width={52} tickFormatter={(value) => formatNumber(Number(value), 1)} />
            <Tooltip
              contentStyle={{ background: "#0b1120", border: "1px solid rgb(148 163 184 / 0.2)", fontSize: 11 }}
              formatter={(value, name) => {
                if (Array.isArray(value)) return [`${formatNumber(Number(value[0]))} - ${formatNumber(Number(value[1]))}`, "90% interval"];
                return [formatNumber(Number(value)), name === "median" ? "with change (median)" : "without change"];
              }}
              labelFormatter={(value) => `${value} s ahead`}
            />
            <Area dataKey="band" stroke="none" fill="#a78bfa" fillOpacity={0.18} isAnimationActive={false} />
            <Line dataKey="baseline" stroke="#94a3b8" strokeDasharray="4 3" dot={false} strokeWidth={1.2} isAnimationActive={false} />
            <Line dataKey="median" stroke="#a78bfa" dot={false} strokeWidth={1.8} isAnimationActive={false} />
          </ComposedChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

export const ForecastChart = memo(ForecastChartBase);
