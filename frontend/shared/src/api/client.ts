/**
 * The API client both apps use.
 *
 * Three decisions worth knowing about:
 *
 * 1. **The branch header is set here, once.** Every request carries
 *    `X-Curavia-Branch`, so a view never has to remember it. The server
 *    validates it against the user's own memberships and answers 403 if it
 *    does not match — the header is a request, not an instruction.
 * 2. **Errors have one shape.** The backend's exception handler guarantees
 *    `{detail, code, fields?}`, so `ApiRequestError` can carry field errors
 *    straight into react-hook-form without a per-endpoint adapter.
 * 3. **A 401 clears the session once, not per request.** Twelve parallel
 *    queries failing after a session times out should produce one redirect to
 *    the login screen, not twelve.
 */

import type { ApiError, Me } from "./types";

const DEFAULT_BASE_URL = "/api/v1";

export class ApiRequestError extends Error {
  readonly status: number;
  readonly code: string;
  readonly fields: Record<string, string[]>;

  constructor(status: number, body: Partial<ApiError>) {
    super(body.detail ?? "Something went wrong. Please try again.");
    this.name = "ApiRequestError";
    this.status = status;
    this.code = body.code ?? "error";
    this.fields = body.fields ?? {};
  }

  /** True when the server rejected the input rather than the caller. */
  get isValidation(): boolean {
    return this.status === 400 && Object.keys(this.fields).length > 0;
  }

  get isPermission(): boolean {
    return this.status === 403;
  }
}

export interface ApiClientOptions {
  baseUrl?: string;
  /** Called once when the session is no longer valid. */
  onUnauthenticated?: () => void;
}

export class ApiClient {
  private readonly baseUrl: string;
  private readonly onUnauthenticated: (() => void) | undefined;
  private accessToken: string | null = null;
  private branchId: string | null = null;
  private organisationId: string | null = null;
  private unauthenticatedNotified = false;

  constructor(options: ApiClientOptions = {}) {
    this.baseUrl = options.baseUrl ?? DEFAULT_BASE_URL;
    this.onUnauthenticated = options.onUnauthenticated;
  }

  setAccessToken(token: string | null): void {
    this.accessToken = token;
    if (token) this.unauthenticatedNotified = false;
  }

  /**
   * Point every subsequent request at a branch.
   *
   * Called by the top-bar branch switcher. The server decides whether it is
   * allowed; this only states which branch the user chose.
   */
  setScope(organisationId: string | null, branchId: string | null): void {
    this.organisationId = organisationId;
    this.branchId = branchId;
  }

  async request<T>(path: string, init: RequestInit = {}): Promise<T> {
    const headers = new Headers(init.headers);
    headers.set("Accept", "application/json");
    if (init.body && !headers.has("Content-Type")) {
      headers.set("Content-Type", "application/json");
    }
    if (this.accessToken) headers.set("Authorization", `Bearer ${this.accessToken}`);
    if (this.organisationId) headers.set("X-Curavia-Organisation", this.organisationId);
    if (this.branchId) headers.set("X-Curavia-Branch", this.branchId);

    const csrf = readCookie("csrftoken");
    if (csrf && !SAFE_METHODS.has((init.method ?? "GET").toUpperCase())) {
      headers.set("X-CSRFToken", csrf);
    }

    const response = await fetch(`${this.baseUrl}${path}`, {
      ...init,
      headers,
      credentials: "include",
    });

    if (response.status === 401) {
      this.accessToken = null;
      if (!this.unauthenticatedNotified) {
        this.unauthenticatedNotified = true;
        this.onUnauthenticated?.();
      }
      throw new ApiRequestError(401, { detail: "Your session has ended. Please sign in again." });
    }

    if (response.status === 204) return undefined as T;

    const body = await parseJson(response);

    if (!response.ok) {
      throw new ApiRequestError(response.status, body as Partial<ApiError>);
    }
    return body as T;
  }

  get<T>(path: string): Promise<T> {
    return this.request<T>(path);
  }

  post<T>(path: string, body?: unknown): Promise<T> {
    return this.request<T>(path, withBody("POST", body));
  }

  patch<T>(path: string, body: unknown): Promise<T> {
    return this.request<T>(path, withBody("PATCH", body));
  }

  delete<T>(path: string): Promise<T> {
    return this.request<T>(path, { method: "DELETE" });
  }

  /** The boot call: one request for identity, scope, permissions and modules. */
  me(): Promise<Me> {
    return this.get<Me>("/me/");
  }

  switchBranch(branchId: string): Promise<unknown> {
    return this.post("/me/branch/", { branch: branchId });
  }
}

const SAFE_METHODS = new Set(["GET", "HEAD", "OPTIONS", "TRACE"]);

/**
 * Build a request init, omitting `body` entirely when there is none.
 *
 * `exactOptionalPropertyTypes` is on, so `{ body: undefined }` is not the same
 * as leaving `body` out: `RequestInit.body` is declared `BodyInit | null`, and
 * an explicit `undefined` is rejected. Omitting the key is what we actually
 * mean anyway - a POST with no payload should send no body.
 */
function withBody(method: string, body?: unknown): RequestInit {
  if (body === undefined) return { method };
  return { method, body: JSON.stringify(body) };
}

async function parseJson(response: Response): Promise<unknown> {
  const text = await response.text();
  if (!text) return {};
  try {
    return JSON.parse(text);
  } catch {
    // An HTML error page from a proxy, for example. Do not surface the markup.
    return { detail: `The server returned an unexpected response (${response.status}).` };
  }
}

function readCookie(name: string): string | null {
  const match = document.cookie.match(new RegExp(`(^|;\\s*)${name}=([^;]*)`));
  return match?.[2] ? decodeURIComponent(match[2]) : null;
}

/**
 * Does this user hold a permission?
 *
 * The same string codes the backend enforces with, taken from `/me/`. The
 * front end uses these only to decide what to *show*; every endpoint checks
 * again. A menu item that appears when it should not is a bug, but a menu item
 * that is hidden while its endpoint stays open would be a vulnerability.
 */
export function can(permissions: readonly string[], ...codes: string[]): boolean {
  return codes.every((code) => permissions.includes(code));
}

export function hasModule(modules: readonly string[], code: string): boolean {
  return modules.includes(code);
}
