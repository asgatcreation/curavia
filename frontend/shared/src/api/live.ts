/**
 * The live connection.
 *
 * Three rules, and they are all about the same thing: **a board that has
 * silently stopped updating is worse than no board**, because people believe
 * it. A nurse glancing at a frozen screen concludes nobody is waiting.
 *
 * 1. **The socket is never the only source.** Every stream has an HTTP
 *    equivalent, and the connection asks for a full snapshot whenever it
 *    opens — including every reopen. A delta missed while reconnecting is
 *    repaired by the next snapshot rather than lingering until something
 *    else happens to change.
 *
 * 2. **The state is visible.** `onState` reports connecting, live and
 *    offline, and the screens draw it. A clock that stops is the warning
 *    sign people recognise, so the board also shows how long ago its data
 *    arrived.
 *
 * 3. **Reconnection backs off and keeps trying forever.** A hospital's
 *    network drops when the generator takes over. The board should come
 *    back by itself, and it should not hammer a server that is restarting.
 */

import type { ApiClient } from "./client";

export type LiveState = "connecting" | "live" | "offline";

export interface LiveOptions {
  /** Called for every message the server sends. */
  onMessage: (message: Record<string, unknown>) => void;
  onState?: (state: LiveState) => void;
  /** Starting backoff; doubles up to `maxDelayMs`. */
  baseDelayMs?: number;
  maxDelayMs?: number;
}

const DEFAULT_BASE_DELAY = 1_000;
const DEFAULT_MAX_DELAY = 30_000;

/** A heartbeat, so a connection dropped by a proxy is noticed rather than assumed. */
const PING_INTERVAL_MS = 25_000;

function socketUrl(path: string, query: Record<string, string> = {}): string {
  const base = new URL(path, window.location.origin);
  base.protocol = base.protocol === "https:" ? "wss:" : "ws:";
  for (const [key, value] of Object.entries(query)) base.searchParams.set(key, value);
  return base.toString();
}

/**
 * One reconnecting socket.
 *
 * `close()` is final: it stops the retries as well as the socket, so a
 * screen that has been navigated away from does not quietly keep a
 * connection open.
 */
export class LiveConnection {
  private socket: WebSocket | null = null;
  private timer: ReturnType<typeof setTimeout> | null = null;
  private pinger: ReturnType<typeof setInterval> | null = null;
  private attempt = 0;
  private closed = false;

  constructor(
    private readonly path: string,
    private readonly options: LiveOptions,
    /** Fetches a fresh ticket. Omitted for sockets that need no account. */
    private readonly ticketFor?: () => Promise<string>,
  ) {}

  async open(): Promise<void> {
    if (this.closed) return;
    this.options.onState?.("connecting");

    let url: string;
    try {
      // A new ticket per connection, including every reconnect: it is
      // single-use, and re-fetching it re-checks that this member of staff
      // still works here.
      const ticket = this.ticketFor ? await this.ticketFor() : "";
      url = socketUrl(this.path, ticket ? { ticket } : {});
    } catch {
      this.retry();
      return;
    }
    if (this.closed) return;

    const socket = new WebSocket(url);
    this.socket = socket;

    socket.onopen = () => {
      this.attempt = 0;
      this.options.onState?.("live");
      this.pinger = setInterval(() => {
        if (socket.readyState === WebSocket.OPEN) {
          socket.send(JSON.stringify({ type: "ping" }));
        }
      }, PING_INTERVAL_MS);
    };

    socket.onmessage = (event: MessageEvent<string>) => {
      try {
        this.options.onMessage(JSON.parse(event.data) as Record<string, unknown>);
      } catch {
        // A message we cannot parse is a bug on the server, not a reason to
        // tear down a working board.
      }
    };

    socket.onclose = () => {
      this.stopPinging();
      if (this.closed) return;
      this.options.onState?.("offline");
      this.retry();
    };

    // `onerror` is always followed by `onclose`, so the reconnect is handled
    // in one place rather than two that can both fire.
    socket.onerror = () => {};
  }

  /** Ask the server for a complete picture again. */
  refresh(): void {
    if (this.socket?.readyState === WebSocket.OPEN) {
      this.socket.send(JSON.stringify({ type: "refresh" }));
    }
  }

  close(): void {
    this.closed = true;
    this.stopPinging();
    if (this.timer) clearTimeout(this.timer);
    this.timer = null;
    this.socket?.close();
    this.socket = null;
  }

  private stopPinging(): void {
    if (this.pinger) clearInterval(this.pinger);
    this.pinger = null;
  }

  private retry(): void {
    if (this.closed) return;
    const base = this.options.baseDelayMs ?? DEFAULT_BASE_DELAY;
    const max = this.options.maxDelayMs ?? DEFAULT_MAX_DELAY;
    // Exponential, with jitter: without it, every board in the hospital
    // reconnects in the same millisecond after a power cut and the server
    // comes back up into a stampede.
    const delay = Math.min(base * 2 ** this.attempt, max) * (0.5 + Math.random() / 2);
    this.attempt += 1;
    this.timer = setTimeout(() => void this.open(), delay);
  }
}

/** The staff board's socket. Needs a ticket, because it carries patient names. */
export function connectJourney(client: ApiClient, options: LiveOptions): LiveConnection {
  const connection = new LiveConnection("/ws/journey/", options, async () => {
    const { ticket } = await client.post<{ ticket: string }>("/ws/ticket/", {});
    return ticket;
  });
  void connection.open();
  return connection;
}

/**
 * A waiting-room television's socket.
 *
 * No ticket and no account: the token in the path is the whole of it, and
 * what it buys is numbers with no names.
 */
export function connectDisplay(token: string, options: LiveOptions): LiveConnection {
  const connection = new LiveConnection(`/ws/display/${encodeURIComponent(token)}/`, options);
  void connection.open();
  return connection;
}
