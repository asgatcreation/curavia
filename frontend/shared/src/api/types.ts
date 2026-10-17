/**
 * Hand-written types for the few shapes the app shell needs before the
 * generated ones exist.
 *
 * Everything else comes from `npm run types`, which generates
 * `generated.ts` from the backend's OpenAPI schema. That file is gitignored
 * on purpose: a committed copy drifts from the API silently, and CI
 * regenerates it to prove the two still agree.
 */

export interface Branch {
  id: string;
  name: string;
  code: string;
  address: string;
  state: string;
  lga: string;
  phone: string;
  is_main: boolean;
}

export interface Organisation {
  id: string;
  name: string;
  slug: string;
  status: string;
  size_mode: "solo" | "clinic" | "hospital";
  modules: string[];
  is_read_only: boolean;
  share_records_across_branches: boolean;
}

export interface Membership {
  organisation_id: string;
  organisation_name: string;
  job_title: string;
  roles: string[];
  branches: Branch[];
}

/** The single boot payload: who you are, where you are, what you may do. */
export interface Me {
  id: string;
  email: string;
  full_name: string;
  first_name: string;
  last_name: string;
  phone: string;
  is_platform_staff: boolean;
  has_mfa: boolean;
  organisation: Organisation | null;
  active_branch: Branch | null;
  memberships: Membership[];
  permissions: string[];
  modules: string[];
}

export interface ApiError {
  detail: string;
  code: string;
  fields?: Record<string, string[]>;
}

export interface Paginated<T> {
  count: number;
  page: number;
  pages: number;
  page_size: number;
  next: string | null;
  previous: string | null;
  results: T[];
}
