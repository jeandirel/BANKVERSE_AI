"use client";

import { useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState, type FormEvent } from "react";
import { BrandMark } from "@/components/shell/app-shell";
import { Notice } from "@/components/ui/layout";
import { Button, Field, Input } from "@/components/ui/primitives";
import { ApiError, post } from "@/lib/api";
import { useI18n, useL } from "@/lib/i18n";

type LoginResp =
  | { status: "ok" }
  | { status: "mfa_required"; mfa_token: string }
  | { status: "mfa_setup_required"; setup_token: string };

const DEMO_ROLES = [
  { role: "fraud_analyst", fr: "Analyste fraude", en: "Fraud analyst" },
  { role: "aml_analyst", fr: "Analyste LCB-FT", en: "AML analyst" },
  { role: "credit_analyst", fr: "Analyste crédit", en: "Credit analyst" },
  { role: "risk_manager", fr: "Responsable des risques", en: "Risk manager" },
  { role: "data_scientist", fr: "Data scientist", en: "Data scientist" },
  { role: "auditor", fr: "Auditeur", en: "Auditor" },
];

function LoginInner() {
  const { t, locale, setLocale } = useI18n();
  const L = useL();
  const router = useRouter();
  const params = useSearchParams();
  const qc = useQueryClient();
  const next = params.get("next")?.startsWith("/app") ? params.get("next")! : "/app";
  const [step, setStep] = useState<"password" | "mfa" | "setup">("password");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [orgSlug, setOrgSlug] = useState("");
  const [code, setCode] = useState("");
  const [token, setToken] = useState("");
  const [setup, setSetup] = useState<{ qr_svg: string; secret: string } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const done = () => {
    qc.clear();
    router.replace(next);
  };

  const submitPassword = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const r = await post<LoginResp>("/auth/login", { email, password, org_slug: orgSlug || null });
      if (r.status === "ok") return done();
      if (r.status === "mfa_required") {
        setToken(r.mfa_token);
        setStep("mfa");
      } else {
        setToken(r.setup_token);
        const s = await post<{ qr_svg: string; secret: string }>("/auth/mfa/setup/start", { setup_token: r.setup_token });
        setSetup(s);
        setStep("setup");
      }
    } catch (err) {
      setError(err instanceof ApiError && err.status === 401 ? t("auth.error.invalid") : err instanceof Error ? err.message : t("common.error"));
    } finally {
      setBusy(false);
    }
  };

  const submitCode = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      if (step === "mfa") await post("/auth/mfa/verify", { mfa_token: token, code });
      else await post("/auth/mfa/setup/confirm", { setup_token: token, code });
      done();
    } catch {
      setError(t("auth.error.mfa"));
    } finally {
      setBusy(false);
    }
  };

  const demo = async (role: string) => {
    setBusy(true);
    setError(null);
    try {
      await post("/auth/demo-session", { role });
      done();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("common.error"));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="min-h-screen grid lg:grid-cols-[minmax(420px,520px)_1fr]">
      <div className="flex flex-col px-6 sm:px-12 py-8 bg-surface border-r border-line">
        <div className="flex items-center justify-between">
          <Link href="/" className="flex items-center gap-2"><BrandMark /><span className="font-semibold tracking-tight">BANKVERSE</span></Link>
          <button className="text-[13px] text-ink-2 hover:text-ink" onClick={() => setLocale(locale === "fr" ? "en" : "fr")}>{t("shell.language")}</button>
        </div>
        <div className="my-auto py-10 w-full max-w-sm">
          {step === "password" && (
            <form onSubmit={submitPassword} className="flex flex-col gap-4">
              <div>
                <h1 className="page-title text-[28px]">{t("auth.title")}</h1>
                <p className="mt-1 text-sm text-ink-2">{t("auth.subtitle")}</p>
              </div>
              <Field label={t("auth.email")} htmlFor="email">
                <Input id="email" type="email" autoComplete="username" required value={email} onChange={(e) => setEmail(e.target.value)} />
              </Field>
              <Field label={t("auth.password")} htmlFor="password">
                <Input id="password" type="password" autoComplete="current-password" required value={password} onChange={(e) => setPassword(e.target.value)} />
              </Field>
              <Field label={t("auth.org")} htmlFor="org" hint={L("Identifiant court, si vous appartenez à plusieurs établissements.", "Short identifier, if you belong to several institutions.")}>
                <Input id="org" value={orgSlug} onChange={(e) => setOrgSlug(e.target.value.toLowerCase())} placeholder="banque-alpha" />
              </Field>
              {error && <p role="alert" className="text-sm text-risk-critical">{error}</p>}
              <Button type="submit" variant="primary" loading={busy}>{t("auth.submit")}</Button>
              <Button type="button" variant="ghost" disabled title={t("auth.sso.unconfigured")}>{t("auth.sso")}</Button>
              <p className="text-xs text-ink-3 -mt-2">{t("auth.sso.unconfigured")}</p>
            </form>
          )}
          {step !== "password" && (
            <form onSubmit={submitCode} className="flex flex-col gap-4">
              <div>
                <h1 className="page-title text-[26px]">{step === "mfa" ? t("auth.mfa.title") : t("auth.mfa.setup.title")}</h1>
                <p className="mt-1 text-sm text-ink-2">{step === "mfa" ? t("auth.mfa.help") : t("auth.mfa.setup.help")}</p>
              </div>
              {step === "setup" && setup && (
                <div className="flex flex-col items-start gap-2">
                  <div className="rounded border border-line bg-white p-2" dangerouslySetInnerHTML={{ __html: setup.qr_svg }} />
                  <p className="text-xs text-ink-3">{t("auth.mfa.setup.secret")} : <span className="num select-all text-ink">{setup.secret}</span></p>
                </div>
              )}
              <Field label={t("auth.mfa.code")} htmlFor="code">
                <Input id="code" inputMode="numeric" autoComplete="one-time-code" pattern="[0-9]{6}" maxLength={6} required autoFocus
                  value={code} onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))} className="num tracking-[0.3em] text-lg" />
              </Field>
              {error && <p role="alert" className="text-sm text-risk-critical">{error}</p>}
              <Button type="submit" variant="primary" loading={busy}>{t("auth.mfa.verify")}</Button>
              <Button type="button" variant="ghost" onClick={() => { setStep("password"); setCode(""); setError(null); }}>{t("common.previous")}</Button>
            </form>
          )}
        </div>
        <p className="text-xs text-ink-3 leading-relaxed">{t("auth.legal")}</p>
      </div>
      <div className="hidden lg:flex flex-col justify-center px-16 py-12 bg-bg">
        <div className="max-w-lg">
          <h2 className="page-title text-2xl">{t("auth.demo.title")}</h2>
          <p className="mt-2 text-sm text-ink-2 leading-relaxed">{t("auth.demo.help")} {L("Les rôles d'administration et de conformité exigent la double authentification et ne sont pas proposés ici.", "Administration and compliance roles require two-factor authentication and are not offered here.")}</p>
          <ul className="mt-6 divide-y divide-line border-y border-line">
            {DEMO_ROLES.map((r) => (
              <li key={r.role}>
                <button onClick={() => demo(r.role)} disabled={busy}
                  className="flex w-full items-center justify-between py-3 text-left text-sm text-ink hover:text-accent disabled:opacity-50">
                  <span>{locale === "fr" ? r.fr : r.en}</span>
                  <span className="text-xs text-ink-3">{L("Banque Démo", "Demo bank")}</span>
                </button>
              </li>
            ))}
          </ul>
          <div className="mt-6"><Notice tone="demo">{t("shell.demoBanner")}</Notice></div>
        </div>
      </div>
    </div>
  );
}

export default function LoginPage() {
  return (
    <Suspense>
      <LoginInner />
    </Suspense>
  );
}
