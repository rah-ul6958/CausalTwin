"use client";

/**
 * Guided demo: a floating panel that plays the backend's demo beats (/demo/start, /demo/next),
 * navigates to the relevant page, and spotlights the relevant part of the screen.
 */

import { AnimatePresence, motion } from "framer-motion";
import { ChevronRight, Clapperboard, Play, X } from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";
import { formatPercent } from "@/lib/format";
import { useLiveStore } from "@/lib/store";
import { useApi } from "@/lib/use-api";
import type { DemoBeatResult } from "@/lib/types";
import { cn } from "@/lib/utils";

/** Where each beat is best seen: page and the panel to spotlight (matches data-demo attributes). */
const BEAT_TARGETS: Record<string, { route: string; target: string }> = {
  normal_operation: { route: "/control", target: "risk-gauge" },
  risk_rises: { route: "/control", target: "risk-gauge" },
  recommendation: { route: "/advisor", target: "recommendation" },
  apply: { route: "/advisor", target: "verification" },
  risk_drops: { route: "/control", target: "risk-chart" },
  fault_injected: { route: "/control", target: "alert-feed" },
  drift_alert: { route: "/graph", target: "graph-canvas" },
  new_recommendation: { route: "/advisor", target: "recommendation" },
};

/**
 * The visible element with this data-demo attribute. Hidden routes stay mounted (React Activity),
 * so a hidden copy on another page may also match; it has no layout boxes and is skipped.
 */
function findVisibleTarget(target: string): Element | null {
  const matches = Array.from(document.querySelectorAll(`[data-demo="${target}"]`));
  return matches.find((element) => element.getClientRects().length > 0) ?? null;
}

/** Glowing ring drawn over the element with the matching data-demo attribute. */
function Spotlight({ target }: { target: string | null }) {
  const [rect, setRect] = useState<DOMRect | null>(null);
  useEffect(() => {
    if (!target) return;
    let scrolled = false;
    const measure = () => {
      const element = findVisibleTarget(target);
      // Bring the highlighted panel into view once (it may be below the fold).
      if (element && !scrolled) {
        scrolled = true;
        const smooth = !window.matchMedia("(prefers-reduced-motion: reduce)").matches;
        element.scrollIntoView({ behavior: smooth ? "smooth" : "auto", block: "center" });
      }
      setRect(element ? element.getBoundingClientRect() : null);
    };
    const timer = setInterval(measure, 300);
    const first = setTimeout(measure, 50);
    return () => {
      clearInterval(timer);
      clearTimeout(first);
    };
  }, [target]);
  if (!target || !rect) return null;
  return (
    <div
      aria-hidden
      className="pointer-events-none fixed z-40 rounded-2xl border-2 border-whatif shadow-[0_0_0_9999px_rgb(5_7_13/0.45),0_0_40px_6px_rgb(167_139_250/0.45)] transition-all duration-300"
      style={{ left: rect.left - 6, top: rect.top - 6, width: rect.width + 12, height: rect.height + 12 }}
    />
  );
}

export function DemoPanel({ open, onClose }: { open: boolean; onClose: () => void }) {
  const router = useRouter();
  const config = useApi(() => api.config(), "config");
  const setDemo = useLiveStore((state) => state.setDemo);
  const [result, setResult] = useState<DemoBeatResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [target, setTarget] = useState<string | null>(null);

  const play = async (action: "start" | "next") => {
    setBusy(true);
    setError(null);
    try {
      const beat = action === "start" ? await api.demoStart() : await api.demoNext();
      setResult(beat);
      setDemo(beat);
      const destination = beat.beat ? BEAT_TARGETS[beat.beat] : undefined;
      if (destination) {
        router.push(destination.route);
        setTarget(destination.target);
      }
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setBusy(false);
    }
  };

  const close = () => {
    setTarget(null);
    onClose();
  };

  const beats = config.data?.demo_beats ?? [];
  return (
    <>
      <Spotlight target={open ? target : null} />
      <AnimatePresence>
        {open && (
          <motion.aside
            initial={{ opacity: 0, y: 16 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: 16 }}
            transition={{ duration: 0.2 }}
            aria-label="Guided demo"
            className="glass fixed bottom-6 right-6 z-50 w-[380px] max-w-[calc(100vw-3rem)] p-4"
          >
            <div className="mb-3 flex items-center justify-between">
              <h2 className="flex items-center gap-2 font-heading text-sm font-semibold text-violet-200">
                <Clapperboard className="size-4" aria-hidden /> Guided demo
              </h2>
              <Button variant="ghost" size="icon-xs" onClick={close} aria-label="Close the guided demo">
                <X aria-hidden />
              </Button>
            </div>
            <ol className="mb-3 grid grid-cols-8 gap-1" aria-label="Demo progress">
              {beats.map((beat, index) => (
                <li
                  key={beat.name}
                  title={beat.title}
                  className={cn(
                    "h-1.5 rounded-full bg-white/10",
                    result && index <= result.position && "bg-whatif",
                  )}
                />
              ))}
            </ol>
            {result?.beat ? (
              <div className="space-y-2 text-sm" aria-live="polite">
                <p className="text-xs uppercase tracking-wider text-muted-foreground">
                  Beat {result.position + 1} of {result.total}
                </p>
                <p className="font-heading text-slate-100">{result.title}</p>
                <p className="max-h-40 overflow-y-auto text-xs leading-relaxed text-slate-300">{result.narration}</p>
                <p className="text-xs text-muted-foreground">
                  Twin risk {formatPercent(result.twin_risk?.probability)} - simulator ground truth{" "}
                  {formatPercent(result.validation?.true_risk_within_horizon)} (validation only)
                </p>
              </div>
            ) : (
              <p className="text-xs leading-relaxed text-slate-300">
                Plays the full story with a fixed seed: normal operation, risk rises, recommendation, apply, risk drops,
                a coolant filter clogs, drift is detected and the graph rewires, and a new recommendation. The live loop
                is paused between beats.
              </p>
            )}
            {error && <p className="mt-2 text-xs text-risk">{error}</p>}
            <div className="mt-4 flex justify-end gap-2">
              <Button size="sm" variant="outline" disabled={busy} onClick={() => void play("start")}>
                <Play aria-hidden /> {result ? "Restart" : "Start"}
              </Button>
              <Button
                size="sm"
                disabled={busy || !result || result.finished}
                onClick={() => void play("next")}
                className="bg-whatif text-slate-950 hover:bg-whatif/80"
              >
                {busy ? "Running..." : "Next beat"} <ChevronRight aria-hidden />
              </Button>
            </div>
          </motion.aside>
        )}
      </AnimatePresence>
    </>
  );
}
