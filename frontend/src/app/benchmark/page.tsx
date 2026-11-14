import type { Metadata } from "next";

import { PageFade } from "@/components/common/page-fade";
import { Benchmark } from "@/components/pages/benchmark";

export const metadata: Metadata = { title: "Benchmark - CausalTwin" };

export default function BenchmarkPage() {
  return (
    <PageFade>
      <Benchmark />
    </PageFade>
  );
}
