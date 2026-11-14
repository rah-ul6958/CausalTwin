"use client";

/** Glass panel: the standard surface for every block of the UI. */

import { motion, useReducedMotion } from "framer-motion";
import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

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
}

function trackPointer(event: React.MouseEvent<HTMLElement>) {
  const rect = event.currentTarget.getBoundingClientRect();
  event.currentTarget.style.setProperty("--mx", `${event.clientX - rect.left}px`);
  event.currentTarget.style.setProperty("--my", `${event.clientY - rect.top}px`);
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
}: PanelProps) {
  const reduceMotion = useReducedMotion();
  return (
    <motion.section
      initial={reduceMotion ? false : { opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.3, delay: reduceMotion ? 0 : index * 0.05, ease: "easeOut" }}
      onMouseMove={trackPointer}
      data-demo={demo}
      data-testid={testId}
      className={cn("glass flex min-w-0 flex-col", className)}
    >
      {(title || actions) && (
        <header className="flex items-start justify-between gap-3 border-b border-line px-4 py-3">
          <div className="flex min-w-0 items-center gap-2">
            {icon && <span className="text-causal">{icon}</span>}
            <div className="min-w-0">
              {title && <h2 className="truncate text-sm font-semibold text-slate-100">{title}</h2>}
              {subtitle && <p className="truncate text-xs text-muted-foreground">{subtitle}</p>}
            </div>
          </div>
          {actions && <div className="flex shrink-0 items-center gap-1.5">{actions}</div>}
        </header>
      )}
      <div className={cn("min-h-0 flex-1 p-4", bodyClassName)}>{children}</div>
    </motion.section>
  );
}
