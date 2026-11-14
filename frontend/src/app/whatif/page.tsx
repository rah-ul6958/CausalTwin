import type { Metadata } from "next";

import { PageFade } from "@/components/common/page-fade";
import { WhatIfLab } from "@/components/pages/whatif-lab";

export const metadata: Metadata = { title: "What-If Lab - CausalTwin" };

export default function WhatIfPage() {
  return (
    <PageFade>
      <WhatIfLab />
    </PageFade>
  );
}
