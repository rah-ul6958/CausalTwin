"use client";

/**
 * Live visualization of the simulated CNC spindle, seen from the front: the rotor, the bearing
 * ring, the coolant channel, the tool-wear arc and the sensors around them.
 *
 * Every visual property is driven by streamed data, never by made-up numbers:
 * - rotor speed follows `spindle_speed` (slowed down so it stays readable);
 * - the bearing ring's colour follows `bearing_temp` against its normal range;
 * - the rotor shakes only while `vibration` is above its normal range;
 * - the coolant channel flows faster with more `coolant_flow`;
 * - the wear arc fills with `tool_wear` against its normal range;
 * - a stopped machine stops the rotor; a failure or injected fault turns the core red or amber.
 * Normal ranges are the illustrative ones from GET /config.
 */

import type { CSSProperties } from "react";

import { formatNumber, humanize } from "@/lib/format";
import { useLiveStore } from "@/lib/store";
import type { AppConfig } from "@/lib/types";
import { cn } from "@/lib/utils";

const CENTER = 160;
/** Sensors drawn around the ring: variable, angle in degrees (0 = right, clockwise), short label. */
const SATELLITES: { name: string; angle: number; label: string }[] = [
  { name: "spindle_speed", angle: -150, label: "Speed" },
  { name: "load_pct", angle: -105, label: "Load" },
  { name: "motor_current", angle: -60, label: "Current" },
  { name: "bearing_temp", angle: -15, label: "Bearing" },
  { name: "vibration", angle: 30, label: "Vibration" },
  { name: "tool_wear", angle: 75, label: "Tool wear" },
  { name: "coolant_flow", angle: 150, label: "Coolant" },
  { name: "coolant_temp", angle: 195, label: "Coolant temp" },
];

export type RiskLevel = "low" | "elevated" | "critical" | "unknown";

const RISK_COLOR: Record<RiskLevel, string> = {
  low: "#34d399",
  elevated: "#fbbf24",
  critical: "#f43f5e",
  unknown: "#64748b",
};

/** Where a value sits in its normal range (0 = bottom, 1 = top; may go beyond). */
function fraction(value: number | null | undefined, range: [number, number] | null | undefined): number | null {
  if (value === null || value === undefined || !range || range[1] === range[0]) return null;
  return (value - range[0]) / (range[1] - range[0]);
}

function temperatureColor(position: number | null): string {
  if (position === null) return "#64748b";
  if (position > 1) return "#f43f5e";
  if (position > 0.75) return "#fbbf24";
  return "#22d3ee";
}

function polar(angleDegrees: number, radius: number): [number, number] {
  const angle = (angleDegrees * Math.PI) / 180;
  return [CENTER + radius * Math.cos(angle), CENTER + radius * Math.sin(angle)];
}

function arc(radius: number, from: number, to: number): string {
  const [x1, y1] = polar(from, radius);
  const [x2, y2] = polar(to, radius);
  const large = to - from > 180 ? 1 : 0;
  return `M ${x1} ${y1} A ${radius} ${radius} 0 ${large} 1 ${x2} ${y2}`;
}

export interface SpindleTwinProps {
  config: AppConfig | undefined;
  riskLevel: RiskLevel;
  /** Draw sensor labels with live values around the ring. */
  labels?: boolean;
  className?: string;
}

