import { use } from "react";
import type { Me } from "@curavia/shared";

import { AuthContext } from "./context";
import type { AuthValue } from "./context";

export function useAuth(): AuthValue {
  const value = use(AuthContext);
  if (!value) throw new Error("useAuth was called outside AuthProvider.");
  return value;
}

/** The signed-in profile, for screens that only render when there is one. */
export function useMe(): Me {
  const { me } = useAuth();
  if (!me) throw new Error("useMe was called before the profile loaded.");
  return me;
}
