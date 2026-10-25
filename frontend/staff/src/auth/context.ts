/**
 * The auth context object, on its own.
 *
 * Separated from the provider and the hooks only so each file exports one
 * kind of thing: Fast Refresh cannot hot-reload a module that exports both a
 * component and a value, and it silently stops reloading the whole module
 * rather than telling you.
 */

import { createContext } from "react";
import type { AuthApi, Me } from "@curavia/shared";

export type AuthStatus = "booting" | "signed-out" | "signed-in";

export interface AuthValue {
  status: AuthStatus;
  me: Me | null;
  auth: AuthApi;
  /** Re-read `/me` after something that changes it: a branch switch, setup. */
  reload: () => Promise<Me>;
  signOut: () => Promise<void>;
  /**
   * Called by the sign-in screens once tokens are in hand. Returns the
   * profile so the caller can send the person to `me.landing` — the server
   * decides where each role opens, and the client has to wait for the answer
   * before it can navigate.
   */
  onSignedIn: () => Promise<Me>;
}

export const AuthContext = createContext<AuthValue | null>(null);
