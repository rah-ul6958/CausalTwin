/** WebSocket client for /ws/stream with automatic reconnect and batched delivery. */

import { STORE_FLUSH_MS } from "@/lib/config";
import type { StreamMessage } from "@/lib/types";

export type SocketStatus = "connecting" | "open" | "closed";

interface LiveSocketOptions {
  url: string;
  onBatch: (messages: StreamMessage[]) => void;
  onStatus: (status: SocketStatus) => void;
  onOpen?: () => void;
}

const MIN_RETRY_MS = 1000;
const MAX_RETRY_MS = 10000;

/** Keeps one connection alive; queues messages and hands them over in batches. */
export class LiveSocket {
  private socket: WebSocket | null = null;
  private queue: StreamMessage[] = [];
  private flushTimer: ReturnType<typeof setInterval> | null = null;
  private retryTimer: ReturnType<typeof setTimeout> | null = null;
  private retryDelay = MIN_RETRY_MS;
  private stopped = false;

  constructor(private readonly options: LiveSocketOptions) {}

  start(): void {
    this.stopped = false;
    this.flushTimer = setInterval(() => this.flush(), STORE_FLUSH_MS);
    this.connect();
  }

  stop(): void {
    this.stopped = true;
    if (this.flushTimer) clearInterval(this.flushTimer);
    if (this.retryTimer) clearTimeout(this.retryTimer);
    this.socket?.close();
    this.socket = null;
  }

  private connect(): void {
    this.options.onStatus("connecting");
    const socket = new WebSocket(this.options.url);
    this.socket = socket;
    socket.onopen = () => {
      this.retryDelay = MIN_RETRY_MS;
      this.options.onStatus("open");
      this.options.onOpen?.();
    };
    socket.onmessage = (event: MessageEvent<string>) => {
      try {
        this.queue.push(JSON.parse(event.data) as StreamMessage);
      } catch {
        /* ignore malformed messages */
      }
    };
    socket.onclose = () => {
      this.options.onStatus("closed");
      if (!this.stopped) {
        this.retryTimer = setTimeout(() => this.connect(), this.retryDelay);
        this.retryDelay = Math.min(this.retryDelay * 2, MAX_RETRY_MS);
      }
    };
    socket.onerror = () => socket.close();
  }

  private flush(): void {
    if (this.queue.length === 0) return;
    const batch = this.queue;
    this.queue = [];
    this.options.onBatch(batch);
  }
}
