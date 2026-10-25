/**
 * Sign in: password, then the six-digit code.
 *
 * One component for both steps rather than two routes. The MFA step is not a
 * separate decision the user makes — it is the second half of the same
 * action — and putting it on its own URL means a reload between the two
 * halves loses the challenge and makes them start again.
 *
 * The code field is `inputMode="numeric"` with `autoComplete="one-time-code"`,
 * so a phone offers the number pad and iOS offers to fill the code from the
 * notification. Small, and it is the difference between two taps and eight.
 */

import { useState } from "react";
import { useNavigate } from "@tanstack/react-router";
import { ApiRequestError, Button, cn, inputClass } from "@curavia/shared";

import { useAuth } from "../../auth/useAuth";
import { AuthLayout, FormError } from "./AuthLayout";

export function SignIn() {
  const { auth, onSignedIn } = useAuth();
  const navigate = useNavigate();

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [challenge, setChallenge] = useState<string | null>(null);
  const [code, setCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submitPassword(event: React.FormEvent) {
    event.preventDefault();
    setError(null);
    setBusy(true);
    try {
      const result = await auth.login(email.trim(), password);
      if (result.mfa_required && result.challenge) {
        setChallenge(result.challenge);
        setPassword(""); // Not kept in memory once it has been used.
        return;
      }
      const me = await onSignedIn();
      // Where this role opens is the server's decision, not the client's.
      await navigate({ to: me.landing });
    } catch (caught) {
      setError(messageFrom(caught));
    } finally {
      setBusy(false);
    }
  }

  async function submitCode(event: React.FormEvent) {
    event.preventDefault();
    if (!challenge) return;
    setError(null);
    setBusy(true);
    try {
      await auth.submitMfaCode(challenge, code.trim());
      const me = await onSignedIn();
      await navigate({ to: me.landing });
    } catch (caught) {
      setError(messageFrom(caught));
      setCode("");
    } finally {
      setBusy(false);
    }
  }

  if (challenge) {
    return (
      <AuthLayout
        title="Enter your code"
        blurb="Open your authenticator app and type the six digits it is showing."
        footer={
          <button
            type="button"
            className="font-semibold text-brand-700 underline dark:text-brand-300"
            onClick={() => {
              setChallenge(null);
              setCode("");
              setError(null);
            }}
          >
            Use a different account
          </button>
        }
      >
        <form onSubmit={(event) => void submitCode(event)} className="space-y-4">
          <FormError message={error} />
          <label className="block">
            <span className="mb-1 block text-xs font-semibold tracking-wide text-slate-600 uppercase dark:text-slate-400">
              Six-digit code
            </span>
            <input
              autoFocus
              value={code}
              onChange={(event) => setCode(event.target.value)}
              inputMode="numeric"
              autoComplete="one-time-code"
              maxLength={10}
              placeholder="000000"
              className={cn(inputClass, "numeric text-center text-xl tracking-[0.4em]")}
            />
            <span className="mt-1 block text-xs text-slate-500 dark:text-slate-400">
              Lost your phone? A recovery code works here too.
            </span>
          </label>
          <Button type="submit" variant="primary" size="lg" disabled={busy} className="w-full">
            {busy ? "Checking…" : "Sign in"}
          </Button>
        </form>
      </AuthLayout>
    );
  }

  return (
    <AuthLayout
      title="Sign in"
      blurb="Your hospital's own Curavia."
      footer={
        <>
          No account yet?{" "}
          <a className="font-semibold text-brand-700 underline dark:text-brand-300" href="/sign-up">
            Set up your hospital
          </a>
        </>
      }
    >
      <form onSubmit={(event) => void submitPassword(event)} className="space-y-4">
        <FormError message={error} />
        <label className="block">
          <span className="mb-1 block text-xs font-semibold tracking-wide text-slate-600 uppercase dark:text-slate-400">
            Email
          </span>
          <input
            autoFocus
            type="email"
            autoComplete="username"
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            className={inputClass}
          />
        </label>
        <label className="block">
          <span className="mb-1 block text-xs font-semibold tracking-wide text-slate-600 uppercase dark:text-slate-400">
            Password
          </span>
          <input
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            className={inputClass}
          />
        </label>
        <Button type="submit" variant="primary" size="lg" disabled={busy} className="w-full">
          {busy ? "Signing in…" : "Sign in"}
        </Button>
      </form>
    </AuthLayout>
  );
}

function messageFrom(caught: unknown): string {
  if (caught instanceof ApiRequestError) return caught.message;
  return "Could not reach the server. Check your connection and try again.";
}
