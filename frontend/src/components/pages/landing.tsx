"use client";

/** Landing page: the pitch, an animated correlation-to-causation illustration, live benchmark numbers. */

import { motion, useReducedMotion } from "framer-motion";
import { ArrowRight, Database, FlaskConical, Lightbulb, Network, ShieldCheck } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";

import { CountUp } from "@/components/common/count-up";
import { Panel } from "@/components/common/panel";
import { PanelSkeleton } from "@/components/common/states";
import { GroundTruthBadge, SimulatedBadge } from "@/components/transparency/transparency";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";
import { formatPercent } from "@/lib/format";
import { useApi } from "@/lib/use-api";

const NODES = [
  { id: "load", label: "Load", x: 60, y: 60 },
  { id: "speed", label: "Speed", x: 60, y: 190 },
  { id: "wear", label: "Tool wear", x: 230, y: 40 },
  { id: "bearing", label: "Bearing temp", x: 230, y: 200 },
  { id: "vibration", label: "Vibration", x: 400, y: 70 },
  { id: "failure", label: "Failure", x: 430, y: 190 },
];
const POSITION = Object.fromEntries(NODES.map((node) => [node.id, node]));
const CORRELATIONS = [
  ["load", "vibration"], ["speed", "vibration"], ["vibration", "failure"], ["wear", "vibration"], ["bearing", "vibration"],
  ["load", "failure"], ["speed", "failure"], ["wear", "failure"], ["bearing", "failure"], ["load", "bearing"],
];
const CAUSES = [["load", "bearing"], ["speed", "bearing"], ["load", "vibration"], ["speed", "vibration"], ["wear", "vibration"], ["wear", "failure"], ["bearing", "failure"]];

function TrapIllustration() {
  const reduceMotion = useReducedMotion();
  const [causal, setCausal] = useState(false);
  useEffect(() => {
    if (reduceMotion) return;
    const timer = setInterval(() => setCausal((value) => !value), 3200);
    return () => clearInterval(timer);
  }, [reduceMotion]);
  const showCausal = reduceMotion ? true : causal;
  return (
    <figure className="relative">
      <svg viewBox="0 0 500 250" className="w-full" role="img" aria-label="Illustration: correlations dissolve into causal arrows; vibration has no arrow to failure">
        <defs>
          <marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
            <path d="M 0 0 L 10 5 L 0 10 z" fill="#22d3ee" />
          </marker>
        </defs>
        {CORRELATIONS.map(([from, to]) => (
          <motion.line
            key={`c-${from}-${to}`}
            x1={POSITION[from].x}
            y1={POSITION[from].y}
            x2={POSITION[to].x}
            y2={POSITION[to].y}
            stroke="#fbbf24"
            strokeDasharray="4 5"
            strokeWidth={from === "vibration" || to === "vibration" ? 2.2 : 1.2}
            animate={{ opacity: showCausal ? 0 : 0.6 }}
            transition={{ duration: 0.8 }}
          />
        ))}
        {CAUSES.map(([from, to]) => (
          <motion.line
            key={`k-${from}-${to}`}
            x1={POSITION[from].x}
            y1={POSITION[from].y}
            x2={POSITION[to].x - (POSITION[to].x - POSITION[from].x) * 0.18}
            y2={POSITION[to].y - (POSITION[to].y - POSITION[from].y) * 0.18}
            stroke="#22d3ee"
            strokeWidth={2}
            markerEnd="url(#arrow)"
            animate={{ opacity: showCausal ? 0.95 : 0 }}
            transition={{ duration: 0.8 }}
          />
        ))}
        {NODES.map((node) => (
          <g key={node.id}>
            <circle cx={node.x} cy={node.y} r={20} fill="#0b1222" stroke={node.id === "failure" ? "#f43f5e" : node.id === "vibration" ? "#fbbf24" : "#334155"} strokeWidth={1.5} />
            {/* A dark stroke drawn under the fill keeps labels readable where lines cross them. */}
            <text
              x={node.x}
              y={node.y + 36}
              textAnchor="middle"
              fontSize={12}
              fill="#cbd5e1"
              stroke="#05070d"
              strokeWidth={4}
              strokeLinejoin="round"
              paintOrder="stroke"
            >
              {node.label}
            </text>
          </g>
        ))}
      </svg>
      <figcaption className="mt-1 text-center text-xs text-muted-foreground">
        Illustration. <span className="text-amber-300">{showCausal ? "Causal view" : "Correlation view"}</span>: vibration moves with failure, but it
        has no causal arrow to it.
      </figcaption>
    </figure>
  );
}

