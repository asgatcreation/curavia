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
  useParams,
  useRouterState,
} from "@tanstack/react-router";

import { AppShell } from "./AppShell";
import { AuthProvider } from "./auth/AuthProvider";
import { useAuth } from "./auth/useAuth";
import { api } from "./api";
import { Appointments } from "./screens/Appointments";
import { Consultation } from "./screens/Consultation";
import { FrontDesk } from "./screens/FrontDesk";
import { GroupDashboard } from "./screens/GroupDashboard";
import { ImportData } from "./screens/ImportData";
import { JourneyBoard } from "./screens/JourneyBoard";
import { NoAccess } from "./screens/NoAccess";
import { NotBuiltYet } from "./screens/NotBuiltYet";
import { PatientRecord } from "./screens/PatientRecord";
import { PLANNED } from "./screens/planned";
import { Pharmacy } from "./screens/Pharmacy";
import { RegisterPatient } from "./screens/RegisterPatient";
import { StaffAndRoles } from "./screens/StaffAndRoles";
import { AcceptInvitation } from "./screens/auth/AcceptInvitation";
import { SetupWizard } from "./screens/setup/SetupWizard";
import { SignIn } from "./screens/auth/SignIn";
import { SignUp } from "./screens/auth/SignUp";
import { VerifyEmail } from "./screens/auth/VerifyEmail";

/**
 * Signed-out routes, split by what should happen when somebody IS signed in.
 *
 * `GUEST_ONLY` are the two that mean "I have no account": a signed-in person
 * who opens them wants the app, not a second login form.
 *
 * `TOKEN_DRIVEN` are opened from a link in an email, and the link decides who
 * they are for — which may not be the person already signed in on this
 * machine. A nurse clicking her invitation on the ward's shared computer,
 * where the matron is still signed in, must see her invitation. Sending her
 * to the matron's dashboard instead is how two people end up sharing one
 * login.
 */
const GUEST_ONLY = new Set(["/sign-in", "/sign-up"]);
const TOKEN_DRIVEN = new Set(["/verify-email", "/accept-invitation"]);

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

  // Rendered the same way whether or not anybody is signed in, and before
  // the boot sequence finishes: the token in the URL is the identity that
  // matters on these pages.
  if (TOKEN_DRIVEN.has(path)) return <Outlet />;

  if (status === "booting") return <Splash />;

  if (status === "signed-out") {
    if (GUEST_ONLY.has(path)) return <Outlet />;
    // No redirect, no history entry: the sign-in screen replaces whatever
    // was asked for, and signing in lands on it.
    return <SignIn />;
  }

  // Signed in. A signed-in person who opens /sign-in gets the app, not a
  // second login form.
  if (GUEST_ONLY.has(path)) return <ShellWithLanding />;

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
  "/appointments": Appointments,
  "/consultation": Consultation,
  "/pharmacy": Pharmacy,
  "/journey": JourneyBoard,
  "/group": GroupDashboard,
  "/no-access": NoAccess,
  "/setup": SetupWizard,
  "/staff": StaffAndRoles,
  "/import": ImportData,
  "/register-patient": RegisterPatient,
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

function PatientRecordRoute() {
  // `strict: false` rather than a typed route object: the route tree is
  // built as an array below, so there is no named route to hang
  // `useParams` off without splitting the array apart.
  const { patientId } = useParams({ strict: false });
  return <PatientRecord patientId={patientId ?? ""} />;
}

const routes = [
  createRoute({ getParentRoute: () => rootRoute, path: "/", component: FrontDesk }),
  createRoute({ getParentRoute: () => rootRoute, path: "/appointments", component: Appointments }),
  createRoute({ getParentRoute: () => rootRoute, path: "/consultation", component: Consultation }),
  createRoute({ getParentRoute: () => rootRoute, path: "/pharmacy", component: Pharmacy }),
  createRoute({ getParentRoute: () => rootRoute, path: "/journey", component: JourneyBoard }),
  createRoute({ getParentRoute: () => rootRoute, path: "/group", component: GroupDashboard }),
  createRoute({ getParentRoute: () => rootRoute, path: "/no-access", component: NoAccess }),
  createRoute({ getParentRoute: () => rootRoute, path: "/setup", component: SetupWizard }),
  createRoute({ getParentRoute: () => rootRoute, path: "/staff", component: StaffAndRoles }),
  createRoute({ getParentRoute: () => rootRoute, path: "/import", component: ImportData }),
  // The only route with a parameter so far. The id is in the path rather
  // than a query string because this URL gets pasted into a message between
  // two members of staff, and a path reads as a record while `?id=` reads as
  // a search somebody might edit.
  createRoute({
    getParentRoute: () => rootRoute,
    path: "/patients/$patientId",
    component: PatientRecordRoute,
  }),
  createRoute({
    getParentRoute: () => rootRoute,
    path: "/register-patient",
    component: RegisterPatient,
  }),
  createRoute({
    getParentRoute: () => rootRoute,
    path: "/accept-invitation",
    component: AcceptInvitation,
  }),
  createRoute({ getParentRoute: () => rootRoute, path: "/sign-in", component: SignIn }),
  createRoute({ getParentRoute: () => rootRoute, path: "/sign-up", component: SignUp }),
  createRoute({ getParentRoute: () => rootRoute, path: "/verify-email", component: VerifyEmail }),
  // Real routes for the screens that are on the plan but not built, so a
  // direct link works and the person is told what it will be rather than
  // seeing Not Found.
  ...Object.keys(PLANNED).map((path) =>
    createRoute({
      getParentRoute: () => rootRoute,
      path,
      component: () => <NotBuiltYet path={path} />,
    }),
  ),
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
