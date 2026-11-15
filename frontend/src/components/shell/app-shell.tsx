"use client";

/** Layout shell: sidebar navigation, top bar, live connection, and the guided demo panel. */

import {
  Clapperboard,
  Database,
  FlaskConical,
  Gauge,
  House,
  Lightbulb,
  Network,
  Pause,
  Play,
  Terminal,
  Trophy,
  User,
  Users,
} from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState, type ReactNode } from "react";

import { DemoPanel } from "@/components/shell/demo-panel";
import { LiveConnection } from "@/components/shell/live-connection";
import { DataSourceBadge } from "@/components/transparency/transparency";
import { Button } from "@/components/ui/button";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { api } from "@/lib/api";
import { humanize } from "@/lib/format";
import { useDataSource } from "@/lib/data-source";
import { useLiveStore, type ConnectionState } from "@/lib/store";
import { cn } from "@/lib/utils";

const NAVIGATION = [
  { href: "/", label: "Overview", icon: House },
  { href: "/control", label: "Control Room", icon: Gauge },
  { href: "/graph", label: "Causal Graph", icon: Network },
  { href: "/whatif", label: "What-If Lab", icon: FlaskConical },
  { href: "/advisor", label: "Advisor", icon: Lightbulb },
  { href: "/benchmark", label: "Benchmark", icon: Trophy },
  { href: "/console", label: "Console", icon: Terminal },
  { href: "/data", label: "Data", icon: Database },
];

function Sidebar() {
  const pathname = usePathname();
  return (
    <nav aria-label="Main" className="fixed inset-y-0 left-0 z-30 flex w-[72px] flex-col items-center gap-1 border-r border-line bg-[#060a14]/80 py-4 backdrop-blur-xl">
      <Link href="/" className="mb-4 flex size-10 items-center justify-center rounded-xl border border-causal/40 bg-causal/10 font-heading text-sm font-bold text-causal">
        CT
      </Link>
      {NAVIGATION.map(({ href, label, icon: Icon }) => {
        const active = href === "/" ? pathname === "/" : pathname.startsWith(href);
        return (
          <Tooltip key={href}>
            <TooltipTrigger asChild>
              <Link
                href={href}
                aria-label={label}
                aria-current={active ? "page" : undefined}
                className={cn(
                  "flex size-11 items-center justify-center rounded-xl text-slate-400 transition-all hover:bg-white/5 hover:text-slate-100 focus-visible:ring-2 focus-visible:ring-causal",
                  active && "bg-causal/10 text-causal shadow-[0_0_18px_-4px_var(--causal)]",
                )}
              >
                <Icon className="size-5" aria-hidden />
              </Link>
            </TooltipTrigger>
            <TooltipContent side="right">{label}</TooltipContent>
          </Tooltip>
        );
      })}
    </nav>
  );
}

/** Whether this tab has its own live simulation on the server or shares one (the reason is in the tooltip). */
function SessionBadge() {
  const session = useLiveStore((state) => state.status?.session);
  if (!session) return null;
  const Icon = session.private ? User : Users;
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span
          tabIndex={0}
          data-testid="session-badge"
          className={cn(
            "inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] font-medium",
            session.private ? "border-line text-slate-300" : "border-warn/40 bg-warn/10 text-amber-200",
          )}
        >
          <Icon className="size-3" aria-hidden /> {session.private ? "Private" : "Shared"}
          <span className="sr-only"> simulation</span>
        </span>
      </TooltipTrigger>
      <TooltipContent className="max-w-xs text-xs leading-relaxed">
        {session.note}
        {session.max_sessions > 0 &&
          ` (${session.private_sessions} of ${session.max_sessions} private simulations in use on this server.)`}
      </TooltipContent>
    </Tooltip>
  );
}

const CONNECTION_STYLE: Record<ConnectionState, { color: string; text: string }> = {
  open: { color: "bg-good", text: "Live" },
  connecting: { color: "bg-warn animate-pulse", text: "Connecting" },
  closed: { color: "bg-risk", text: "Offline - retrying" },
};

function TopBar({ onDemo }: { onDemo: () => void }) {
  const connection = useLiveStore((state) => state.connection);
  const status = useLiveStore((state) => state.status);
  const setStatus = useLiveStore((state) => state.setStatus);
  const style = CONNECTION_STYLE[connection];
  const source = useDataSource();

  const toggleRunning = async () => {
    if (!status) return;
    const response = await api.simControl(status.running ? "pause" : "resume");
    setStatus(response.status);
  };

  return (
    <header className="sticky top-0 z-20 flex h-14 items-center justify-between gap-4 border-b border-line bg-[#05070d]/70 px-6 backdrop-blur-xl">
      <div className="flex items-center gap-3">
        <span className="font-heading text-base font-semibold tracking-tight text-slate-100">CausalTwin</span>
        <DataSourceBadge />
        <SessionBadge />
      </div>
      <div className="flex items-center gap-3 text-xs">
        {status && (
          <span className="hidden items-center gap-3 text-muted-foreground md:flex">
            {source.has_simulator ? (
              <span>
                Scenario <span className="text-slate-200">{humanize(status.scenario)}</span>
              </span>
            ) : (
              <span className="max-w-56 truncate text-cyan-200" title={source.description}>
                {source.description}
              </span>
            )}
            <span>
              Step <span className="tabular text-slate-200" data-testid="sim-step">{status.step}</span>
            </span>
            {status.active_faults.length > 0 && (
              <span className="text-warn">Fault: {status.active_faults.map(humanize).join(", ")}</span>
            )}
          </span>
        )}
        <Button
          size="sm"
          variant="outline"
          onClick={() => void toggleRunning()}
          disabled={!status}
          aria-label={status?.running ? "Pause the live simulation" : "Resume the live simulation"}
        >
          {status?.running ? <Pause aria-hidden /> : <Play aria-hidden />}
          {status?.running ? "Pause" : "Resume"}
        </Button>
        <Button
          size="sm"
          variant="outline"
          onClick={onDemo}
          disabled={!source.has_simulator}
          title={source.has_simulator ? undefined : "The guided demo runs on the simulator; the live data is real."}
          className="border-whatif/40 text-violet-200"
        >
          <Clapperboard aria-hidden /> Guided demo
        </Button>
        <span className="flex items-center gap-2 rounded-full border border-line px-2.5 py-1" role="status" aria-live="polite">
          <span className={cn("size-2 rounded-full", style.color)} aria-hidden />
          <span className="text-slate-300">{style.text}</span>
        </span>
      </div>
    </header>
  );
}

export function AppShell({ children }: { children: ReactNode }) {
  const [demoOpen, setDemoOpen] = useState(false);
  return (
    <>
      <LiveConnection />
      <Sidebar />
      <div className="pl-[72px]">
        <TopBar onDemo={() => setDemoOpen((open) => !open)} />
        <main className="mx-auto w-full max-w-[1600px] px-6 py-6">{children}</main>
      </div>
      <DemoPanel open={demoOpen} onClose={() => setDemoOpen(false)} />
    </>
  );
}
