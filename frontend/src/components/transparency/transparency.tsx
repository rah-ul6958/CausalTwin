"use client";

/**
 * Transparency components reused everywhere:
 * HowComputed (provenance drawer), ConfidenceBar, SimulatedBadge, GroundTruthBadge, TimingChip, InfoTip.
 */

import { FlaskConical, Info, Radio, ShieldCheck, Timer } from "lucide-react";
import type { ReactNode } from "react";

import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle, SheetTrigger } from "@/components/ui/sheet";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { useDataSource } from "@/lib/data-source";
import { formatMs, humanize } from "@/lib/format";
import type { Provenance } from "@/lib/types";
import { cn } from "@/lib/utils";

export function InfoTip({ children, label = "More information" }: { children: ReactNode; label?: string }) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <button type="button" aria-label={label} className="inline-flex text-muted-foreground hover:text-causal">
          <Info className="size-3.5" aria-hidden />
        </button>
      </TooltipTrigger>
      <TooltipContent className="max-w-xs text-xs leading-relaxed">{children}</TooltipContent>
    </Tooltip>
  );
}

function formatParameter(value: unknown): string {
  if (typeof value === "number") return Number.isInteger(value) ? String(value) : value.toPrecision(4);
  if (typeof value === "string") return value;
  return JSON.stringify(value);
}

/** Info button that opens the "How was this computed?" drawer for a provenance block. */
export function HowComputed({ provenance, title }: { provenance: Provenance | null | undefined; title?: string }) {
  if (!provenance) return null;
  return (
    <Sheet>
      <SheetTrigger asChild>
        <Button variant="ghost" size="icon-xs" aria-label="How was this computed?" title="How was this computed?">
          <Info aria-hidden />
        </Button>
      </SheetTrigger>
      <SheetContent className="w-[420px] max-w-[92vw] overflow-y-auto border-line bg-[#080c16]/95 backdrop-blur-xl sm:max-w-md">
        <SheetHeader>
          <SheetTitle className="font-heading">How was this computed?</SheetTitle>
          <SheetDescription>{title ?? "Provenance reported by the backend for this result."}</SheetDescription>
        </SheetHeader>
        <div className="space-y-5 px-4 pb-6 text-sm">
          <div className="flex flex-wrap gap-2">
            {provenance.data_source === "simulated" ? <SimulatedBadge /> : <UploadedBadge />}
            {provenance.ground_truth_used && <GroundTruthBadge />}
            {!provenance.ground_truth_available && (
              <span className="rounded-full border border-warn/40 px-2 py-0.5 text-xs text-warn">No ground truth available</span>
            )}
            {provenance.computation_ms !== null && <TimingChip ms={provenance.computation_ms} />}
          </div>
          <section>
            <h3 className="mb-1 text-xs uppercase tracking-wider text-muted-foreground">Method</h3>
            <p className="leading-relaxed text-slate-200">{provenance.method}</p>
          </section>
          <section>
            <h3 className="mb-1 text-xs uppercase tracking-wider text-muted-foreground">Data window</h3>
            <p className="text-slate-200">{provenance.data_window}</p>
          </section>
          {Object.keys(provenance.parameters).length > 0 && (
            <section>
              <h3 className="mb-1 text-xs uppercase tracking-wider text-muted-foreground">Parameters</h3>
              <dl className="grid grid-cols-2 gap-x-4 gap-y-1 font-mono text-xs">
                {Object.entries(provenance.parameters).map(([key, value]) => (
                  <div key={key} className="contents">
                    <dt className="text-muted-foreground">{humanize(key)}</dt>
                    <dd className="text-right text-slate-200">{formatParameter(value)}</dd>
                  </div>
                ))}
              </dl>
            </section>
          )}
          {provenance.assumptions.length > 0 && (
            <section>
              <h3 className="mb-1 text-xs uppercase tracking-wider text-muted-foreground">Assumptions</h3>
              <ul className="list-disc space-y-1 pl-4 text-slate-300">
                {provenance.assumptions.map((assumption) => (
                  <li key={assumption}>{assumption}</li>
                ))}
              </ul>
            </section>
          )}
        </div>
      </SheetContent>
    </Sheet>
  );
}

