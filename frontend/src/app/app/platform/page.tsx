"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Building2, ShieldCheck } from "lucide-react";
import { useState } from "react";
import { ErrorState, KeyValue, LoadingBlock, Notice, PageHeader, Panel } from "@/components/ui/layout";
import { Dialog, Tabs, TabPanel, useToast } from "@/components/ui/overlay";
import { Badge, Button, Field, Input, Select, Textarea } from "@/components/ui/primitives";
import { ApiError, get, post } from "@/lib/api";
import { fmtDate, fmtNumber } from "@/lib/format";
import { useI18n, useL } from "@/lib/i18n";
import { useMe } from "@/lib/session";

type Org = { id: string; slug: string; name: string; kind: string; status: string; country: string; plan: string; isolation_mode: string; data_region: string; created_at: string;
  members: number; usage: { metric: string; used: number; limit: number | null }[] };
type User = { id: string; email: string; full_name: string; status: string; mfa_enabled: boolean; is_platform_admin: boolean; last_login_at: string | null; locked: boolean };

export default function PlatformPage() {
  const { t, locale } = useI18n();
  const L = useL();
  const { data: me } = useMe();
  const qc = useQueryClient();
  const toast = useToast();
  const [tab, setTab] = useState("orgs");
  const [create, setCreate] = useState(false);
  const [form, setForm] = useState({ slug: "", name: "", country: "FR", isolation_mode: "shared", admin_email: "" });
  const [invite, setInvite] = useState<string | null>(null);
  const [life, setLife] = useState<{ org: Org; action: string } | null>(null);
  const [reason, setReason] = useState("");
  const [confirm, setConfirm] = useState("");
  const isAdmin = !!me?.user?.is_platform_admin;
  const orgs = useQuery({ queryKey: ["platform", "orgs"], queryFn: () => get<Org[]>("/platform/organizations"), enabled: isAdmin });
  const users = useQuery({ queryKey: ["platform", "users"], queryFn: () => get<User[]>("/platform/users"), enabled: isAdmin && tab === "users" });
  const verify = useQuery({ queryKey: ["platform", "verify"], queryFn: () => get<{ valid: boolean; checked: number; head?: string; broken_at_id?: number; reason?: string }>("/platform/audit/verify"), enabled: isAdmin && tab === "audit" });
  const onErr = (e: unknown) => toast(e instanceof ApiError ? e.message : String(e), "error");
  const mk = useMutation({ mutationFn: () => post<{ admin_invite_path: string }>("/platform/organizations", form),
    onSuccess: (r) => { setInvite(`${location.origin}${r.admin_invite_path}`); qc.invalidateQueries({ queryKey: ["platform"] }); }, onError: onErr });
  const act = useMutation({ mutationFn: () => post(`/platform/organizations/${life?.org.id}/lifecycle`, { action: life?.action, reason, confirm_slug: confirm || null }),
    onSuccess: () => { setLife(null); setReason(""); setConfirm(""); qc.invalidateQueries({ queryKey: ["platform"] }); toast(L("Opération effectuée", "Operation done")); }, onError: onErr });
  if (!isAdmin) return <div className="flex flex-col gap-6"><PageHeader title={t("nav.platform")} /><Notice tone="warn">{t("common.forbidden")}</Notice></div>;
  const destructive = life?.action === "deprovision" || life?.action === "purge";
  return (
    <div className="flex flex-col gap-6">
      <PageHeader title={t("nav.platform")} subtitle={L("Exploitation de la plateforme : banques clientes, cycle de vie des tenants, utilisateurs, intégrité de l'audit.", "Platform operations: client banks, tenant lifecycle, users, audit integrity.")}
        actions={<Button variant="primary" icon={<Building2 className="size-4" />} onClick={() => { setInvite(null); setCreate(true); }}>{L("Nouvelle banque", "New bank")}</Button>} />
      <Tabs value={tab} onValueChange={setTab} items={[{ value: "orgs", label: L("Organisations", "Organisations"), count: orgs.data?.length }, { value: "users", label: L("Utilisateurs", "Users") }, { value: "audit", label: L("Intégrité de l'audit", "Audit integrity") }]}>
        <TabPanel value="orgs" className="pt-4">
          {orgs.error ? <ErrorState error={orgs.error} /> : !orgs.data ? <LoadingBlock /> : (
            <ul className="grid gap-4 lg:grid-cols-2">{orgs.data.map((o) => (
              <li key={o.id} className="rounded-[var(--radius-panel)] border border-line bg-surface p-4">
                <div className="flex flex-wrap items-center gap-2"><h3 className="flex-1 font-semibold">{o.name}</h3><Badge tone={o.kind === "demo" ? "demo" : "neutral"}>{o.kind}</Badge>
                  <Badge tone={o.status === "active" ? "ok" : o.status === "suspended" ? "medium" : "high"}>{o.status}</Badge></div>
                <KeyValue className="mt-3" rows={[[L("Identifiant", "Slug"), o.slug], [L("Isolation", "Isolation"), o.isolation_mode], [L("Région", "Region"), o.data_region], [L("Membres", "Members"), String(o.members)],
                  [L("Appels API (mois)", "API calls (month)"), fmtNumber(o.usage.find((u) => u.metric === "api_calls")?.used ?? 0, locale)], [L("Créée", "Created"), fmtDate(o.created_at, locale)]]} />
                {o.id !== me?.org.id && <div className="mt-3 flex flex-wrap gap-2">
                  {o.status === "active" && <Button size="sm" onClick={() => setLife({ org: o, action: "suspend" })}>{L("Suspendre", "Suspend")}</Button>}
                  {o.status === "suspended" && <Button size="sm" onClick={() => setLife({ org: o, action: "reactivate" })}>{L("Réactiver", "Reactivate")}</Button>}
                  {o.status !== "deprovisioned" && <Button size="sm" variant="ghost" onClick={() => setLife({ org: o, action: "deprovision" })}>{L("Déprovisionner", "Deprovision")}</Button>}
                  {o.status === "deprovisioned" && <Button size="sm" variant="danger" onClick={() => setLife({ org: o, action: "purge" })}>{L("Purger les données", "Purge data")}</Button>}
                </div>}
              </li>))}</ul>)}
        </TabPanel>
        <TabPanel value="users" className="pt-4">
          <Panel flush>{!users.data ? <LoadingBlock /> : (
            <ul>{users.data.map((u) => (
              <li key={u.id} className="flex flex-wrap items-center gap-3 border-b border-line px-4 py-2 text-sm last:border-0">
                <span className="font-medium">{u.full_name}</span><span className="text-ink-3">{u.email}</span>{u.is_platform_admin && <Badge tone="accent">admin</Badge>}
                {u.mfa_enabled ? <Badge tone="ok">MFA</Badge> : <Badge tone="medium">{L("sans MFA", "no MFA")}</Badge>}{u.locked && <Badge tone="high">{L("verrouillé", "locked")}</Badge>}
                <span className="ml-auto text-xs text-ink-3">{u.last_login_at ? fmtDate(u.last_login_at, locale, true) : "—"}</span>
              </li>))}</ul>)}
          </Panel>
        </TabPanel>
        <TabPanel value="audit" className="pt-4">
          <Panel title={L("Vérification de la chaîne d'audit", "Audit chain verification")} description={L("Recalcule chaque empreinte depuis la première entrée.", "Recomputes every fingerprint from the first entry.")}>
            {verify.isFetching ? <LoadingBlock /> : verify.data && (
              <div className="flex items-start gap-3"><ShieldCheck className={`size-6 ${verify.data.valid ? "text-ok" : "text-risk-high"}`} aria-hidden />
                <div><p className="font-medium">{verify.data.valid ? L("Chaîne intègre", "Chain intact") : L("Rupture détectée", "Break detected")}</p>
                  <p className="text-sm text-ink-2">{L(`${fmtNumber(verify.data.checked, locale)} entrées vérifiées`, `${fmtNumber(verify.data.checked, locale)} entries checked`)}{verify.data.head ? ` · ${L("tête", "head")} ${verify.data.head.slice(0, 16)}…` : ""}{verify.data.broken_at_id ? ` · #${verify.data.broken_at_id} (${verify.data.reason})` : ""}</p></div></div>)}
            <Button className="mt-3" size="sm" onClick={() => verify.refetch()}>{L("Revérifier", "Verify again")}</Button>
          </Panel>
        </TabPanel>
      </Tabs>
      <Dialog open={create} onOpenChange={setCreate} title={L("Nouvelle banque cliente", "New client bank")} description={L("Crée le tenant, ses quotas et une invitation pour son administrateur.", "Creates the tenant, its quotas and an invitation for its administrator.")}
        footer={invite ? <Button onClick={() => setCreate(false)}>{L("Fermer", "Close")}</Button> : <><Button onClick={() => setCreate(false)}>{L("Annuler", "Cancel")}</Button>
          <Button variant="primary" disabled={form.slug.length < 3 || form.name.length < 2 || !/^\S+@\S+\.\S+$/.test(form.admin_email)} loading={mk.isPending} onClick={() => mk.mutate()}>{L("Créer", "Create")}</Button></>}>
        {invite ? <div className="flex flex-col gap-2"><Notice tone="warn">{L("Lien d'invitation de l'administrateur (usage unique) :", "Administrator invitation link (single use):")}</Notice><code className="num break-all rounded bg-surface-2 p-2 text-xs">{invite}</code></div> : (
          <div className="flex flex-col gap-3">
            <Field label={L("Nom", "Name")} htmlFor="o-n"><Input id="o-n" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} /></Field>
            <Field label={L("Identifiant (slug)", "Slug")} htmlFor="o-s"><Input id="o-s" value={form.slug} onChange={(e) => setForm({ ...form, slug: e.target.value.toLowerCase().replace(/[^a-z0-9-]/g, "") })} /></Field>
            <Field label={L("E-mail de l'administrateur", "Administrator e-mail")} htmlFor="o-a"><Input id="o-a" type="email" value={form.admin_email} onChange={(e) => setForm({ ...form, admin_email: e.target.value })} /></Field>
            <div className="grid grid-cols-2 gap-3">
              <Field label={L("Pays", "Country")} htmlFor="o-c"><Input id="o-c" maxLength={2} value={form.country} onChange={(e) => setForm({ ...form, country: e.target.value.toUpperCase() })} /></Field>
              <Field label={L("Isolation", "Isolation")} htmlFor="o-i"><Select id="o-i" value={form.isolation_mode} onChange={(e) => setForm({ ...form, isolation_mode: e.target.value })}>
                <option value="shared">{L("schéma partagé + RLS", "shared schema + RLS")}</option><option value="dedicated">{L("base dédiée (secret requis)", "dedicated database (secret required)")}</option></Select></Field>
            </div>
          </div>)}
      </Dialog>
      <Dialog open={!!life} onOpenChange={(v) => !v && setLife(null)} title={`${life?.action} — ${life?.org.name}`}
        description={destructive ? L("Action destructive et irréversible : saisissez l'identifiant de l'organisation pour confirmer.", "Destructive, irreversible action: type the organisation slug to confirm.") : undefined}
        footer={<><Button onClick={() => setLife(null)}>{L("Annuler", "Cancel")}</Button><Button variant={destructive ? "danger" : "primary"} disabled={reason.length < 3 || (destructive && confirm !== life?.org.slug)} loading={act.isPending} onClick={() => act.mutate()}>{L("Confirmer", "Confirm")}</Button></>}>
        <div className="flex flex-col gap-3">
          <Field label={L("Motif", "Reason")} htmlFor="l-r"><Textarea id="l-r" rows={2} value={reason} onChange={(e) => setReason(e.target.value)} /></Field>
          {destructive && <Field label={L(`Identifiant : ${life?.org.slug}`, `Slug: ${life?.org.slug}`)} htmlFor="l-c"><Input id="l-c" value={confirm} onChange={(e) => setConfirm(e.target.value)} /></Field>}
        </div>
      </Dialog>
    </div>
  );
}
