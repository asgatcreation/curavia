/**
 * Authentication: the calls, and where the tokens live.
 *
 * **Where the tokens live, and the honest trade-off.**
 *
 * The access token is kept in memory only. It never touches storage, so it
 * cannot be read from another tab, survive a reload, or be lifted out of a
 * backup of the machine. It lasts fifteen minutes.
 *
 * The refresh token is kept in `localStorage`. That is the part worth being
 * clear about: script injected into this origin could read it. The
 * alternatives were considered and each is worse here —
 *
 *   - In memory only: every reload signs the user out. A ward tablet that
 *     reloads when it wakes would log out the nurse mid-shift, and staff
 *     would respond by never locking the screen.
 *   - An httpOnly cookie: the right answer, and where this should end up, but
 *     it needs the refresh endpoint to set and read a cookie, CSRF protection
 *     on it, and a same-site story for the staff app and the API being on
 *     different hosts in production. That is milestone 14's hardening work,
 *     not a line in a client.
 *
 * The mitigations that make this acceptable in the meantime: the refresh
 * token is single-use (the server rotates and blacklists on every refresh, so
 * a stolen one stops working the moment the real user refreshes), it expires
 * after twelve hours, signing out blacklists it immediately, and "sign out
 * everywhere" ends every session for the user. The real defence against the
 * attack this exposes is not storing it elsewhere — it is that there is no
 * injected script, which is what the Content-Security-Policy is for.
 */

import type { ApiClient } from "./client";

const REFRESH_KEY = "curavia.refresh";

export interface LoginResult {
  /** True when the password was right but a six-digit code is still needed. */
  mfa_required: boolean;
  /** Present only when `mfa_required` — pass it back with the code. */
  challenge?: string;
  access?: string;
  refresh?: string;
  /** False when the account has no authenticator enrolled yet. */
  mfa_enrolled?: boolean;
}

export interface SignUpInput {
  email: string;
  password: string;
  first_name: string;
  last_name: string;
  phone?: string;
  hospital_name: string;
  size_mode: "solo" | "clinic" | "hospital";
  state?: string;
}

/**
 * Read the stored refresh token.
 *
 * Every access is wrapped: `localStorage` throws in a private window and when
 * site data is blocked, and the app has to render either way — the person
 * simply has to sign in again.
 */
export function readRefreshToken(): string | null {
  try {
    return localStorage.getItem(REFRESH_KEY);
  } catch {
    return null;
  }
}

export function writeRefreshToken(token: string | null): void {
  try {
    if (token === null) localStorage.removeItem(REFRESH_KEY);
    else localStorage.setItem(REFRESH_KEY, token);
  } catch {
    // Storage unavailable. The session still works until this tab closes.
  }
}

export class AuthApi {
  constructor(private readonly client: ApiClient) {}

  /** Step one. Returns tokens, or an MFA challenge. */
  async login(email: string, password: string): Promise<LoginResult> {
    const result = await this.client.post<LoginResult>("/auth/login/", { email, password });
    this.adopt(result);
    return result;
  }

  /** Step two: the six-digit code, or a recovery code. */
  async submitMfaCode(challenge: string, code: string): Promise<LoginResult> {
    const result = await this.client.post<LoginResult>("/auth/login/mfa/", { challenge, code });
    this.adopt(result);
    return result;
  }

  /**
   * Exchange the stored refresh token for a new access token.
   *
   * Returns false rather than throwing when there is nothing to exchange or
   * the token has expired: "not signed in" is a normal state on boot, not an
   * error to report.
   */
  async refresh(): Promise<boolean> {
    const refresh = readRefreshToken();
    if (!refresh) return false;
    try {
      const result = await this.client.post<LoginResult>("/auth/refresh/", { refresh });
      this.adopt(result);
      return Boolean(result.access);
    } catch {
      writeRefreshToken(null);
      this.client.setAccessToken(null);
      return false;
    }
  }

  async logout(): Promise<void> {
    const refresh = readRefreshToken();
    try {
      await this.client.post("/auth/logout/", refresh ? { refresh } : undefined);
    } finally {
      // Local state is cleared whether or not the server could be reached.
      // A user who pressed "sign out" is signed out of this device, and the
      // token is single-use anyway.
      writeRefreshToken(null);
      this.client.setAccessToken(null);
    }
  }

  signUp(input: SignUpInput): Promise<{ detail: string; code: string }> {
    return this.client.post("/onboarding/sign-up/", input);
  }

  verifyEmail(token: string): Promise<{ detail: string; code: string }> {
    return this.client.post("/onboarding/verify-email/", { token });
  }

  resendVerification(email: string): Promise<{ detail: string; code: string }> {
    return this.client.post("/onboarding/verify-email/resend/", { email });
  }

  /** Keep the tokens a response carried, if it carried any. */
  private adopt(result: LoginResult): void {
    if (result.access) this.client.setAccessToken(result.access);
    // The server rotates refresh tokens, so a refresh response carries a new
    // one and the old one has already been blacklisted. Storing it is not
    // optional: miss it and the next refresh fails.
    if (result.refresh) writeRefreshToken(result.refresh);
  }
}
