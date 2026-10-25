/**
 * Who is signed in, for the whole app.
 *
 * One boot sequence, in this order, and the order matters:
 *
 *   1. Try to refresh. There may be a twelve-hour refresh token in storage
 *      from earlier in the shift.
 *   2. If that worked, call `/me` — identity, scope, permissions, modules and
 *      where to land, in one request.
 *   3. Point the API client at the hospital and branch `/me` reported, so
 *      every later request carries them.
 *
 * Until step 3 finishes the app renders a splash rather than a screen. A
 * half-booted staff app is worse than a slow one: a nurse who sees an empty
 * patient list because permissions have not arrived yet will believe the list
 * is empty.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { AuthApi, readRefreshToken } from "@curavia/shared";
import type { ApiClient, Me } from "@curavia/shared";

import { AuthContext } from "./context";
import type { AuthStatus, AuthValue } from "./context";

export function AuthProvider({ client, children }: { client: ApiClient; children: ReactNode }) {
  // Lazy initialiser, not an effect. With no refresh token there is nothing
  // to await and the answer is known at first render; setting it from inside
  // an effect would render a splash, then immediately re-render as signed
  // out, which React's lint rule objects to and which flashes on screen.
  const [status, setStatus] = useState<AuthStatus>(() =>
    readRefreshToken() ? "booting" : "signed-out",
  );
  const [me, setMe] = useState<Me | null>(null);

  const auth = useMemo(() => new AuthApi(client), [client]);

  // Let the client refresh and retry on a 401, rather than throwing the user
  // out the first time an access token ages out mid-shift.
  useEffect(() => {
    client.setTokenRefresher(() => auth.refresh());
    return () => client.setTokenRefresher(null);
  }, [auth, client]);

  const load = useCallback(async (): Promise<Me> => {
    const profile = await client.me();
    // Before anything else reads it: every subsequent request must carry the
    // hospital and branch this profile says the user is working in.
    client.setScope(profile.organisation?.id ?? null, profile.active_branch?.id ?? null);
    setMe(profile);
    setStatus("signed-in");
    return profile;
  }, [client]);

  const boot = useCallback(async () => {
    const refreshed = await auth.refresh();
    if (!refreshed) {
      setStatus("signed-out");
      return;
    }
    try {
      await load();
    } catch {
      // A valid token for a user the server will not describe — deactivated
      // mid-shift, for instance. Treat it as signed out rather than looping.
      setStatus("signed-out");
    }
  }, [auth, load]);

  useEffect(() => {
    // Only when there was a token to exchange; see the state initialiser.
    //
    // The rule disabled below objects because it can see `setStatus` inside
    // `boot`. Nothing is set synchronously: `boot` is async and does not
    // touch state until after a network round trip, so there is no cascading
    // render to avoid. This is the "subscribe to an external system on mount"
    // case the rule's own documentation allows, and the external system is
    // the API.
    //
    // `status` is left out of the dependencies deliberately: this runs once
    // on mount, not again each time the status changes — which `boot` itself
    // causes.
    //
    // eslint-disable-next-line react-hooks/set-state-in-effect
    if (status === "booting") void boot();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [boot]);

  const signOut = useCallback(async () => {
    await auth.logout();
    setMe(null);
    client.setScope(null, null);
    setStatus("signed-out");
  }, [auth, client]);

  const value = useMemo<AuthValue>(
    () => ({
      status,
      me,
      auth,
      reload: load,
      signOut,
      onSignedIn: load,
    }),
    [status, me, auth, load, signOut],
  );

  return <AuthContext value={value}>{children}</AuthContext>;
}