const STEPS = [
  { title: "Ingest", text: "Sensor time series and the event log, aligned and quality-checked.", icon: Database },
  { title: "Discover", text: "PCMCI finds cause and effect with time lags, bootstrapped for confidence.", icon: Network },
  { title: "Simulate", text: "What-if rollouts of the fitted causal model under interventions.", icon: FlaskConical },
  { title: "Recommend", text: "The cheapest change that brings failure risk below target.", icon: Lightbulb },
  { title: "Verify", text: "Every claim checked against the simulator's ground truth.", icon: ShieldCheck },
];

function KeyNumbers() {
  const benchmark = useApi(() => api.benchmark(), "benchmark");
  const drift = useApi(() => api.driftResults(), "drift");
  const config = useApi(() => api.config(), "config");
  if (!benchmark.data) {
    return benchmark.error ? <p className="text-sm text-muted-foreground">Benchmark results are not available yet: {benchmark.error}</p> : <PanelSkeleton rows={3} />;
  }
  const summary = benchmark.data.summary;
  const vibration = summary.baseline.blamed.vibration ?? 0;
  const delays = (drift.data?.faults ?? []).map((run) => run.detection_delay_steps).filter((value): value is number => typeof value === "number");
  const medianDelay = delays.length ? [...delays].sort((first, second) => first - second)[Math.floor(delays.length / 2)] : null;
  const secondsPerStep = config.data?.seconds_per_step ?? null;
  const targetRisk = formatPercent(benchmark.data.settings.target_risk, 0);
  const items = [
    {
      value: <CountUp value={summary.causaltwin.success_rate} format={(value) => formatPercent(value, 0)} />,
      label: `of ${benchmark.data.n_cases} simulated high-risk cases brought to ${targetRisk} true risk or less by CausalTwin, versus ${formatPercent(summary.baseline.success_rate, 0)} for XGBoost + SHAP`,
    },
    {
      value: (
        <>
          <CountUp value={vibration} format={(value) => Math.round(value).toString()} /> / {benchmark.data.n_cases}
        </>
      ),
      label: "high-risk cases where standard ML blamed vibration, which has no causal effect on failure",
    },
    {
      value: medianDelay === null ? <>-</> : secondsPerStep === null ? <>{medianDelay} steps</> : <>{Math.round(medianDelay * secondsPerStep)} s</>,
      label: "median simulated time to detect an injected physical fault (clogged filter, lube leak, bearing damage)",
    },
  ];
  return (
    <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
      {items.map((item, index) => (
        <Panel key={index} index={index + 2}>
          <p className="tabular text-4xl text-cyan-100">{item.value}</p>
          <p className="mt-2 text-xs leading-relaxed text-muted-foreground">{item.label}</p>
        </Panel>
      ))}
    </div>
  );
}

export function Landing() {
  return (
    <div className="space-y-8">
      <section className="grid grid-cols-1 items-center gap-8 pt-4 lg:grid-cols-2">
        <motion.div initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.4 }}>
          <div className="mb-4 flex gap-2">
            <SimulatedBadge />
            <GroundTruthBadge />
          </div>
          <h1 className="font-heading text-5xl font-semibold leading-tight tracking-tight text-slate-50">
            AI that finds <span className="text-causal">why</span> a machine is failing.
          </h1>
          <p className="mt-4 max-w-xl text-base leading-relaxed text-slate-300">
            Most predictive maintenance says &quot;failure is likely&quot;. CausalTwin finds the cause, the lever that actually prevents it,
            and how much to change it. Here it runs on a simulated spindle whose true causes are known, so every answer is checked against
            that ground truth.
          </p>
          <div className="mt-6 flex gap-3">
            <Button size="lg" asChild className="h-11 px-5 text-sm shadow-[0_0_28px_-6px_var(--causal)]">
              <Link href="/control">
                Launch Twin <ArrowRight aria-hidden />
              </Link>
            </Button>
            <Button size="lg" variant="outline" asChild className="h-11 px-5 text-sm">
              <Link href="/benchmark">See the benchmark</Link>
            </Button>
          </div>
        </motion.div>
        <Panel index={1}>
          <TrapIllustration />
        </Panel>
      </section>
      <KeyNumbers />
      <section>
        <h2 className="mb-3 font-heading text-lg text-slate-100">How it works</h2>
        <ol className="grid grid-cols-1 gap-3 md:grid-cols-5">
          {STEPS.map((step, index) => (
            <li key={step.title}>
              <Panel index={index + 5} className="h-full">
                <step.icon className="size-5 text-causal" aria-hidden />
                <p className="mt-2 font-heading text-sm text-slate-100">
                  {index + 1}. {step.title}
                </p>
                <p className="mt-1 text-xs leading-relaxed text-muted-foreground">{step.text}</p>
              </Panel>
            </li>
          ))}
        </ol>
      </section>
    </div>
  );
}
