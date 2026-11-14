/** Tiny SVG sparkline (no axes), used inside graph nodes. */

import { memo } from "react";

function SparklineBase({ values, color = "#22d3ee", width = 96, height = 22 }: {
  values: number[];
  color?: string;
  width?: number;
  height?: number;
}) {
  if (values.length < 2) return <svg width={width} height={height} aria-hidden />;
  const minimum = Math.min(...values);
  const maximum = Math.max(...values);
  const span = maximum - minimum || 1;
  const points = values
    .map((value, index) => {
      const x = (index / (values.length - 1)) * width;
      const y = height - 2 - ((value - minimum) / span) * (height - 4);
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");
  return (
    <svg width={width} height={height} aria-hidden>
      <polyline points={points} fill="none" stroke={color} strokeWidth={1.4} strokeLinejoin="round" opacity={0.85} />
    </svg>
  );
}

export const Sparkline = memo(SparklineBase);
