/**
 * The thresholds, tested.
 *
 * Vitest lives in the staff workspace, so the test for the shared thresholds
 * lives here too rather than adding a second test runner to a package that
 * has no other need for one.
 *
 * These four functions decide what turns red on a triage board, and the
 * boundaries are the whole of them — 4 is not 5, 6 is not 7. They are cheap
 * to get wrong in a refactor and expensive to get wrong in a ward, so the
 * boundary on each side of every band is asserted rather than a midpoint.
 */

import { describe, expect, it } from "vitest";

// Imported through the package entry point rather than by file path: these
// are tested as the staff app consumes them, so a change to what the shared
// workspace exports breaks this test too.
import {
  WAIT_CRITICAL_MINUTES,
  WAIT_WARNING_MINUTES,
  minutesSince,
  news2Label,
  news2Tone,
  waitTone,
} from "@curavia/shared";

describe("news2Tone", () => {
  it("treats 0 as routine monitoring", () => {
    expect(news2Tone(0)).toBe("stable");
    expect(news2Label(0)).toBe("Routine monitoring");
  });

  it("escalates at 1, 5 and 7, and not before", () => {
    expect(news2Tone(0)).toBe("stable");
    expect(news2Tone(1)).toBe("warning");
    expect(news2Tone(4)).toBe("warning");
    expect(news2Tone(5)).toBe("urgent");
    expect(news2Tone(6)).toBe("urgent");
    expect(news2Tone(7)).toBe("critical");
  });

  it("stays critical above the top band", () => {
    expect(news2Tone(20)).toBe("critical");
    expect(news2Label(20)).toBe("Emergency response");
  });
});

describe("waitTone", () => {
  it("turns amber exactly at the warning threshold", () => {
    expect(waitTone(WAIT_WARNING_MINUTES - 1)).toBe("stable");
    expect(waitTone(WAIT_WARNING_MINUTES)).toBe("warning");
  });

  it("turns red exactly at the critical threshold", () => {
    expect(waitTone(WAIT_CRITICAL_MINUTES - 1)).toBe("warning");
    expect(waitTone(WAIT_CRITICAL_MINUTES)).toBe("critical");
  });
});

describe("minutesSince", () => {
  const now = new Date("2026-03-01T12:00:00+01:00");

  it("counts whole minutes", () => {
    expect(minutesSince("2026-03-01T11:18:00+01:00", now)).toBe(42);
  });

  it("never returns a negative wait", () => {
    // Clocks disagree. A tablet five minutes fast must not display "-5m".
    expect(minutesSince("2026-03-01T12:05:00+01:00", now)).toBe(0);
  });
});
