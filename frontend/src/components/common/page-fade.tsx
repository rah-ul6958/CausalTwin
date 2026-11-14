"use client";

/**
 * Quick crossfade between pages using React's <ViewTransition> (route navigations are
 * transitions, so it runs automatically). Pages are not remounted, so their state is kept.
 * The animation itself is defined in globals.css (.page-fade) and disabled for reduced motion.
 */

import { ViewTransition, type ReactNode } from "react";

export function PageFade({ children }: { children: ReactNode }) {
  return (
    <ViewTransition enter="page-fade" exit="page-fade" default="none">
      {children}
    </ViewTransition>
  );
}
