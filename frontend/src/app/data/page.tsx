import type { Metadata } from "next";

import { PageFade } from "@/components/common/page-fade";
import { DataPage } from "@/components/pages/data-page";

export const metadata: Metadata = { title: "Data - CausalTwin" };

export default function DataRoute() {
  return (
    <PageFade>
      <DataPage />
    </PageFade>
  );
}
