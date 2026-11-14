import type { Metadata } from "next";

import { PageFade } from "@/components/common/page-fade";
import { GraphExplorer } from "@/components/pages/graph-explorer";

export const metadata: Metadata = { title: "Causal Graph - CausalTwin" };

export default function GraphPage() {
  return (
    <PageFade>
      <GraphExplorer />
    </PageFade>
  );
}
