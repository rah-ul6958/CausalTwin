"use client";

/**
 * Error boundary for every page: if a page crashes while rendering, the sidebar and top bar
 * stay usable and this panel explains what happened, with Try again and a way home.
 */

import Link from "next/link";

import { ErrorState } from "@/components/common/states";
import { Button } from "@/components/ui/button";

export default function PageError({ error, retry }: { error: unknown; retry: () => void }) {
  const message = error instanceof Error && error.message ? error.message : "Unknown error";
  return (
    <div className="mx-auto mt-16 max-w-xl space-y-4">
      <h1 className="font-heading text-2xl text-slate-100">This page hit an error</h1>
      <ErrorState message={`Something went wrong while showing this page: ${message}`} onRetry={retry} />
      <p className="text-sm text-muted-foreground">
        The live connection and the other pages are unaffected. If it happens again, check the backend in the System
        Console.
      </p>
      <Button variant="outline" asChild>
        <Link href="/">Back to the start page</Link>
      </Button>
    </div>
  );
}
