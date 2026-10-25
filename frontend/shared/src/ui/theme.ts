/**
 * Light / dark, with the operating system as the default.
 *
 * Three states, not two: "system" is a real choice and the one most people
 * leave it on. A hospital that runs day staff on bright screens and night
 * staff on dim ones gets the right answer from the OS without anybody
 * touching a setting.
 *
 * The choice is written to `data-theme` on <html>, which is what the Tailwind
 * dark variant and the marketing site's own CSS both key on, so the two stay
 * in step. localStorage is wrapped because it throws in a private window and
 * the page must still render.
 */

import { useCallback, useEffect, useState } from "react";

export type ThemeChoice = "light" | "dark" | "system";

const STORAGE_KEY = "curavia.theme";

function read(): ThemeChoice {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored === "light" || stored === "dark" || stored === "system") return stored;
  } catch {
    // Private window, or site data blocked. The OS preference is fine.
  }
  return "system";
}

function apply(choice: ThemeChoice): void {
  const root = document.documentElement;
  if (choice === "system") {
    root.removeAttribute("data-theme");
  } else {
    root.setAttribute("data-theme", choice);
  }
}

export function useTheme(): [ThemeChoice, (next: ThemeChoice) => void] {
  const [choice, setChoice] = useState<ThemeChoice>(read);

  useEffect(() => {
    apply(choice);
  }, [choice]);

  const set = useCallback((next: ThemeChoice) => {
    setChoice(next);
    try {
      localStorage.setItem(STORAGE_KEY, next);
    } catch {
      // Not worth failing over: the choice still applies for this visit.
    }
  }, []);

  return [choice, set];
}
