/** Formatting helpers for numbers, units, percentages and durations. */

/** A number with sensible precision: no decimals above 1000, exponent form for tiny values. */
export function formatNumber(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "-";
  const magnitude = Math.abs(value);
  if (magnitude >= 1000) return value.toLocaleString("en-US", { maximumFractionDigits: 0 });
  if (magnitude >= 100) return value.toFixed(Math.max(0, digits - 1));
  if (magnitude !== 0 && magnitude < 0.01) return value.toExponential(1);
  return value.toFixed(digits);
}

/** A probability (0..1) as a percentage. */
export function formatPercent(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "-";
  return `${(value * 100).toFixed(digits)}%`;
}

/** A number with an explicit sign (+ or -). */
export function formatSigned(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "-";
  return `${value >= 0 ? "+" : ""}${formatNumber(value, digits)}`;
}

/** A duration in milliseconds, switching to seconds above 1000 ms. */
export function formatMs(value: number | null | undefined): string {
  if (value === null || value === undefined) return "-";
  if (value >= 1000) return `${(value / 1000).toFixed(2)} s`;
  return `${value.toFixed(value < 10 ? 1 : 0)} ms`;
}

/** A p-value with three decimals, or "< 0.001". */
export function formatPValue(value: number | null | undefined): string {
  if (value === null || value === undefined) return "-";
  if (value < 0.001) return "< 0.001";
  return value.toFixed(3);
}

/** "spindle_speed" -> "Spindle speed". */
export function humanize(name: string): string {
  const text = name.replace(/_/g, " ");
  return text.charAt(0).toUpperCase() + text.slice(1);
}

/** An ISO timestamp as a 24-hour clock time. */
export function formatTime(iso: string | undefined): string {
  if (!iso) return "-";
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? iso : date.toLocaleTimeString("en-GB", { hour12: false });
}

/** Short forms of long unit names, for compact headers (the full unit stays in tooltips). */
const SHORT_UNITS: Record<string, string> = {
  "minutes of cutting": "min",
  "relative units": "rel.",
};

/** A unit short enough for a small chart header. */
export function shortUnit(unit: string | null | undefined): string {
  if (!unit) return "";
  return SHORT_UNITS[unit] ?? unit;
}

/** A change in probability (0..1) as signed percentage points with fixed decimals, e.g. "+0.34 pts". */
export function formatPoints(change: number | null | undefined, digits = 2, label = "pts"): string {
  if (change === null || change === undefined || Number.isNaN(change)) return "-";
  const points = change * 100;
  return `${points >= 0 ? "+" : ""}${points.toFixed(digits)} ${label}`;
}
