import { useQuery } from "@tanstack/react-query";
import type { Me } from "@curavia/shared";

import { api } from "./main";

/**
 * Staff app shell.
 *
 * Deliberately thin. It proves the whole chain works end to end - Vite proxy,
 * JWT or session auth, the tenant middleware, the permission engine - by
 * showing what the server says this user may do. The real screens are built
 * from milestone 3 onwards.
 */
export default function App() {
  const { data, isPending, error } = useQuery<Me>({
    queryKey: ["me"],
    queryFn: () => api.me(),
  });

  if (isPending) {
    return <Centred>Loading…</Centred>;
  }

  if (error) {
    return (
      <Centred>
        <h1 className="text-xl font-semibold">Not signed in</h1>
        <p className="mt-2 max-w-prose text-slate-600 dark:text-slate-400">
          The API answered: {(error as Error).message}
        </p>
        <p className="mt-4 text-sm text-slate-500">
          Start the backend with <code>python manage.py runserver</code>, then sign in.
        </p>
      </Centred>
    );
  }

  return (
    <main className="mx-auto max-w-4xl p-4 sm:p-6">
      <header className="mb-6">
        <p className="text-sm font-medium text-brand-600">Curavia staff</p>
        <h1 className="text-2xl font-semibold tracking-tight">
          {data.organisation?.name ?? "No hospital"}
        </h1>
        <p className="mt-1 text-slate-600 dark:text-slate-400">
          {data.full_name}
          {data.active_branch ? ` · ${data.active_branch.name}` : ""}
          {data.has_mfa ? "" : " · two-factor authentication not set up"}
        </p>
      </header>

      <Panel title={`Modules (${data.modules.length})`}>
        <ul className="flex flex-wrap gap-2">
          {data.modules.map((code) => (
            <li
              key={code}
              className="rounded-md bg-brand-50 px-2 py-1 text-xs text-brand-700 dark:bg-slate-800 dark:text-brand-500"
            >
              {code}
            </li>
          ))}
        </ul>
      </Panel>

      <Panel title={`Permissions (${data.permissions.length})`}>
        <ul className="grid grid-cols-1 gap-x-4 text-xs sm:grid-cols-2 lg:grid-cols-3">
          {data.permissions.map((code) => (
            <li key={code} className="truncate py-0.5 font-mono text-slate-600 dark:text-slate-400">
              {code}
            </li>
          ))}
        </ul>
      </Panel>
    </main>
  );
}

function Centred({ children }: { children: React.ReactNode }) {
  return (
    <div className="grid h-full place-items-center p-6 text-center">
      <div>{children}</div>
    </div>
  );
}

function Panel({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="mb-4 rounded-lg border border-slate-200 bg-white p-4 dark:border-slate-800 dark:bg-slate-900">
      <h2 className="mb-3 text-sm font-semibold">{title}</h2>
      {children}
    </section>
  );
}
