"use client";

/**
 * Landing page: the pitch, a live view of the simulated spindle, benchmark numbers read from the
 * backend, the pipeline from sensor data to the smallest safe action, and the Correlation Trap.
 */

import { motion, useReducedMotion, useScroll, useSpring, useTransform } from "framer-motion";
import { Activity, ArrowRight, Boxes, Cpu, FlaskConical, Network, ShieldCheck, Sparkles } from "lucide-react";
import Link from "next/link";
import { useEffect, useRef, useState, type ReactNode } from "react";

import { riskLevel, SpindleTwin } from "@/components/charts/spindle-twin";
import { CountUp } from "@/components/common/count-up";
import { Panel } from "@/components/common/panel";
import { PanelSkeleton } from "@/components/common/states";
import { GroundTruthBadge, SimulatedBadge } from "@/components/transparency/transparency";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";
import { formatPercent, humanize } from "@/lib/format";
import { useLiveStore } from "@/lib/store";
import type { BaselineMethod } from "@/lib/types";
import { useApi } from "@/lib/use-api";
import { cn } from "@/lib/utils";

const EASE = [0.2, 0.8, 0.2, 1] as const;

/* ---------------------------------------------------------------- Correlation Trap illustration */

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
          <filter id="trap-glow" x="-50%" y="-50%" width="200%" height="200%">
            <feGaussianBlur stdDeviation="2.5" result="blur" />
            <feMerge>
              <feMergeNode in="blur" />
              <feMergeNode in="SourceGraphic" />
            </feMerge>
          </filter>
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
            filter="url(#trap-glow)"
            animate={{ opacity: showCausal ? 0.95 : 0 }}
            transition={{ duration: 0.8 }}
          />
        ))}
        {NODES.map((node) => (
          <g key={node.id}>
            <circle
              cx={node.x}
              cy={node.y}
              r={20}
              fill="#0b1222"
              stroke={node.id === "failure" ? "#f43f5e" : node.id === "vibration" ? "#fbbf24" : "#334155"}
              strokeWidth={1.5}
              filter={node.id === "failure" ? "url(#trap-glow)" : undefined}
            />
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

/* ------------------------------------------------------------------------------------- Hero */

const HEADLINE = [
  ["Don't", "Just", "Predict", "Failure."],
  ["Understand", "Why", "It", "Happens."],
];

function Headline() {
  const reduceMotion = useReducedMotion();

  return (
    <h1 className="font-heading text-[2.6rem] leading-[1.05] font-semibold tracking-tight sm:text-5xl xl:text-[3.6rem]">
      {HEADLINE.map((line, lineIndex) => (
        <span key={lineIndex} className={cn("block", lineIndex === 1 ? "text-gradient pb-1" : "text-slate-50")}>
          {line.map((word, wordIndex) => {
            const delay = 0.15 + (lineIndex * line.length + wordIndex) * 0.06;
            return (
              <motion.span
                key={word}
                className="mr-[0.24em] inline-block"
                initial={reduceMotion ? false : { opacity: 0, y: 18, filter: "blur(6px)" }}
                animate={{ opacity: 1, y: 0, filter: "blur(0px)" }}
                transition={{ duration: 0.55, delay, ease: EASE }}
              >
                {word}
              </motion.span>
            );
          })}
        </span>
      ))}
    </h1>
  );
}

/** A small floating data label beside the hero visual (live values or "--"). */
function DataChip({ label, value, className, delay }: { label: string; value: string; className: string; delay: number }) {
  const reduceMotion = useReducedMotion();
  return (
    <motion.div
      initial={reduceMotion ? false : { opacity: 0, scale: 0.9 }}
      animate={{ opacity: 1, scale: 1 }}
      transition={{ duration: 0.5, delay, ease: EASE }}
      className={cn("absolute z-10", className)}
    >
      <div className="float-y rounded-lg border border-causal/25 bg-[#060a13]/85 px-2.5 py-1.5 shadow-[0_0_24px_-10px_var(--causal)] backdrop-blur" style={{ animationDelay: `${delay}s` }}>
        <p className="hud-label !text-[9px]">{label}</p>
        <p className="tabular text-sm text-cyan-50">{value}</p>
      </div>
    </motion.div>
  );
}

function HeroVisual() {
  const config = useApi(() => api.config(), "config");
  const status = useLiveStore((state) => state.status);
  const latestRisk = useLiveStore((state) => state.latestRisk);
  const probability = latestRisk?.twin_risk?.probability;
  const level = riskLevel(probability, config.data?.optimizer.target_risk);
  const reduceMotion = useReducedMotion();
  return (
    <motion.div
      initial={reduceMotion ? false : { opacity: 0, scale: 0.96 }}
      animate={{ opacity: 1, scale: 1 }}
      transition={{ duration: 0.9, ease: EASE }}
      className="relative mx-auto w-full max-w-[560px]"
    >
      <div className="pointer-events-none absolute inset-0 overflow-hidden rounded-[2rem]" aria-hidden>
        <div className="absolute top-1/2 left-1/2 size-[70%] -translate-x-1/2 -translate-y-1/2 rounded-full bg-causal/10 blur-3xl" />
        <div className="absolute top-[20%] right-[10%] size-40 rounded-full bg-whatif/10 blur-3xl" />
        <div className="grid-floor" />
      </div>
      <div className="relative px-4 pt-2 pb-6">
        <SpindleTwin config={config.data} riskLevel={level} />
      </div>
      <DataChip label="Simulation step" value={status ? status.step.toLocaleString() : "--"} className="top-2 left-0" delay={0.6} />
      <DataChip
        label="Twin failure risk"
        value={probability === undefined ? "--" : formatPercent(probability)}
        className="top-6 right-0"
        delay={0.75}
      />
      <DataChip label="Drift monitor" value={status ? humanize(status.drift_state) : "--"} className="bottom-6 left-2" delay={0.9} />
      <DataChip label="Scenario" value={status ? humanize(status.scenario) : "--"} className="right-2 bottom-2" delay={1.05} />
    </motion.div>
  );
}

function Hero() {
  const reduceMotion = useReducedMotion();
  return (
    <section className="relative grid grid-cols-1 items-center gap-10 pt-2 pb-6 lg:grid-cols-[1.05fr_1fr]">
      <div>
        <motion.div
          initial={reduceMotion ? false : { opacity: 0, y: 8 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.4 }}
          className="mb-5 flex flex-wrap items-center gap-2"
        >
          <span className="inline-flex items-center gap-1.5 rounded-full border border-causal/30 bg-causal/5 px-2.5 py-0.5 text-[11px] text-cyan-200">
            <Sparkles className="size-3" aria-hidden /> Causal digital twin for industrial failure intelligence
          </span>
          <SimulatedBadge />
          <GroundTruthBadge />
        </motion.div>
        <Headline />
        <motion.p
          initial={reduceMotion ? false : { opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.6, delay: 0.7, ease: EASE }}
          className="mt-5 max-w-xl text-base leading-relaxed text-slate-300 sm:text-lg"
        >
          An intelligent causal digital twin that discovers root causes, simulates interventions, and recommends the smallest action
          that prevents machine failure.
        </motion.p>
        <motion.div
          initial={reduceMotion ? false : { opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.6, delay: 0.85, ease: EASE }}
          className="mt-8 flex flex-wrap gap-3"
        >
          <Button
            size="lg"
            asChild
            className="group h-12 bg-gradient-to-r from-cyan-400 to-sky-400 px-6 text-sm font-semibold text-slate-950 shadow-[0_0_36px_-8px_var(--causal)] transition-shadow hover:shadow-[0_0_48px_-6px_var(--causal)]"
          >
            <Link href="/control">
              Enter the command center <ArrowRight className="transition-transform group-hover:translate-x-1" aria-hidden />
            </Link>
          </Button>
          <Button size="lg" variant="outline" asChild className="h-12 border-line bg-white/[0.02] px-6 text-sm hover:border-causal/40">
            <Link href="/benchmark">See the proof</Link>
          </Button>
        </motion.div>
        <motion.p
          initial={reduceMotion ? false : { opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ duration: 0.6, delay: 1.1 }}
          className="mt-6 max-w-xl text-xs leading-relaxed text-muted-foreground"
        >
          Runs here on a simulated CNC spindle whose true causes are known, so every answer is checked against that ground truth.
          Physical constants are illustrative.
        </motion.p>
      </div>
      <HeroVisual />
    </section>
  );
}

/* ---------------------------------------------------------------------------- Key numbers */

function MetricCard({ index, value, label, accent }: { index: number; value: ReactNode; label: string; accent: string }) {
  const reduceMotion = useReducedMotion();
  return (
    <motion.div
      initial={reduceMotion ? false : { opacity: 0, y: 18 }}
      whileInView={{ opacity: 1, y: 0 }}
      viewport={{ once: true, amount: 0.4 }}
      transition={{ duration: 0.5, delay: index * 0.08, ease: EASE }}
      className="glass lift relative overflow-hidden p-5"
    >
      <span className={cn("absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent to-transparent", accent)} aria-hidden />
      <p className="tabular text-4xl font-medium tracking-tight text-slate-50 xl:text-[2.6rem]">{value}</p>
      <p className="mt-2 text-xs leading-relaxed text-muted-foreground">{label}</p>
    </motion.div>
  );
}

function KeyNumbers() {
  const benchmark = useApi(() => api.benchmark(), "benchmark");
  const drift = useApi(() => api.driftResults(), "drift");
  const config = useApi(() => api.config(), "config");
  if (!benchmark.data) {
    return benchmark.error ? <p className="text-sm text-muted-foreground">Benchmark results are not available yet: {benchmark.error}</p> : <PanelSkeleton rows={3} />;
  }
  const summary = benchmark.data.summary;
  const cases = benchmark.data.n_cases;
  const main = summary.main_baseline;
  const strongest = summary.baselines[main];
  const baselineMethods = (Object.keys(summary.baseline_labels) as BaselineMethod[]).reverse();
  const madeWorse = baselineMethods.reduce((total, method) => total + summary.baselines[method].n_worse_than_no_action, 0);
  const madeWorseByMethod = baselineMethods
    .map((method) => `${summary.baseline_labels[method].split(":")[0]} ${summary.baselines[method].n_worse_than_no_action}`)
    .join(", ");
  const vibration = summary.baselines.naive_shap.blamed.vibration ?? 0;
  const runs = drift.data?.faults ?? [];
  const detected = runs.filter((run) => run.detected_step !== null).length;
  const delays = runs.map((run) => run.detection_delay_steps).filter((value): value is number => typeof value === "number");
  const medianDelay = delays.length ? [...delays].sort((first, second) => first - second)[Math.floor(delays.length / 2)] : null;
  const secondsPerStep = config.data?.seconds_per_step ?? null;
  const targetRisk = formatPercent(benchmark.data.settings.target_risk, 0);
  const delayText = medianDelay === null ? "" : secondsPerStep === null ? `${medianDelay} steps` : `${Math.round(medianDelay * secondsPerStep)} s`;
  return (
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
      <MetricCard
        index={0}
        accent="via-causal"
        value={
          <>
            <CountUp value={summary.causaltwin.n_success} format={(value) => Math.round(value).toString()} durationMs={1400} />
            <span className="text-slate-500"> vs </span>
            <CountUp value={strongest.n_success} format={(value) => Math.round(value).toString()} durationMs={1400} />
          </>
        }
        label={`of ${cases} simulated high-risk cases brought to ${targetRisk} true risk or less: CausalTwin versus the strongest predictive baseline (${summary.baseline_labels[main]}, XGBoost scoring the same candidate actions), judged by the simulator`}
      />
      <MetricCard
        index={1}
        accent="via-risk"
        value={<CountUp value={madeWorse} format={(value) => Math.round(value).toString()} durationMs={1400} />}
        label={`times a predictive baseline made things worse than doing nothing (${madeWorseByMethod}); CausalTwin: ${summary.causaltwin.n_worse_than_no_action}`}
      />
      <MetricCard
        index={2}
        accent="via-whatif"
        value={
          runs.length ? (
            <>
              <CountUp value={detected} format={(value) => Math.round(value).toString()} durationMs={1400} />
              <span className="text-slate-500"> / {runs.length}</span>
            </>
          ) : (
            <>-</>
          )
        }
        label={`injected physical faults detected (clogged filter, lube leak, bearing damage)${delayText ? `; median detection time ${delayText} of machine time` : ""}`}
      />
      <MetricCard
        index={3}
        accent="via-warn"
        value={
          <>
            <CountUp value={vibration} format={(value) => Math.round(value).toString()} durationMs={1400} />
            <span className="text-slate-500"> / {cases}</span>
          </>
        }
        label={`high-risk cases where the naive SHAP rule (${summary.baseline_labels.naive_shap.split(":")[0]}) blamed vibration, which has no causal effect on failure`}
      />
    </div>
  );
}

/* -------------------------------------------------------------------------------- Pipeline */

const STAGES = [
  { title: "Sensor data", text: "Time series and the event log, aligned and quality-checked.", icon: Activity, color: "#7dd3fc" },
  { title: "Causal discovery", text: "PCMCI finds cause and effect with time lags, bootstrapped for confidence.", icon: Network, color: "#22d3ee" },
  { title: "Digital twin", text: "Fitted equations for every variable plus a failure model, updated live.", icon: Boxes, color: "#38bdf8" },
  { title: "What-if simulation", text: "Monte Carlo rollouts of the causal model under interventions.", icon: FlaskConical, color: "#a78bfa" },
  { title: "Optimal intervention", text: "The cheapest change that brings failure risk below target, checked against ground truth.", icon: ShieldCheck, color: "#34d399" },
];

function Pipeline() {
  const reduceMotion = useReducedMotion();
  const section = useRef<HTMLElement>(null);
  const { scrollYProgress } = useScroll({ target: section, offset: ["start 85%", "end 55%"] });
  const progress = useSpring(scrollYProgress, { stiffness: 120, damping: 30 });
  const width = useTransform(progress, (value) => `${Math.min(100, Math.max(0, value * 100))}%`);
  return (
    <section ref={section} aria-labelledby="pipeline-title">
      <div className="mb-6 flex flex-wrap items-end justify-between gap-3">
        <div>
          <p className="hud-label mb-1.5 !text-cyan-300/80">How it works</p>
          <h2 id="pipeline-title" className="font-heading text-2xl font-semibold tracking-tight text-slate-50">
            From raw sensor data to the smallest action that prevents failure
          </h2>
        </div>
        <span className="flex items-center gap-1.5 text-xs text-muted-foreground">
          <Cpu className="size-3.5 text-causal" aria-hidden /> CPU only, no deep learning: every step in seconds
        </span>
      </div>
      <div className="relative">
        <div className="absolute top-[30px] right-[10%] left-[10%] hidden h-px bg-line lg:block" aria-hidden>
          <motion.div className="h-full bg-gradient-to-r from-ice via-causal to-good shadow-[0_0_12px_var(--causal)]" style={{ width: reduceMotion ? "100%" : width }} />
        </div>
        <ol className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-5">
          {STAGES.map((stage, index) => (
            <motion.li
              key={stage.title}
              initial={reduceMotion ? false : { opacity: 0, y: 24 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true, amount: 0.5 }}
              transition={{ duration: 0.55, delay: index * 0.12, ease: EASE }}
              className="relative"
            >
              <div className="relative z-10 mx-auto mb-3 flex size-[60px] items-center justify-center rounded-2xl border bg-[#060a13]" style={{ borderColor: `${stage.color}55`, boxShadow: `0 0 28px -10px ${stage.color}` }}>
                <stage.icon className="size-6" style={{ color: stage.color }} aria-hidden />
              </div>
              <div className="glass lift h-[calc(100%-72px)] p-4 text-center">
                <p className="hud-label">Stage {index + 1}</p>
                <p className="mt-1 font-heading text-sm font-semibold text-slate-100">{stage.title}</p>
                <p className="mt-1.5 text-xs leading-relaxed text-muted-foreground">{stage.text}</p>
              </div>
            </motion.li>
          ))}
        </ol>
      </div>
    </section>
  );
}

/* ------------------------------------------------------------------------- Correlation Trap */

function CorrelationTrap() {
  const reduceMotion = useReducedMotion();
  return (
    <section className="grid grid-cols-1 items-center gap-6 lg:grid-cols-[1fr_1.1fr]" aria-labelledby="trap-title">
      <motion.div
        initial={reduceMotion ? false : { opacity: 0, x: -20 }}
        whileInView={{ opacity: 1, x: 0 }}
        viewport={{ once: true, amount: 0.4 }}
        transition={{ duration: 0.6, ease: EASE }}
      >
        <p className="hud-label mb-1.5 !text-amber-300/90">The Correlation Trap</p>
        <h2 id="trap-title" className="font-heading text-2xl font-semibold tracking-tight text-slate-50">
          Vibration rises before failure. It does not cause it.
        </h2>
        <p className="mt-3 text-sm leading-relaxed text-slate-300">
          A standard model (XGBoost with SHAP explanations) sees vibration climb before every failure and blames it. Damping vibration
          leaves the risk unchanged, because heat and tool wear are the real causes. CausalTwin separates symptoms from causes, then
          proves its fix in the simulator.
        </p>
        <p className="mt-2 text-sm leading-relaxed text-slate-300">
          Skipping the symptom is not enough. Even a predictive model that tries every possible change asks &quot;how risky is a machine
          that looks like this?&quot;, not &quot;what happens if I change this?&quot;, so it can say &quot;no action needed&quot; or make things
          worse. The Benchmark page measures that too.
        </p>
        <div className="mt-5 flex flex-wrap gap-3">
          <Button variant="outline" asChild className="hover:border-causal/40">
            <Link href="/graph">Explore the causal graph</Link>
          </Button>
          <Button variant="ghost" asChild>
            <Link href="/advisor">
              See a recommendation <ArrowRight aria-hidden />
            </Link>
          </Button>
        </div>
      </motion.div>
      <Panel index={1} title="Correlation view versus causal view" subtitle="Illustration of the trap" icon={<Network className="size-4" />}>
        <TrapIllustration />
      </Panel>
    </section>
  );
}

function FinalCallToAction() {
  const reduceMotion = useReducedMotion();
  return (
    <motion.section
      initial={reduceMotion ? false : { opacity: 0, y: 20 }}
      whileInView={{ opacity: 1, y: 0 }}
      viewport={{ once: true, amount: 0.5 }}
      transition={{ duration: 0.6, ease: EASE }}
      className="glass relative overflow-hidden px-6 py-10 text-center"
    >
      <div className="pointer-events-none absolute -top-24 left-1/2 h-48 w-[60%] -translate-x-1/2 rounded-full bg-causal/15 blur-3xl" aria-hidden />
      <p className="hud-label !text-cyan-300/80">Live command center</p>
      <h2 className="mt-2 font-heading text-2xl font-semibold tracking-tight text-slate-50 sm:text-3xl">
        Watch the twin find the cause, in real time.
      </h2>
      <p className="mx-auto mt-2 max-w-xl text-sm text-muted-foreground">
        Open the Control Room, or play the guided demo: risk rises, the cheapest causal fix is applied, a hidden fault appears, and the
        graph rewires itself.
      </p>
      <Button
        size="lg"
        asChild
        className="group mt-6 h-12 bg-gradient-to-r from-cyan-400 to-sky-400 px-6 text-sm font-semibold text-slate-950 shadow-[0_0_36px_-8px_var(--causal)]"
      >
        <Link href="/control">
          Launch the Control Room <ArrowRight className="transition-transform group-hover:translate-x-1" aria-hidden />
        </Link>
      </Button>
    </motion.section>
  );
}

export function Landing() {
  return (
    <div className="space-y-16 pb-8">
      <Hero />
      <KeyNumbers />
      <Pipeline />
      <CorrelationTrap />
      <FinalCallToAction />
    </div>
  );
}
