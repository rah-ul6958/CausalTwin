"use client";

/**
 * Below the target risk there is nothing to explain: root-cause panels say so, and fold the current
 * contributions behind a button instead of presenting them as "causes of failure".
 */

import { ChevronDown } from "lucide-react";
import { useState, type ReactNode } from "react";

import { formatPercent } from "@/lib/format";
import { cn } from "@/lib/utils";

/** Whether a risk estimate is known and below the target. */
export function isBelowTarget(risk: number | null | undefined, target: number | null | undefined): boolean {
  return typeof risk === "number" && typeof target === "number" && risk < target;
}

export function BelowTargetFold({ risk, target, children }: { risk: number | null | undefined; target: number | null | undefined; children: ReactNode }) {
  const [open, setOpen] = useState(false);
  if (!isBelowTarget(risk, target)) return <>{children}</>;
  return (
    <div className="space-y-2 text-xs">
      <p data-testid="below-target-message" className="rounded-lg border border-good/25 bg-good/5 p-2.5 leading-relaxed text-emerald-100">
        Risk is below the target; nothing to explain right now. (Predicted {formatPercent(risk)}, target {formatPercent(target, 0)}.)
      </p>
      <button type="button" className="flex items-center gap-1 text-muted-foreground hover:text-slate-200" onClick={() => setOpen((value) => !value)} aria-expanded={open}>
        <ChevronDown className={cn("size-3.5 transition-transform", open && "rotate-180")} aria-hidden />
        Show what contributes most anyway
      </button>
      {open && <div className="opacity-80">{children}</div>}
    </div>
  );
}
