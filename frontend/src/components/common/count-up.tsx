"use client";

/** A number that counts up to its value (instant when reduced motion is preferred). */

import { animate, useReducedMotion } from "framer-motion";
import { useEffect, useRef } from "react";

interface CountUpProps {
  value: number;
  format: (value: number) => string;
  className?: string;
  durationMs?: number;
}

export function CountUp({ value, format, className, durationMs = 600 }: CountUpProps) {
  const reduceMotion = useReducedMotion();
  const element = useRef<HTMLSpanElement>(null);
  const previous = useRef(0);

  useEffect(() => {
    const node = element.current;
    if (!node) return;
    if (reduceMotion) {
      node.textContent = format(value);
      previous.current = value;
      return;
    }
    const controls = animate(previous.current, value, {
      duration: durationMs / 1000,
      ease: "easeOut",
      onUpdate: (current) => {
        node.textContent = format(current);
      },
    });
    previous.current = value;
    return () => controls.stop();
  }, [value, format, reduceMotion, durationMs]);

  return (
    <span ref={element} className={className}>
      {format(value)}
    </span>
  );
}
