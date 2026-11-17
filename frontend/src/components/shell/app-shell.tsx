"use client";

/** Layout shell: collapsible sidebar, command bar, live connection, and the guided demo panel. */

import { motion, useReducedMotion } from "framer-motion";
import {
  ChevronsLeft,
  ChevronsRight,
  Clapperboard,
  Database,
  FlaskConical,
  Gauge,
  House,
  Layers,
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
import { formatPercent, humanize } from "@/lib/format";
import { useDataSource } from "@/lib/data-source";
import { setSidebarExpanded, useSidebarExpanded } from "@/lib/sidebar";
import { useLiveStore, type ConnectionState } from "@/lib/store";
import { useApi } from "@/lib/use-api";
import { cn } from "@/lib/utils";
import { setViewMode, useViewMode } from "@/lib/view-mode";

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

function isActive(href: string, pathname: string): boolean {
  return href === "/" ? pathname === "/" : pathname.startsWith(href);
}

/** The CausalTwin mark: a spindle ring with three causal nodes. */
function BrandMark({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 32 32" className={className} aria-hidden>
      <defs>
        <linearGradient id="brand-gradient" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#7dd3fc" />
          <stop offset="1" stopColor="#a78bfa" />
        </linearGradient>
      </defs>
      <circle cx="16" cy="16" r="11" fill="none" stroke="url(#brand-gradient)" strokeWidth="1.6" strokeDasharray="3 2.2" />
      <circle cx="16" cy="16" r="4.2" fill="none" stroke="#22d3ee" strokeWidth="1.6" />
      <path d="M8 9 L13.5 13.6 M24 9 L18.5 13.6 M16 27 L16 20.4" stroke="url(#brand-gradient)" strokeWidth="1.4" strokeLinecap="round" />
      <circle cx="8" cy="9" r="1.8" fill="#7dd3fc" />
      <circle cx="24" cy="9" r="1.8" fill="#a78bfa" />
      <circle cx="16" cy="27" r="1.8" fill="#22d3ee" />
    </svg>
  );
}

/** Simple view folds technical panels away; Expert view shows everything. */
function ViewModeToggle() {
  const mode = useViewMode();
  return (
    <div className="hidden items-center rounded-full border border-line bg-white/[0.02] p-0.5 text-[11px] lg:flex" role="group" aria-label="View">
      {(["simple", "expert"] as const).map((option) => (
        <button
          key={option}
          type="button"
          aria-pressed={mode === option}
          onClick={() => setViewMode(option)}
          className={cn(
            "rounded-full px-2.5 py-0.5 transition-colors",
            mode === option ? "bg-causal/15 text-cyan-100" : "text-slate-400 hover:text-slate-200",
          )}
        >
          {option === "simple" ? "Simple" : "Expert"}
        </button>
      ))}
    </div>
  );
}

function Sidebar() {
  const pathname = usePathname();
  const expanded = useSidebarExpanded();
  const reduceMotion = useReducedMotion();
  const mode = useViewMode();
  return (
    <nav
      aria-label="Main"
      className={cn(
        "fixed inset-y-0 left-0 z-30 flex w-[72px] flex-col border-r border-line bg-[#04070d]/85 px-3 py-4 backdrop-blur-xl transition-[width] duration-300",
        expanded && "lg:w-[228px]",
      )}
    >
      <Link href="/" className="mb-6 flex h-10 items-center gap-2.5 rounded-xl px-1.5" aria-label="CausalTwin overview">
        <span className="flex size-9 shrink-0 items-center justify-center rounded-xl border border-causal/30 bg-causal/5 shadow-[0_0_24px_-8px_var(--causal)]">
          <BrandMark className="size-6" />
        </span>
        <span className={cn("hidden flex-col leading-none", expanded && "lg:flex")}>
          <span className="font-heading text-sm font-semibold tracking-tight text-slate-50">CausalTwin</span>
          <span className="hud-label mt-1 !text-[9px]">Failure intelligence</span>
        </span>
      </Link>
      <p className={cn("hud-label mb-2 hidden px-2", expanded && "lg:block")}>Workspace</p>
      <ul className="flex flex-col gap-1">
        {NAVIGATION.map(({ href, label, icon: Icon }) => {
          const active = isActive(href, pathname);
          const link = (
            <Link
              href={href}
              aria-label={label}
              aria-current={active ? "page" : undefined}
              className={cn(
                "group relative flex h-11 items-center gap-3 rounded-xl px-3 text-slate-400 transition-colors hover:bg-white/[0.04] hover:text-slate-100 focus-visible:ring-2 focus-visible:ring-causal",
                active && "text-cyan-100",
              )}
            >
              {active && (
                <motion.span
                  layoutId="nav-active"
                  transition={reduceMotion ? { duration: 0 } : { type: "spring", stiffness: 420, damping: 34 }}
                  className="absolute inset-0 rounded-xl border border-causal/25 bg-gradient-to-r from-causal/15 to-causal/[0.02] shadow-[0_0_22px_-8px_var(--causal)]"
                >
                  <span className="absolute top-2.5 bottom-2.5 left-0 w-[3px] rounded-full bg-causal shadow-[0_0_10px_var(--causal)]" />
                </motion.span>
              )}
              <Icon className="relative size-5 shrink-0 transition-transform duration-200 group-hover:scale-110" aria-hidden />
              <span className={cn("relative hidden truncate text-sm", expanded && "lg:inline")}>{label}</span>
            </Link>
          );
          return (
            <li key={href}>
              {expanded ? (
                <>
                  <span className="hidden lg:block">{link}</span>
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <span className="block lg:hidden">{link}</span>
                    </TooltipTrigger>
                    <TooltipContent side="right">{label}</TooltipContent>
                  </Tooltip>
                </>
              ) : (
                <Tooltip>
                  <TooltipTrigger asChild>{link}</TooltipTrigger>
                  <TooltipContent side="right">{label}</TooltipContent>
                </Tooltip>
              )}
            </li>
          );
        })}
      </ul>
      <Tooltip>
        <TooltipTrigger asChild>
          <button
            type="button"
            onClick={() => setViewMode(mode === "simple" ? "expert" : "simple")}
            className="mt-auto flex h-10 items-center gap-3 rounded-xl px-3 text-xs text-slate-400 transition-colors hover:bg-white/[0.04] hover:text-slate-100"
            aria-label={mode === "simple" ? "Switch to Expert view (all technical details)" : "Switch to Simple view"}
          >
            <Layers className={cn("size-5 shrink-0", mode === "expert" && "text-causal")} aria-hidden />
            <span className={cn("hidden", expanded && "lg:inline")}>{mode === "simple" ? "Simple view" : "Expert view"}</span>
          </button>
        </TooltipTrigger>
        <TooltipContent side="right">{mode === "simple" ? "Simple view: click for all technical details" : "Expert view: click for the simple view"}</TooltipContent>
      </Tooltip>
      <button
        type="button"
        onClick={() => setSidebarExpanded(!expanded)}
        className="hidden h-10 items-center gap-3 rounded-xl px-3 text-xs text-slate-400 transition-colors hover:bg-white/[0.04] hover:text-slate-100 lg:flex"
        aria-label={expanded ? "Collapse the sidebar" : "Expand the sidebar"}
        aria-expanded={expanded}
      >
        {expanded ? <ChevronsLeft className="size-5 shrink-0" aria-hidden /> : <ChevronsRight className="size-5 shrink-0" aria-hidden />}
        <span className={cn("hidden", expanded && "lg:inline")}>Collapse</span>
      </button>
    </nav>
  );
}

const CONNECTION_STYLE: Record<ConnectionState, { color: string; text: string; ring: string }> = {
  open: { color: "bg-good", text: "Live", ring: "border-good/30 bg-good/5" },
  connecting: { color: "bg-warn", text: "Connecting", ring: "border-warn/30 bg-warn/5" },
  closed: { color: "bg-risk", text: "Offline - retrying", ring: "border-risk/30 bg-risk/5" },
};

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

/** The twin's latest risk estimate, colored against the optimizer's target risk (live data only). */
function RiskReadout() {
  const latestRisk = useLiveStore((state) => state.latestRisk);
  const config = useApi(() => api.config(), "config");
  const probability = latestRisk?.twin_risk?.probability;
  const target = config.data?.optimizer.target_risk;
  if (probability === undefined || target === undefined) return null;
  const tone = probability >= target ? "text-rose-300" : probability >= target / 2 ? "text-amber-200" : "text-emerald-200";
  return (
    <span className="hidden items-center gap-1.5 xl:flex">
      <span className="hud-label">Twin risk</span>
      <span className={cn("tabular text-xs", tone)}>{formatPercent(probability)}</span>
    </span>
  );
}

function TopBar({ onDemo }: { onDemo: () => void }) {
  const pathname = usePathname();
  const connection = useLiveStore((state) => state.connection);
  const status = useLiveStore((state) => state.status);
  const setStatus = useLiveStore((state) => state.setStatus);
  const style = CONNECTION_STYLE[connection];
  const source = useDataSource();
  const page = NAVIGATION.find((item) => isActive(item.href, pathname));

  const toggleRunning = async () => {
    if (!status) return;
    const response = await api.simControl(status.running ? "pause" : "resume");
    setStatus(response.status);
  };

  return (
    <header className="sticky top-0 z-20 flex h-14 items-center justify-between gap-4 border-b border-line bg-[#03050a]/75 px-6 backdrop-blur-xl">
      <div className="flex min-w-0 items-center gap-3">
        <span className="hidden min-w-0 items-center gap-2 text-sm sm:flex">
          <span className="font-heading font-semibold tracking-tight text-slate-100">CausalTwin</span>
          {page && page.href !== "/" && (
            <>
              <span className="text-slate-600" aria-hidden>
                /
              </span>
              <span className="truncate text-slate-400">{page.label}</span>
            </>
          )}
        </span>
        <DataSourceBadge />
        <SessionBadge />
        <ViewModeToggle />
      </div>
      <div className="flex items-center gap-3 text-xs">
        {status && (
          <span className="hidden items-center gap-4 text-muted-foreground md:flex">
            {source.has_simulator ? (
              <span className="hidden items-center gap-1.5 lg:flex">
                <span className="hud-label">Scenario</span>
                <span className="text-slate-200">{humanize(status.scenario)}</span>
              </span>
            ) : (
              <span className="max-w-56 truncate text-cyan-200" title={source.description}>
                {source.description}
              </span>
            )}
            <span className="flex items-center gap-1.5">
              <span className="hud-label">Step</span>
              <span className="tabular text-slate-100" data-testid="sim-step">
                {status.step}
              </span>
            </span>
            <RiskReadout />
            {status.active_faults.length > 0 && (
              <span className="flex items-center gap-1.5 rounded-full border border-warn/40 bg-warn/10 px-2 py-0.5 text-warn">
                <span className="status-light size-1.5 bg-warn" aria-hidden />
                Fault: {status.active_faults.map(humanize).join(", ")}
              </span>
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
          className="border-whatif/40 bg-whatif/5 text-violet-200 hover:bg-whatif/15 hover:shadow-[0_0_20px_-6px_var(--whatif)]"
        >
          <Clapperboard aria-hidden /> Guided demo
        </Button>
        <span className={cn("flex items-center gap-2 rounded-full border px-2.5 py-1", style.ring)} role="status" aria-live="polite">
          <span className={cn("size-2 rounded-full", style.color, connection !== "closed" && "status-light")} aria-hidden />
          <span className="text-slate-200">{style.text}</span>
        </span>
      </div>
    </header>
  );
}

export function AppShell({ children }: { children: ReactNode }) {
  const [demoOpen, setDemoOpen] = useState(false);
  const expanded = useSidebarExpanded();
  return (
    <>
      <LiveConnection />
      <Sidebar />
      <div className={cn("pl-[72px] transition-[padding] duration-300", expanded && "lg:pl-[228px]")}>
        <TopBar onDemo={() => setDemoOpen((open) => !open)} />
        <main className="mx-auto w-full max-w-[1600px] px-6 py-6">{children}</main>
      </div>
      <DemoPanel open={demoOpen} onClose={() => setDemoOpen(false)} />
    </>
  );
}
