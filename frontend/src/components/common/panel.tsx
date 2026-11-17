"use client";

/** Glass panel: the standard surface for every block of the UI. */

import { motion, useReducedMotion } from "framer-motion";
import { ChevronDown } from "lucide-react";
import { useState, type ReactNode } from "react";

import { cn } from "@/lib/utils";
import { useViewMode } from "@/lib/view-mode";

interface PanelProps {
  title?: ReactNode;
  subtitle?: ReactNode;
  icon?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
  bodyClassName?: string;
  /** Position in a stagger sequence (fade and rise in). */
  index?: number;
  /** Target name for the guided demo spotlight. */
  demo?: string;
  testId?: string;
  /** Technical detail: folded away behind "Show details" in Simple view. */
  expert?: boolean;
}

export function Panel({
  title,
  subtitle,
  icon,
  actions,
  children,
  className,
  bodyClassName,
  index = 0,
  demo,
  testId,
  expert = false,
}: PanelProps) {
  const reduceMotion = useReducedMotion();
  const mode = useViewMode();
  const [opened, setOpened] = useState(false);
  const folded = expert && mode === "simple" && !opened;
  return (
    <motion.section
      initial={reduceMotion ? false : { opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.3, delay: reduceMotion ? 0 : index * 0.05, ease: "easeOut" }}
      data-demo={demo}
      data-testid={testId}
      className={cn("glass flex min-w-0 flex-col", className)}
    >
      {(title || actions) && (
        <header className="relative flex items-start justify-between gap-3 border-b border-line px-4 py-3">
          <span className="pointer-events-none absolute inset-x-4 -bottom-px h-px bg-gradient-to-r from-transparent via-causal/25 to-transparent" aria-hidden />
          <div className="flex min-w-0 items-center gap-2.5">
            {icon && (
              <span className="flex size-7 shrink-0 items-center justify-center rounded-lg border border-causal/20 bg-causal/[0.07] text-causal shadow-[0_0_16px_-6px_var(--causal)]">
                {icon}
              </span>
            )}
            <div className="min-w-0">
              {title && <h2 className="truncate text-sm font-semibold text-slate-100">{title}</h2>}
              {subtitle && <p className="truncate text-xs text-muted-foreground">{subtitle}</p>}
            </div>
          </div>
          {actions && <div className="flex shrink-0 items-center gap-1.5">{actions}</div>}
        </header>
      )}
      {folded ? (
        <div className="px-4 py-2.5">
          <button
            type="button"
            onClick={() => setOpened(true)}
            className="flex items-center gap-1.5 text-xs text-slate-400 transition-colors hover:text-cyan-200"
          >
            <ChevronDown className="size-3.5" aria-hidden /> Show details
          </button>
        </div>
      ) : (
        <div className={cn("min-h-0 flex-1 p-4", bodyClassName)}>{children}</div>
      )}
    </motion.section>
  );
}
