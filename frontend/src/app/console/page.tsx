import type { Metadata } from "next";

import { PageFade } from "@/components/common/page-fade";
import { SystemConsole } from "@/components/pages/console";

export const metadata: Metadata = { title: "Console - CausalTwin" };

export default function ConsolePage() {
  return (
    <PageFade>
      <SystemConsole />
    </PageFade>
  );
}
