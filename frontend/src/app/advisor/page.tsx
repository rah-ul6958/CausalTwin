import type { Metadata } from "next";

import { PageFade } from "@/components/common/page-fade";
import { Advisor } from "@/components/pages/advisor";

export const metadata: Metadata = { title: "Advisor - CausalTwin" };

export default function AdvisorPage() {
  return (
    <PageFade>
      <Advisor />
    </PageFade>
  );
}