/** A 0..1 bar with its numeric value and an explanatory tooltip. */
export function ConfidenceBar({
  value,
  label,
  explanation,
  className,
}: {
  value: number | null | undefined;
  label?: string;
  explanation?: string;
  className?: string;
}) {
  const safe = value ?? 0;
  const color = safe >= 0.9 ? "bg-good" : safe >= 0.6 ? "bg-causal" : "bg-warn";
  const bar = (
    <div className={cn("flex items-center gap-2", className)}>
      {label && <span className="w-28 shrink-0 text-xs text-muted-foreground">{label}</span>}
      <div
        className="h-1.5 flex-1 overflow-hidden rounded-full bg-white/10"
        role="meter"
        aria-label={label ?? "Confidence"}
        aria-valuemin={0}
        aria-valuemax={1}
        aria-valuenow={value ?? undefined}
      >
        <div className={cn("h-full rounded-full transition-all duration-500", color)} style={{ width: `${safe * 100}%` }} />
      </div>
      <span className="tabular w-10 text-right text-xs text-slate-200">{value === null || value === undefined ? "-" : safe.toFixed(2)}</span>
    </div>
  );
  if (!explanation) return bar;
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <div tabIndex={0}>{bar}</div>
      </TooltipTrigger>
      <TooltipContent className="max-w-xs text-xs leading-relaxed">{explanation}</TooltipContent>
    </Tooltip>
  );
}

/**
 * The live data's label: "Simulated" for simulated data; otherwise a "Live data" badge naming the
 * source and reminding that no ground truth exists, so results are hypotheses to confirm.
 */
export function DataSourceBadge({ className }: { className?: string }) {
  const source = useDataSource();
  if (source.simulated) return <SimulatedBadge className={className} />;
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span
          tabIndex={0}
          className={cn(
            "inline-flex items-center gap-1 rounded-full border border-causal/40 bg-causal/10 px-2 py-0.5 text-[11px] font-medium text-cyan-200",
            className,
          )}
        >
          <Radio className="size-3" aria-hidden /> Live data
        </span>
      </TooltipTrigger>
      <TooltipContent className="max-w-xs text-xs">
        {source.description}. No ground truth exists for this data: CausalTwin&apos;s results are hypotheses to confirm
        with the machine&apos;s engineers, and recommendations are advisory.
      </TooltipContent>
    </Tooltip>
  );
}

export function SimulatedBadge({ className }: { className?: string }) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span
          tabIndex={0}
          className={cn(
            "inline-flex items-center gap-1 rounded-full border border-whatif/40 bg-whatif/10 px-2 py-0.5 text-[11px] font-medium text-violet-200",
            className,
          )}
        >
          <FlaskConical className="size-3" aria-hidden /> Simulated
        </span>
      </TooltipTrigger>
      <TooltipContent className="max-w-xs text-xs">
        This data comes from the CausalTwin spindle simulator, not a real machine. Physical constants are illustrative.
      </TooltipContent>
    </Tooltip>
  );
}

function UploadedBadge() {
  return (
    <span className="inline-flex items-center gap-1 rounded-full border border-causal/40 bg-causal/10 px-2 py-0.5 text-[11px] text-cyan-200">
      Uploaded data
    </span>
  );
}

export function GroundTruthBadge({ className }: { className?: string }) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span
          tabIndex={0}
          className={cn(
            "inline-flex items-center gap-1 rounded-full border border-good/40 bg-good/10 px-2 py-0.5 text-[11px] font-medium text-emerald-200",
            className,
          )}
        >
          <ShieldCheck className="size-3" aria-hidden /> Simulator ground truth
        </span>
      </TooltipTrigger>
      <TooltipContent className="max-w-xs text-xs leading-relaxed">
        Known only because we built the simulator. The twin never sees it; it is shown to check the twin&apos;s
        estimates. Real machines have no ground truth.
      </TooltipContent>
    </Tooltip>
  );
}

export function TimingChip({ ms, label }: { ms: number | null | undefined; label?: string }) {
  if (ms === null || ms === undefined) return null;
  return (
    <span className="inline-flex items-center gap-1 rounded-full border border-line bg-white/5 px-2 py-0.5 font-mono text-[11px] text-slate-300">
      <Timer className="size-3" aria-hidden /> {label ? `${label} ` : ""}
      {formatMs(ms)}
    </span>
  );
}
