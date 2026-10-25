/**
 * The current time, as state that ticks.
 *
 * Every queue and board in this system shows how long someone has been
 * waiting, and reading the clock during render is wrong twice over. React's
 * own lint rule objects — a component that calls `Date.now()` while rendering
 * produces a different result every time it happens to re-render, for reasons
 * unrelated to its props. And it is wrong in practice: a journey board left on
 * a wall display would show the waiting times frozen at the moment the page
 * loaded, quietly, for the rest of the day.
 *
 * So the clock is state, and it ticks. Thirty seconds by default, which is
 * below the resolution anything here displays (whole minutes) and cheap enough
 * to leave running on a screen nobody is touching.
 */

import { useEffect, useState } from "react";

export function useNow(intervalMs = 30_000): Date {
  // Lazy initialiser: it runs once, when the state is created, rather than on
  // every render.
  const [now, setNow] = useState(() => new Date());

  useEffect(() => {
    const timer = setInterval(() => setNow(new Date()), intervalMs);
    return () => clearInterval(timer);
  }, [intervalMs]);

  return now;
}

/** Whole minutes between an ISO timestamp and `now`, never negative. */
export function minutesSince(iso: string, now: Date): number {
  return Math.max(0, Math.floor((now.getTime() - new Date(iso).getTime()) / 60_000));
}
