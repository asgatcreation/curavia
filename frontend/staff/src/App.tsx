/**
 * The staff application.
 *
 * Code-based routes rather than file-based: there are five screens at
 * milestone 3 and the route tree is easier to read in one file than spread
 * across five. When the module count grows past a dozen this becomes a
 * generated tree; it is not worth the build step yet.
 */

import { Outlet, RouterProvider, createRootRoute, createRoute, createRouter } from "@tanstack/react-router";

import { AppShell } from "./AppShell";
import { Consultation } from "./screens/Consultation";
import { FrontDesk } from "./screens/FrontDesk";
import { GroupDashboard } from "./screens/GroupDashboard";
import { JourneyBoard } from "./screens/JourneyBoard";
import { Pharmacy } from "./screens/Pharmacy";

const rootRoute = createRootRoute({
  component: () => (
    <AppShell>
      <Outlet />
    </AppShell>
  ),
});

const routes = [
  createRoute({ getParentRoute: () => rootRoute, path: "/", component: FrontDesk }),
  createRoute({ getParentRoute: () => rootRoute, path: "/consultation", component: Consultation }),
  createRoute({ getParentRoute: () => rootRoute, path: "/pharmacy", component: Pharmacy }),
  createRoute({ getParentRoute: () => rootRoute, path: "/journey", component: JourneyBoard }),
  createRoute({ getParentRoute: () => rootRoute, path: "/group", component: GroupDashboard }),
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
