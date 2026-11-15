"use client";

/** Page title row with an eyebrow label, description, and right-side badges or actions. */

import { motion, useReducedMotion } from "framer-motion";
import type { ReactNode } from "react";

export function PageHeader({
  title,
  description,
  eyebrow,
  children,
}: {
  title: string;
  description?: string;
  /** Small technical label above the title (for example "Module 02"). */
  eyebrow?: string;
  children?: ReactNode;
}) {
  const reduceMotion = useReducedMotion();
  return (
    <div className="mb-6 flex flex-wrap items-end justify-between gap-3">
      <motion.div
        initial={reduceMotion ? false : { opacity: 0, y: 8, filter: "blur(4px)" }}
        animate={{ opacity: 1, y: 0, filter: "blur(0px)" }}
        transition={{ duration: 0.45, ease: [0.2, 0.8, 0.2, 1] }}
      >
        <p className="hud-label mb-1.5 flex items-center gap-2 !text-cyan-300/80">
          <span className="h-px w-6 bg-gradient-to-r from-causal to-transparent" aria-hidden />
          {eyebrow ?? "CausalTwin"}
        </p>
        <h1 className="font-heading text-[1.7rem] font-semibold tracking-tight text-slate-50">{title}</h1>
        {description && <p className="mt-1 max-w-3xl text-sm text-muted-foreground">{description}</p>}
      </motion.div>
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
