/**
 * The staff application.
 *
 * Two route trees under one router: the signed-out screens, which render on
 * their own with no shell, and the signed-in ones, which render inside
 * `AppShell`. The guard sits between them.
 *
 * Code-based routes rather than file-based: there are a dozen screens and the
 * tree is easier to read in one file than spread across a dozen. When the
 * module count grows this becomes a generated tree; it is not worth the build
 * step yet.
 */

import {
  Outlet,
  RouterProvider,
  createRootRoute,
  createRoute,
  createRouter,
  useRouterState,
} from "@tanstack/react-router";

import { AppShell } from "./AppShell";
import { AuthProvider } from "./auth/AuthProvider";
import { useAuth } from "./auth/useAuth";
import { api } from "./api";
import { Consultation } from "./screens/Consultation";
import { FrontDesk } from "./screens/FrontDesk";
import { GroupDashboard } from "./screens/GroupDashboard";
import { JourneyBoard } from "./screens/JourneyBoard";
import { NoAccess } from "./screens/NoAccess";
import { Pharmacy } from "./screens/Pharmacy";
import { SignIn } from "./screens/auth/SignIn";
import { SignUp } from "./screens/auth/SignUp";
import { VerifyEmail } from "./screens/auth/VerifyEmail";

/** Routes anybody may open, signed in or not. */
const PUBLIC_PATHS = new Set(["/sign-in", "/sign-up", "/verify-email"]);

const rootRoute = createRootRoute({ component: Root });

function Root() {
  return (
    <AuthProvider client={api}>
      <Gate />
    </AuthProvider>
  );
}

/**
 * Decides whether to render the shell, a signed-out screen, or a splash.
 *
 * Rendering is held back until the boot sequence finishes, because a screen
 * drawn before permissions arrive shows an empty list that looks like real,
 * empty data. See AuthProvider.
 */
function Gate() {
  const { status } = useAuth();
  const path = useRouterState({ select: (state) => state.location.pathname });
  const isPublic = PUBLIC_PATHS.has(path);

  if (status === "booting") return <Splash />;

  if (status === "signed-out") {
    if (isPublic) return <Outlet />;
    // No redirect, no history entry: the sign-in screen replaces whatever
    // was asked for, and signing in lands on it.
    return <SignIn />;
  }

  // Signed in. A signed-in person who opens /sign-in gets the app, not a
  // second login form.
  if (isPublic) return <ShellWithLanding />;

  return (
    <AppShell>
      <Outlet />
    </AppShell>
  );
}

/**
 * Where a signed-in person lands when the URL does not say.
 *
 * The server decides — see apps/core/landing.py — so a cashier opens the
 * cashier's screen rather than a dashboard with a cashier's tile on it.
 */
function ShellWithLanding() {
  const { me } = useAuth();
  const Screen = SCREENS[me?.landing ?? "/"] ?? FrontDesk;
  return (
    <AppShell>
      <Screen />
    </AppShell>
  );
}

/** The screens that exist today. Milestone 4 adds the rest. */
const SCREENS: Record<string, () => React.JSX.Element> = {
  "/": FrontDesk,
  "/consultation": Consultation,
  "/pharmacy": Pharmacy,
  "/journey": JourneyBoard,
  "/group": GroupDashboard,
  "/no-access": NoAccess,
};

function Splash() {
  return (
    <div className="flex min-h-full items-center justify-center">
      <div className="flex items-center gap-3">
        <span className="flex size-9 items-center justify-center rounded-lg bg-brand-600 text-lg font-black text-white dark:bg-brand-500 dark:text-slate-950">
          C
        </span>
        <span className="text-slate-500 dark:text-slate-400">Loading your hospital…</span>
      </div>
    </div>
  );
}

const routes = [
  createRoute({ getParentRoute: () => rootRoute, path: "/", component: FrontDesk }),
  createRoute({ getParentRoute: () => rootRoute, path: "/consultation", component: Consultation }),
  createRoute({ getParentRoute: () => rootRoute, path: "/pharmacy", component: Pharmacy }),
  createRoute({ getParentRoute: () => rootRoute, path: "/journey", component: JourneyBoard }),
  createRoute({ getParentRoute: () => rootRoute, path: "/group", component: GroupDashboard }),
  createRoute({ getParentRoute: () => rootRoute, path: "/no-access", component: NoAccess }),
  createRoute({ getParentRoute: () => rootRoute, path: "/sign-in", component: SignIn }),
  createRoute({ getParentRoute: () => rootRoute, path: "/sign-up", component: SignUp }),
  createRoute({ getParentRoute: () => rootRoute, path: "/verify-email", component: VerifyEmail }),
];

const router = createRouter({ routeTree: rootRoute.addChildren(routes) });

declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router;
  }
}

export default function App() {
  return <RouterProvider router={router} />;
}
