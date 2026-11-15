/** Shown for an unknown address: a short message and links to the main pages. */

import Link from "next/link";

import { Button } from "@/components/ui/button";

export default function NotFound() {
  return (
    <div className="mx-auto mt-16 max-w-xl space-y-4">
      <h1 className="font-heading text-2xl text-slate-100">Page not found</h1>
      <p className="text-sm text-muted-foreground">There is no page at this address. The main views are below.</p>
      <div className="flex flex-wrap gap-2">
        <Button asChild>
          <Link href="/control">Control Room</Link>
        </Button>
        <Button variant="outline" asChild>
          <Link href="/advisor">Advisor</Link>
        </Button>
        <Button variant="outline" asChild>
          <Link href="/benchmark">Benchmark</Link>
        </Button>
      </div>
    </div>
  );
}