export function SpindleTwin({ config, riskLevel, labels = true, className }: SpindleTwinProps) {
  const latest = useLiveStore((state) => state.latest);
  const values = latest?.values ?? {};
  const variables = config?.variables ?? {};
  const running = latest ? latest.machine_running === 1 : false;
  const failed = latest?.failure === 1;
  const faults = latest?.active_faults ?? [];

  const speed = values.spindle_speed;
  // Real spindles turn thousands of times a minute; the drawing turns once every 0.7-3 s.
  const spinSeconds = speed ? Math.max(0.7, 3 - (speed / 5000) * 2.3) : 3;
  const bearing = fraction(values.bearing_temp, variables.bearing_temp?.normal_range);
  const vibration = fraction(values.vibration, variables.vibration?.normal_range);
  const coolant = fraction(values.coolant_flow, variables.coolant_flow?.normal_range);
  const wear = fraction(values.tool_wear, variables.tool_wear?.normal_range);
  const bearingColor = temperatureColor(bearing);
  const core = failed ? "#f43f5e" : faults.length ? "#fbbf24" : RISK_COLOR[riskLevel];
  const shaking = running && vibration !== null && vibration > 1;
  const coolantSeconds = coolant === null ? 2 : Math.max(0.5, 2.2 - coolant * 1.4);
  const wearSweep = wear === null ? 0 : Math.min(1, Math.max(0, wear)) * 300;

  const rotorStyle = { "--spin-duration": `${spinSeconds}s`, animationPlayState: running ? "running" : "paused" } as CSSProperties;
  const label = `Live spindle visualization: ${running ? "running" : "stopped"}${failed ? ", failure" : ""}${
    faults.length ? `, fault ${faults.map(humanize).join(", ")}` : ""
  }`;

  return (
    <svg viewBox="-60 -6 440 332" className={cn("w-full", className)} role="img" aria-label={label}>
      <defs>
        <radialGradient id="spindle-core" cx="50%" cy="50%" r="50%">
          <stop offset="0%" stopColor={core} stopOpacity={0.9} />
          <stop offset="60%" stopColor={core} stopOpacity={0.15} />
          <stop offset="100%" stopColor={core} stopOpacity={0} />
        </radialGradient>
        <radialGradient id="spindle-disk" cx="40%" cy="35%" r="70%">
          <stop offset="0%" stopColor="#1e293b" />
          <stop offset="100%" stopColor="#070b14" />
        </radialGradient>
        <filter id="spindle-glow" x="-50%" y="-50%" width="200%" height="200%">
          <feGaussianBlur stdDeviation="3" result="blur" />
          <feMerge>
            <feMergeNode in="blur" />
            <feMergeNode in="SourceGraphic" />
          </feMerge>
        </filter>
      </defs>

      {/* Outer housing with tick marks. */}
      <circle cx={CENTER} cy={CENTER} r={132} fill="none" stroke="rgb(125 211 252 / 0.12)" strokeWidth={1} />
      {Array.from({ length: 72 }, (_, index) => {
        const long = index % 6 === 0;
        const [x1, y1] = polar(index * 5, long ? 122 : 126);
        const [x2, y2] = polar(index * 5, 130);
        return <line key={index} x1={x1} y1={y1} x2={x2} y2={y2} stroke="rgb(148 163 184 / 0.3)" strokeWidth={long ? 1.2 : 0.6} />;
      })}

      {/* Coolant channel: flowing dashes, faster with more flow. */}
      <circle
        cx={CENTER}
        cy={CENTER}
        r={110}
        fill="none"
        stroke="#2dd4bf"
        strokeOpacity={coolant === null ? 0.15 : 0.25 + Math.min(1, Math.max(0, coolant)) * 0.5}
        strokeWidth={3}
        className="dash-flow"
        style={{ animationDuration: `${coolantSeconds}s`, animationPlayState: running ? "running" : "paused" }}
      />

      {/* Bearing ring: colour from bearing temperature. */}
      <circle cx={CENTER} cy={CENTER} r={96} fill="none" stroke={bearingColor} strokeOpacity={0.18} strokeWidth={12} />
      <circle cx={CENTER} cy={CENTER} r={96} fill="none" stroke={bearingColor} strokeWidth={2.5} filter="url(#spindle-glow)" />

      {/* Tool-wear arc. */}
      <path d={arc(84, -240, 60)} fill="none" stroke="rgb(148 163 184 / 0.12)" strokeWidth={4} strokeLinecap="round" />
      {wearSweep > 0 && (
        <path d={arc(84, -240, -240 + wearSweep)} fill="none" stroke="#f97316" strokeWidth={4} strokeLinecap="round" filter="url(#spindle-glow)" />
      )}

      {/* Rotor: shakes only while vibration is above its normal range. */}
      <g className={shaking ? "shake" : undefined} style={{ "--shake-amount": "1.6px" } as CSSProperties}>
        <circle cx={CENTER} cy={CENTER} r={70} fill="url(#spindle-disk)" stroke="rgb(125 211 252 / 0.35)" strokeWidth={1.2} />
        <g className="spin" style={rotorStyle}>
          {Array.from({ length: 6 }, (_, index) => {
            const [x1, y1] = polar(index * 60, 22);
            const [x2, y2] = polar(index * 60 + 18, 64);
            return (
              <path
                key={index}
                d={`M ${x1} ${y1} Q ${polar(index * 60 + 2, 48).join(" ")} ${x2} ${y2}`}
                stroke="rgb(125 211 252 / 0.55)"
                strokeWidth={2.2}
                fill="none"
                strokeLinecap="round"
              />
            );
          })}
          <circle cx={CENTER} cy={CENTER} r={64} fill="none" stroke="rgb(34 211 238 / 0.4)" strokeWidth={1} strokeDasharray="2 6" />
        </g>
        <circle cx={CENTER} cy={CENTER} r={34} fill="url(#spindle-core)" />
        <circle cx={CENTER} cy={CENTER} r={14} fill="#05080f" stroke={core} strokeWidth={2} filter="url(#spindle-glow)" />
      </g>

      {/* Sensors around the ring, with live values. */}
      {labels &&
        SATELLITES.filter((satellite) => satellite.name in values).map((satellite) => {
          const [nodeX, nodeY] = polar(satellite.angle, 132);
          const [innerX, innerY] = polar(satellite.angle, 100);
          const position = fraction(values[satellite.name], variables[satellite.name]?.normal_range);
          const color = position !== null && position > 1 ? "#f43f5e" : "#7dd3fc";
          const right = Math.cos((satellite.angle * Math.PI) / 180) >= 0;
          return (
            <g key={satellite.name}>
              <line x1={innerX} y1={innerY} x2={nodeX} y2={nodeY} stroke={color} strokeOpacity={0.5} strokeWidth={1} className="dash-flow" />
              <circle cx={nodeX} cy={nodeY} r={3.5} fill={color} filter="url(#spindle-glow)" />
              <text
                x={nodeX + (right ? 7 : -7)}
                y={nodeY - 3}
                textAnchor={right ? "start" : "end"}
                fontSize={8}
                fill="#94a3b8"
                fontFamily="var(--font-jetbrains-mono)"
                letterSpacing="0.08em"
              >
                {satellite.label.toUpperCase()}
              </text>
              <text
                x={nodeX + (right ? 7 : -7)}
                y={nodeY + 8}
                textAnchor={right ? "start" : "end"}
                fontSize={10}
                fill="#e2e8f0"
                fontFamily="var(--font-jetbrains-mono)"
              >
                {formatNumber(values[satellite.name], satellite.name === "spindle_speed" ? 0 : 1)}
              </text>
            </g>
          );
        })}
    </svg>
  );
}

/** Risk level of a probability against the optimizer's target (presentation of real values). */
export function riskLevel(probability: number | null | undefined, target: number | undefined): RiskLevel {
  if (probability === null || probability === undefined || target === undefined) return "unknown";
  if (probability >= target) return "critical";
  if (probability >= target / 2) return "elevated";
  return "low";
}
