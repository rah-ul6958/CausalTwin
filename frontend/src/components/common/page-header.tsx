/** Page title row with description and right-side badges or actions. */

import type { ReactNode } from "react";

export function PageHeader({ title, description, children }: { title: string; description?: string; children?: ReactNode }) {
  return (
    <div className="mb-5 flex flex-wrap items-end justify-between gap-3">
      <div>
        <h1 className="font-heading text-2xl font-semibold tracking-tight text-slate-50">{title}</h1>
        {description && <p className="mt-1 max-w-3xl text-sm text-muted-foreground">{description}</p>}
      </div>
      {children && <div className="flex flex-wrap items-center gap-2">{children}</div>}
    </div>
  );
}

/** Display colors per variable (presentation only). */
export const VARIABLE_COLORS: Record<string, string> = {
  ambient_temp: "#94a3b8",
  load_pct: "#22d3ee",
  spindle_speed: "#38bdf8",
  coolant_flow: "#2dd4bf",
  lube_rate: "#5eead4",
  motor_current: "#a78bfa",
  coolant_temp: "#818cf8",
  bearing_temp: "#fb7185",
  vibration: "#fbbf24",
  tool_wear: "#f97316",
};

export function variableColor(name: string): string {
  return VARIABLE_COLORS[name] ?? "#22d3ee";
}
