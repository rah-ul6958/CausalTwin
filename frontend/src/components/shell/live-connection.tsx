"use client";

/** Opens the WebSocket once for the whole app and seeds the store from the REST API. */

import { useEffect } from "react";

import { api } from "@/lib/api";
import { LIVE_BUFFER_POINTS, MAX_ALERTS, MAX_EVENTS, MAX_ROUTINE_LOGS, SEED_LOGS, STATUS_POLL_MS, WS_URL } from "@/lib/config";
import { useLiveStore } from "@/lib/store";
import { LiveSocket } from "@/lib/ws";

/** Fills the store with recent history from the REST API (missing parts stay empty). */
async function seedStore() {
  const [recent, riskHistory, events, alerts, logs, routineLogs] = await Promise.allSettled([
    api.dataRecent(LIVE_BUFFER_POINTS),
    api.riskHistory(LIVE_BUFFER_POINTS),
    api.events(MAX_EVENTS),
    api.alerts(MAX_ALERTS),
    api.logs(SEED_LOGS, "exclude"),
    api.logs(MAX_ROUTINE_LOGS, "only"),
  ]);
  useLiveStore.getState().seed({
    rows: recent.status === "fulfilled" ? recent.value.rows : [],
    riskHistory: riskHistory.status === "fulfilled" ? riskHistory.value.points : [],
    events: events.status === "fulfilled" ? events.value.events : [],
    alerts: alerts.status === "fulfilled" ? alerts.value.alerts : [],
    logs: logs.status === "fulfilled" ? logs.value : [],
    routineLogs: routineLogs.status === "fulfilled" ? routineLogs.value : [],
  });
}

/** Invisible component: owns the WebSocket, the re-seeding and the status polling. */
export function LiveConnection() {
  useEffect(() => {
    const socket = new LiveSocket({
      url: WS_URL,
      onBatch: (messages) => useLiveStore.getState().applyMessages(messages),
      onStatus: (status) => useLiveStore.getState().setConnection(status),
      onOpen: () => void seedStore(),
    });
    socket.start();
    // After a server-side restart of the simulation, fetch the new run's history.
    const unsubscribe = useLiveStore.subscribe((state, previous) => {
      if (state.runId !== previous.runId) void seedStore();
    });
    // The running flag can change on the server (for example the demo pauses the loop), so poll it.
    const refreshStatus = async () => {
      try {
        useLiveStore.getState().setStatus((await api.metrics()).status);
      } catch {
        /* the connection indicator already shows the problem */
      }
    };
    void refreshStatus();
    const timer = setInterval(() => void refreshStatus(), STATUS_POLL_MS);
    return () => {
      socket.stop();
      unsubscribe();
      clearInterval(timer);
    };
  }, []);
  return null;
}
