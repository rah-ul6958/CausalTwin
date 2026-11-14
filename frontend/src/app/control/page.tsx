import type { Metadata } from "next";

import { PageFade } from "@/components/common/page-fade";
import { ControlRoom } from "@/components/pages/control-room";

export const metadata: Metadata = { title: "Control Room - CausalTwin" };

export default function ControlPage() {
  return (
    <PageFade>
      <ControlRoom />
    </PageFade>
  );
}
