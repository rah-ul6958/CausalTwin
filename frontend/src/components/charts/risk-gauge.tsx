"use client";

/**
 * Animated semicircle gauge for the twin's failure-risk estimate with its interval.
 * The scale is square-root (small risks stay visible); tick labels show the true values.
 * Zones: low (below half the target), elevated (up to the target), critical (at or above it).
 */

import { motion, useReducedMotion, useSpring, useTransform } from "framer-motion";
import { useEffect } from "react";

import { formatPercent } from "@/lib/format";
import type { RiskEstimate } from "@/lib/types";

const CENTER_X = 120;
const CENTER_Y = 118;
const RADIUS = 96;
const TICKS = [0, 0.05, 0.1, 0.25, 0.5, 1];

function toScale(probability: number): number {
  return Math.sqrt(Math.min(1, Math.max(0, probability)));
}

function point(fraction: number, radius = RADIUS): [number, number] {
  const angle = Math.PI * (1 - fraction);
  return [CENTER_X + radius * Math.cos(angle), CENTER_Y - radius * Math.sin(angle)];
}

function arcPath(fromFraction: number, toFraction: number, radius = RADIUS): string {
  const [startX, startY] = point(fromFraction, radius);
  const [endX, endY] = point(Math.max(fromFraction + 0.0001, toFraction), radius);
  return `M ${startX} ${startY} A ${radius} ${radius} 0 0 1 ${endX} ${endY}`;
}

function riskColor(probability: number, target: number): string {
  if (probability >= target) return "#f43f5e";
  if (probability >= target / 2) return "#fbbf24";
  return "#34d399";
}

export function RiskGauge({ risk, target }: { risk: RiskEstimate | null; target: number }) {
  const reduceMotion = useReducedMotion();
  const spring = useSpring(0, reduceMotion ? { duration: 0 } : { stiffness: 80, damping: 18 });
  const valuePath = useTransform(spring, (value) => arcPath(0, value));
  const needleX = useTransform(spring, (value) => point(value, RADIUS - 30)[0]);
  const needleY = useTransform(spring, (value) => point(value, RADIUS - 30)[1]);
  const value = risk?.probability ?? 0;

  useEffect(() => {
    spring.set(toScale(value));
  }, [spring, value]);

  const color = riskColor(value, target);
  return (
    <svg viewBox="0 0 240 150" className="w-full max-w-[320px]" role="img" aria-label={`Twin risk estimate ${formatPercent(risk?.probability)}`}>
      <path d={arcPath(0, 1)} stroke="rgb(148 163 184 / 0.12)" strokeWidth={14} fill="none" strokeLinecap="round" />
      {/* Zones: low, elevated, critical (relative to the target risk). */}
      <path d={arcPath(0, toScale(target / 2), RADIUS - 13)} stroke="#34d399" strokeOpacity={0.55} strokeWidth={2.5} fill="none" />
      <path d={arcPath(toScale(target / 2), toScale(target), RADIUS - 13)} stroke="#fbbf24" strokeOpacity={0.6} strokeWidth={2.5} fill="none" />
      <path d={arcPath(toScale(target), 1, RADIUS - 13)} stroke="#f43f5e" strokeOpacity={0.55} strokeWidth={2.5} fill="none" />
      {risk && (
        <path
          d={arcPath(toScale(risk.lower), toScale(risk.upper), RADIUS + 13)}
          stroke={color}
          strokeOpacity={0.35}
          strokeWidth={4}
          fill="none"
          strokeLinecap="round"
        />
      )}
      {/* A wide translucent copy is the glow (a blur filter re-rendered on every spring frame). */}
      <motion.path d={valuePath} stroke={color} strokeOpacity={0.22} strokeWidth={24} fill="none" strokeLinecap="round" />
      <motion.path d={valuePath} stroke={color} strokeWidth={14} fill="none" strokeLinecap="round" />
      {risk && (
        <>
          <motion.line x1={CENTER_X} y1={CENTER_Y} x2={needleX} y2={needleY} stroke="#e2e8f0" strokeWidth={1.6} strokeLinecap="round" />
          <circle cx={CENTER_X} cy={CENTER_Y} r={4} fill="#03050a" stroke={color} strokeWidth={2} />
        </>
      )}
      {(() => {
        const [x1, y1] = point(toScale(target), RADIUS - 12);
        const [x2, y2] = point(toScale(target), RADIUS + 9);
        return <line x1={x1} y1={y1} x2={x2} y2={y2} stroke="#e2e8f0" strokeWidth={1.5} strokeDasharray="2 2" />;
      })()}
      {TICKS.map((tick) => {
        const [x, y] = point(toScale(tick), RADIUS - 26);
        return (
          <text key={tick} x={x} y={y} fill="#64748b" fontSize={8} textAnchor="middle" fontFamily="var(--font-jetbrains-mono)">
            {tick === 0 ? "0" : `${tick * 100}%`}
          </text>
        );
      })}
    </svg>
  );
}
