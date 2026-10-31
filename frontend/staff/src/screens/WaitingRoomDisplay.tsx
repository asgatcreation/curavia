/**
 * The television in the waiting room.
 *
 * Not a staff screen. Nobody signs in, nobody touches it, and it is read
 * from across a room by people who may be anxious, unwell, or holding a
 * child. Everything about it follows from that:
 *
 * **No names, ever.** The server will not send any — see `DisplayScreen` on
 * the backend — and this screen could not show one if it tried. Everybody in
 * the room can read this wall, including the people in the queue and whoever
 * walked in off the street.
 *
 * **Enormous type.** The called number is the largest thing in the building.
 * A patient should be able to read it from the far end of a corridor without
 * glasses.
 *
 * **It renders outside the app shell**, with no navigation and no theme
 * switch, because there is nothing to navigate to and nobody to switch it.
 *
 * **It says when it has lost the connection.** A frozen television is how a
 * waiting room full of people miss their turn, so the screen stops claiming
 * to be current the moment it stops being current — stated in words a
 * patient understands, not a technical status.
 */

import { useEffect, useRef, useState } from "react";
import { useParams } from "@tanstack/react-router";
import type { DisplayPayload, LiveState } from "@curavia/shared";
import { JourneyApi, LiveConnection, connectDisplay, useNow } from "@curavia/shared";

import { api } from "../api";

const journey = new JourneyApi(api);

/** After this long with no word from the server, say so on the wall. */
const STALE_SECONDS = 90;

export function WaitingRoomDisplay() {
  const { token } = useParams({ strict: false });
  const screenToken = token ?? "";

  const [payload, setPayload] = useState<DisplayPayload | null>(null);
  const [state, setState] = useState<LiveState>("connecting");
  const [updatedAt, setUpdatedAt] = useState<Date | null>(null);
  const [missing, setMissing] = useState(false);
  const connection = useRef<LiveConnection | null>(null);

  // A clock of its own, so "last updated" ages on screen even when nothing
  // arrives. A display whose staleness only updates when data arrives can
  // never tell you that data has stopped arriving — and reading the time
  // during render rather than from this hook is the frozen-clock bug that
  // `useNow` exists to prevent.
  const now = useNow(5_000);

  useEffect(() => {
    if (!screenToken) return;

    const load = () => {
      void journey
        .display(screenToken)
        .then((result) => {
          setPayload(result);
          setUpdatedAt(new Date());
        })
        .catch(() => setMissing(true));
    };
    load();

    const live = connectDisplay(screenToken, {
      onState: (next) => {
        setState(next);
        if (next === "live") load();
      },
      onMessage: (message) => {
        if (message["type"] === "display") {
          setPayload(message as unknown as DisplayPayload);
          setUpdatedAt(new Date());
        }
      },
    });
    connection.current = live;
    return () => {
      live.close();
      connection.current = null;
    };
  }, [screenToken]);

  // Clamped at zero: the interval clock can be a few seconds behind the
  // moment the data arrived, and a negative age on a wall is nonsense.
  const ageSeconds = updatedAt
    ? Math.max(Math.floor((now.getTime() - updatedAt.getTime()) / 1000), 0)
    : null;
  const stale = state === "offline" || (ageSeconds !== null && ageSeconds > STALE_SECONDS);

  if (missing) {
    return (
      <Shell>
        <p className="text-center text-4xl font-bold">This screen is not set up.</p>
        <p className="mt-4 text-center text-2xl opacity-70">
          Please ask at the desk.
        </p>
      </Shell>
    );
  }

  if (!payload) {
    return (
      <Shell>
        <p className="text-center text-4xl font-bold opacity-70">Starting…</p>
      </Shell>
    );
  }

  const [current, ...recent] = payload.now_serving;

  return (
    <Shell>
      <header className="flex flex-wrap items-baseline justify-between gap-4">
        <h1 className="text-3xl font-bold tracking-wide">{payload.screen}</h1>
        {stale && (
          // Said plainly, for a patient rather than an engineer.
          <p className="rounded-full bg-amber-500 px-5 py-2 text-xl font-bold text-black">
            Not updating — please ask at the desk
          </p>
        )}
      </header>

      <section className="mt-8 flex-1" aria-live="polite" aria-atomic="true">
        <p className="text-2xl tracking-[0.2em] uppercase opacity-60">Now serving</p>
        {current ? (
          <div className="mt-2">
            {/* The largest thing in the building — and it must never wrap.
                A token split as "A-" above "001" is two numbers to somebody
                reading it from the back of a room, so the size follows the
                viewport rather than being fixed: hospitals hang these on
                whatever screen they have, including portrait ones and
                old 4:3 sets. */}
            <p className="numeric text-[clamp(4rem,19vw,14rem)] leading-none font-black whitespace-nowrap tabular-nums">
              {current.label}
            </p>
            <p className="mt-2 text-[clamp(1.5rem,5vw,3rem)] font-bold">
              {current.called_to || current.department}
            </p>
          </div>
        ) : (
          <p className="mt-6 text-6xl font-bold opacity-50">Please wait</p>
        )}

        {recent.length > 0 && (
          <div className="mt-10">
            <p className="text-xl tracking-[0.2em] uppercase opacity-50">Called before</p>
            <ul className="mt-2 flex flex-wrap gap-x-10 gap-y-2">
              {recent.map((entry) => (
                <li
                  key={entry.label}
                  className="numeric text-[clamp(1.75rem,5vw,3rem)] font-bold whitespace-nowrap opacity-60"
                >
                  {entry.label}
                  <span className="ml-3 text-2xl font-medium opacity-70">
                    {entry.called_to || entry.department}
                  </span>
                </li>
              ))}
            </ul>
          </div>
        )}
      </section>

      <footer className="mt-8 border-t border-white/20 pt-6">
        {payload.waiting.length > 0 && (
          <ul className="flex flex-wrap gap-x-12 gap-y-3">
            {payload.waiting.map((row) => (
              <li key={row.department} className="text-3xl">
                <span className="opacity-60">{row.department}</span>{" "}
                <span className="numeric font-bold">{row.count}</span>{" "}
                <span className="opacity-60">waiting</span>
              </li>
            ))}
          </ul>
        )}
        {payload.footer && (
          <p className="mt-6 text-2xl opacity-70">{payload.footer}</p>
        )}
      </footer>
    </Shell>
  );
}

/**
 * Always dark, whatever the browser's theme.
 *
 * A television is usually a bright screen in a room with the lights on and
 * nobody to adjust it; a white wall of light is glare from across the room,
 * and a screen that followed the system theme would be a different colour on
 * every set the hospital bought.
 */
function Shell({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex min-h-screen flex-col bg-slate-950 p-10 text-white">{children}</div>
  );
}
