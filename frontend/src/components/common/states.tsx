"use client";

/** Loading skeletons, friendly error states with retry, and empty states. */

import { RefreshCw, TriangleAlert } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";

/** Grey placeholder bars shown while a panel's data loads. */
export function PanelSkeleton({ rows = 4 }: { rows?: number }) {
  return (
    <div className="space-y-3" aria-busy="true" aria-label="Loading">
      {Array.from({ length: rows }, (_, index) => (
        <Skeleton key={index} className="shimmer h-6 w-full rounded-md bg-transparent" style={{ width: `${100 - (index % 3) * 12}%` }} />
      ))}
    </div>
  );
}

/** A clear error message with an optional Retry button (never a blank panel). */
export function ErrorState({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div role="alert" className="flex flex-col items-start gap-3 rounded-lg border border-risk/30 bg-risk/5 p-4 text-sm">
      <div className="flex items-start gap-2 text-rose-200">
        <TriangleAlert className="mt-0.5 size-4 shrink-0 text-risk" aria-hidden />
        <span>{message}</span>
      </div>
      {onRetry && (
        <Button size="sm" variant="outline" onClick={onRetry}>
          <RefreshCw aria-hidden /> Retry
        </Button>
      )}
    </div>
  );
}

/** The loading or failed side of an API call: an error with Retry if it failed, otherwise a skeleton. */
export function LoadingOrError({ state, rows = 4 }: { state: { error?: string; reload: () => void }; rows?: number }) {
  return state.error ? <ErrorState message={state.error} onRetry={state.reload} /> : <PanelSkeleton rows={rows} />;
}

/** A short "nothing here yet" message with an optional hint. */
export function EmptyState({ title, hint }: { title: string; hint?: string }) {
  return (
    <div className="flex flex-col items-center justify-center gap-1 py-8 text-center">
      <span className="mb-2 flex size-10 items-center justify-center rounded-full border border-dashed border-causal/30" aria-hidden>
        <span className="status-light size-1.5 bg-causal/70" />
      </span>
      <p className="text-sm text-slate-300">{title}</p>
      {hint && <p className="max-w-sm text-xs text-muted-foreground">{hint}</p>}
    </div>
  );
}
