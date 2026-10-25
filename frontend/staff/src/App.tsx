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
import { SetupWizard } from "./screens/setup/SetupWizard";
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
      <SetupGate>
        <Outlet />
      </SetupGate>
    </AppShell>
  );
}

/**
 * A hospital that has not finished setup has nothing to show anybody.
 *
 * The front desk of a hospital with no departments, services or prices is a
 * screen full of empty lists that looks broken. So whoever can finish setup
 * is sent to finish it, and everybody else is told plainly that the hospital
 * is not ready — rather than being dropped into the emptiness and left to
 * conclude the product does not work.
 */
function SetupGate({ children }: { children: React.ReactNode }) {
  const { me } = useAuth();
  const path = useRouterState({ select: (state) => state.location.pathname });

  if (!me || me.setup_complete || path === "/setup") return <>{children}</>;

  const canSetUp = me.permissions.includes("org.settings.change");
  return canSetUp ? <SetupWizard /> : <SetupPending />;
}

function SetupPending() {
  const { me } = useAuth();
  return (
    <div className="mx-auto w-full max-w-2xl p-4 sm:p-6">
      <h1 className="text-lg font-semibold text-slate-900 dark:text-white">
        {me?.organisation?.name ?? "This hospital"} is still being set up
      </h1>
      <p className="mt-2 text-sm text-slate-600 dark:text-slate-300">
        Departments, services and prices have not been entered yet, so the screens you work from
        would be empty. Whoever administers Curavia here can finish setting it up — it takes a
        few minutes.
      </p>
    </div>
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
      <SetupGate>
        <Screen />
      </SetupGate>
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
  "/setup": SetupWizard,
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
  createRoute({ getParentRoute: () => rootRoute, path: "/setup", component: SetupWizard }),
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
