/**
 * The one API client for this app.
 *
 * Its own module rather than a export from `main.tsx`: `App` needs the client
 * and `main` renders `App`, so putting it in `main` makes the two import each
 * other. That cycle happens to work under Vite today, and would stop working
 * the first time either module did real work at import time.
 */

import { ApiClient } from "@curavia/shared";

export const api = new ApiClient({
  onUnauthenticated: () => {
    // One redirect, however many queries failed at once. Not a router
    // navigation: the session is gone, so the cleanest thing is a fresh load
    // of the sign-in screen with no stale state behind it.
    if (window.location.pathname !== "/sign-in") {
      window.location.assign("/sign-in");
    }
  },
});
