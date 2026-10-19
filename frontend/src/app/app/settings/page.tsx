"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { KeyValue, LoadingBlock, Notice, PageHeader, Panel } from "@/components/ui/layout";
import { useToast } from "@/components/ui/overlay";
import { Badge, Button, Field, Input } from "@/components/ui/primitives";
import { ApiError, del, get, post } from "@/lib/api";
import { fmtDate } from "@/lib/format";
import { pick, useI18n, useL } from "@/lib/i18n";
import { useMe } from "@/lib/session";

type Sess = { id: string; created_at: string; last_seen_at: string | null; ip: string | null; user_agent: string | null; current: boolean; mfa_verified: boolean };

export default function SettingsPage() {
  const { t, locale, setLocale } = useI18n();
  const L = useL();
  const { data: me } = useMe();
  const qc = useQueryClient();
  const toast = useToast();
  const [pw, setPw] = useState({ current_password: "", new_password: "", confirm: "" });
  const [mfa, setMfa] = useState<{ secret: string; qr_svg: string } | null>(null);
  const [code, setCode] = useState("");
  const sessions = useQuery({ queryKey: ["auth", "sessions"], queryFn: () => get<Sess[]>("/auth/sessions") });
  const onErr = (e: unknown) => toast(e instanceof ApiError ? e.message : String(e), "error");
  const changePw = useMutation({ mutationFn: () => post("/auth/password", { current_password: pw.current_password, new_password: pw.new_password }),
    onSuccess: () => { setPw({ current_password: "", new_password: "", confirm: "" }); toast(L("Mot de passe modifié ; les autres sessions ont été fermées.", "Password changed; other sessions were closed.")); }, onError: onErr });
  const startMfa = useMutation({ mutationFn: () => post<{ secret: string; qr_svg: string }>("/auth/mfa/setup/start", {}), onSuccess: setMfa, onError: onErr });
  const confirmMfa = useMutation({ mutationFn: () => post("/auth/mfa/setup/confirm", { code }), onSuccess: () => { setMfa(null); setCode(""); qc.invalidateQueries({ queryKey: ["me"] }); toast(L("Double authentification activée", "Two-factor authentication enabled")); }, onError: onErr });
  const revoke = useMutation({ mutationFn: (id: string) => del(`/auth/sessions/${id}`), onSuccess: () => qc.invalidateQueries({ queryKey: ["auth", "sessions"] }), onError: onErr });
  if (!me) return <LoadingBlock />;
  const u = me.user;
  const pwOk = pw.new_password.length >= 12 && pw.new_password === pw.confirm && pw.current_password.length > 0;
  return (
    <div className="flex flex-col gap-6">
      <PageHeader title={t("nav.settings")} subtitle={u ? `${u.full_name} · ${u.email}` : L("Identité de service", "Service identity")} />
      <div className="grid gap-6 xl:grid-cols-2">
        <Panel title={L("Profil", "Profile")}>
          <KeyValue rows={[[L("Organisation", "Organisation"), me.org.name], [L("Rôle", "Role"), pick(me.role_label, locale, me.role)],
            [L("Double authentification", "Two-factor authentication"), u?.mfa_enabled ? <Badge key="m" tone="ok">{L("activée", "enabled")}</Badge> : <Badge key="m" tone="medium">{L("désactivée", "disabled")}</Badge>]]} />
          <div className="mt-4 flex items-center gap-3"><span className="text-sm text-ink-3">{L("Langue de l'interface", "Interface language")}</span>
            <div role="group" aria-label={L("Langue", "Language")} className="inline-flex rounded-[var(--radius-control)] border border-line-strong p-0.5">
              {(["fr", "en"] as const).map((l) => <button key={l} type="button" aria-pressed={locale === l} onClick={() => setLocale(l)}
                className={`rounded px-3 py-1 text-sm ${locale === l ? "bg-accent text-accent-ink" : "text-ink-2 hover:text-ink"}`}>{l === "fr" ? "Français" : "English"}</button>)}</div></div>
        </Panel>
        {u && !u.mfa_enabled && <Panel title={L("Activer la double authentification", "Enable two-factor authentication")} description={L("Application TOTP (Google Authenticator, Microsoft Authenticator, 1Password…).", "TOTP app (Google Authenticator, Microsoft Authenticator, 1Password…).")}>
          {!mfa ? <Button variant="primary" loading={startMfa.isPending} onClick={() => startMfa.mutate()}>{L("Commencer", "Start")}</Button> : (
            <div className="flex flex-col gap-3">
              <div className="w-44 rounded bg-white p-2" aria-label={L("QR code TOTP", "TOTP QR code")} dangerouslySetInnerHTML={{ __html: mfa.qr_svg }} />
              <p className="text-xs text-ink-3">{L("Ou saisissez la clé :", "Or enter the key:")} <code className="num">{mfa.secret}</code></p>
              <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); confirmMfa.mutate(); }}>
                <Input inputMode="numeric" autoComplete="one-time-code" maxLength={6} value={code} onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))} aria-label={L("Code à 6 chiffres", "6-digit code")} className="w-32 num" />
                <Button type="submit" variant="primary" disabled={code.length !== 6} loading={confirmMfa.isPending}>{L("Vérifier", "Verify")}</Button></form>
            </div>)}
        </Panel>}
        {u && <Panel title={L("Mot de passe", "Password")} description={L("12 caractères minimum ; les mots de passe courants sont refusés.", "At least 12 characters; common passwords are refused.")}>
          <form className="flex flex-col gap-3" onSubmit={(e) => { e.preventDefault(); if (pwOk) changePw.mutate(); }}>
            <Field label={L("Mot de passe actuel", "Current password")} htmlFor="p-c"><Input id="p-c" type="password" autoComplete="current-password" value={pw.current_password} onChange={(e) => setPw({ ...pw, current_password: e.target.value })} /></Field>
            <Field label={L("Nouveau mot de passe", "New password")} htmlFor="p-n"><Input id="p-n" type="password" autoComplete="new-password" value={pw.new_password} onChange={(e) => setPw({ ...pw, new_password: e.target.value })} /></Field>
            <Field label={L("Confirmation", "Confirmation")} htmlFor="p-k" error={pw.confirm && pw.confirm !== pw.new_password ? L("Les mots de passe diffèrent", "Passwords differ") : undefined}>
              <Input id="p-k" type="password" autoComplete="new-password" value={pw.confirm} onChange={(e) => setPw({ ...pw, confirm: e.target.value })} /></Field>
            <Button className="self-start" type="submit" variant="primary" disabled={!pwOk} loading={changePw.isPending}>{L("Modifier", "Change")}</Button>
          </form>
        </Panel>}
        <Panel title={L("Sessions actives", "Active sessions")} flush>
          {!sessions.data ? <LoadingBlock /> : (
            <ul>{sessions.data.map((s) => (
              <li key={s.id} className="flex flex-wrap items-center gap-3 border-b border-line px-4 py-2.5 text-sm last:border-0">
                <span className="truncate max-w-[260px] text-ink-2">{s.user_agent ?? "—"}</span><span className="text-xs text-ink-3">{s.ip}</span>
                {s.current && <Badge tone="accent">{L("cette session", "this session")}</Badge>}{s.mfa_verified && <Badge tone="ok">MFA</Badge>}
                <span className="ml-auto text-xs text-ink-3">{fmtDate(s.last_seen_at ?? s.created_at, locale, true)}</span>
                {!s.current && <Button size="sm" variant="ghost" onClick={() => revoke.mutate(s.id)}>{L("Fermer", "End")}</Button>}
              </li>))}</ul>)}
        </Panel>
      </div>
      {me.org.kind === "demo" && <Notice tone="demo">{L("Compte de démonstration : les modifications sont possibles mais l'environnement peut être réinitialisé.", "Demo account: changes are allowed but the environment may be reset.")}</Notice>}
    </div>
  );
}
