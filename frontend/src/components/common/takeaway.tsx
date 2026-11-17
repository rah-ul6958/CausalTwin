"use client";

/** One plain-language sentence at the top of a page: what it shows, or what it means right now. */

import { motion, useReducedMotion } from "framer-motion";
import { CircleCheck, Info, TriangleAlert } from "lucide-react";
import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

const TONE = {
  info: { icon: Info, className: "border-causal/25 bg-causal/[0.06] text-cyan-50", iconClass: "text-causal" },
  good: { icon: CircleCheck, className: "border-good/30 bg-good/[0.06] text-emerald-50", iconClass: "text-good" },
  warn: { icon: TriangleAlert, className: "border-warn/35 bg-warn/[0.07] text-amber-50", iconClass: "text-warn" },
  risk: { icon: TriangleAlert, className: "border-risk/40 bg-risk/[0.08] text-rose-50", iconClass: "text-risk" },
};

export function Takeaway({ tone = "info", className, children }: { tone?: keyof typeof TONE; className?: string; children: ReactNode }) {
  const reduceMotion = useReducedMotion();
  const style = TONE[tone];
  return (
    <motion.p
      initial={reduceMotion ? false : { opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.35 }}
      className={cn("mb-5 flex items-start gap-2.5 rounded-xl border px-4 py-3 text-sm leading-relaxed", style.className, className)}
      data-testid="page-takeaway"
    >
      <style.icon className={cn("mt-0.5 size-4 shrink-0", style.iconClass)} aria-hidden />
      <span>{children}</span>
    </motion.p>
  );
}
