"use client";

/** Which live data source the backend uses, from the live status (the simulator until known). */

import { useLiveStore } from "@/lib/store";
import type { DataSourceInfo } from "@/lib/types";

export const SIMULATOR_SOURCE: DataSourceInfo = {
  kind: "simulator",
  description: "Simulated CNC spindle (illustrative constants)",
  has_simulator: true,
  simulated: true,
};

/** The current data source; simulator-only features check `has_simulator`. */
export function useDataSource(): DataSourceInfo {
  return useLiveStore((state) => state.status?.data_source) ?? SIMULATOR_SOURCE;
}
