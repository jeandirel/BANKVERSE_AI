"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { KeyRound, Trash2 } from "lucide-react";
import { useState } from "react";
import { ErrorState, KeyValue, LoadingBlock, Notice, PageHeader, Panel } from "@/components/ui/layout";
import { Dialog, useToast } from "@/components/ui/overlay";
import { Badge, Button, Field, Input, Select, Switch } from "@/components/ui/primitives";
import { DemoTag } from "@/components/ui/risk";
import { ApiError, del, get, patch, post, put } from "@/lib/api";
import { MODULE_LABELS } from "@/lib/domain/models";
import { fmtDate, fmtNumber } from "@/lib/format";
import { pick, useI18n, useL } from "@/lib/i18n";
import { useCan } from "@/lib/session";

type Org = { id: string; slug: string; name: string; kind: string; country: string; status: string; plan: string; isolation_mode: string; data_region: string;
  created_at: string; members: number; settings: Record<string, unknown>; modules: { module: string; enabled: boolean }[] };
type Key = { id: string; name: string; prefix: string; role: string; created_at: string; expires_at: string | null; last_used_at: string | null; revoked: boolean };
type Role = { role: string; label: { fr?: string; en?: string }; assignable: boolean };

const SETTINGS: { key: string; fr: string; en: string; type: "number" | "text" }[] = [
  { key: "fraud_alert_threshold", fr: "Seuil d'alerte fraude (probabilité)", en: "Fraud alert threshold (probability)", type: "number" },
  { key: "fraud_review_cost_eur", fr: "Coût d'une revue fraude (€)", en: "Cost of a fraud review (€)", type: "number" },
  { key: "aml_alert_threshold", fr: "Seuil d'alerte LCB-FT", en: "AML alert threshold", type: "number" },
  { key: "credit_review_required_above_pd", fr: "Second avis crédit au-delà d'une PD de", en: "Second credit opinion above PD", type: "number" },
  { key: "data_retention_days", fr: "Conservation des données (jours)", en: "Data retention (days)", type: "number" },
];
const ALL_MODULES = ["data", "fraud", "aml", "credit", "forecasting", "documents", "customer", "copilot", "agents", "governance"];

export default function OrgPage() {
  const { t, locale } = useI18n();
  const L = useL();
  const can = useCan();
  const qc = useQueryClient();
  const toast = useToast();
  const org = useQuery({ queryKey: ["org"], queryFn: () => get<Org>("/org") });
  const keys = useQuery({ queryKey: ["org", "keys"], queryFn: () => get<Key[]>("/org/api-keys"), enabled: can("org.apikeys.manage") });
  const roles = useQuery({ queryKey: ["org", "roles"], queryFn: () => get<Role[]>("/org/roles"), enabled: can("org.apikeys.manage") });
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [keyOpen, setKeyOpen] = useState(false);
  const [keyForm, setKeyForm] = useState({ name: "", role: "fraud_analyst", expires_in_days: "90" });
  const [secret, setSecret] = useState<string | null>(null);
  const onErr = (e: unknown) => toast(e instanceof ApiError ? e.message : String(e), "error");
  const save = useMutation({ mutationFn: () => patch("/org", { settings: Object.fromEntries(Object.entries(draft).map(([k, v]) => [k, v === "" ? null : Number(v)])) }),
    onSuccess: () => { setDraft({}); qc.invalidateQueries({ queryKey: ["org"] }); toast(L("Paramètres enregistrés", "Settings saved")); }, onError: onErr });
  const toggle = useMutation({ mutationFn: (v: { module: string; enabled: boolean }) => put(`/org/modules/${v.module}`, { enabled: v.enabled }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["org"] }); qc.invalidateQueries({ queryKey: ["me"] }); }, onError: onErr });
  const createKey = useMutation({ mutationFn: () => post<{ api_key: string }>("/org/api-keys", { ...keyForm, expires_in_days: Number(keyForm.expires_in_days) }),
    onSuccess: (r) => { setSecret(r.api_key); qc.invalidateQueries({ queryKey: ["org", "keys"] }); }, onError: onErr });
  const revoke = useMutation({ mutationFn: (id: string) => del(`/org/api-keys/${id}`), onSuccess: () => qc.invalidateQueries({ queryKey: ["org", "keys"] }), onError: onErr });
  if (org.error) return <ErrorState error={org.error} />;
  const o = org.data;
  if (!o) return <LoadingBlock />;
  const enabled = new Set(o.modules.filter((m) => m.enabled).map((m) => m.module));
  const manage = can("org.settings.manage");
  return (
    <div className="flex flex-col gap-6">
      <PageHeader title={t("nav.org")} subtitle={o.name} meta={o.kind === "demo" ? <DemoTag /> : <Badge>{o.plan}</Badge>} />
      <div className="grid gap-6 xl:grid-cols-2">
        <Panel title={L("Identité du tenant", "Tenant identity")}>
          <KeyValue rows={[[L("Identifiant", "Slug"), o.slug], [L("Type", "Type"), o.kind], [L("Statut", "Status"), o.status], [L("Pays", "Country"), o.country],
            [L("Isolation des données", "Data isolation"), o.isolation_mode === "dedicated" ? L("base dédiée", "dedicated database") : L("schéma partagé + RLS PostgreSQL", "shared schema + PostgreSQL RLS")],
            [L("Région de données", "Data region"), o.data_region], [L("Membres actifs", "Active members"), String(o.members)], [L("Créé le", "Created"), fmtDate(o.created_at, locale)]]} />
        </Panel>
        <Panel title={L("Paramètres métier", "Business settings")} description={L("Seuils utilisés par les scorings de votre organisation.", "Thresholds used by your organisation's scoring.")}>
          <div className="flex flex-col gap-3">
            {SETTINGS.map((s) => (
              <Field key={s.key} label={locale === "en" ? s.en : s.fr} htmlFor={`s-${s.key}`}>
                <Input id={`s-${s.key}`} type="number" step="any" disabled={!manage} value={draft[s.key] ?? (o.settings[s.key] == null ? "" : String(o.settings[s.key]))}
                  onChange={(e) => setDraft({ ...draft, [s.key]: e.target.value })} placeholder={L("valeur par défaut du modèle", "model default")} />
              </Field>))}
            {manage && <Button className="self-start" variant="primary" disabled={!Object.keys(draft).length} loading={save.isPending} onClick={() => save.mutate()}>{L("Enregistrer", "Save")}</Button>}
          </div>
        </Panel>
      </div>
      <Panel title={L("Modules activés", "Enabled modules")} description={L("Un module désactivé disparaît de la navigation et son API est refusée pour toute l'organisation.", "A disabled module disappears from navigation and its API is refused for the whole organisation.")}>
        <ul className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">{ALL_MODULES.map((m) => (
          <li key={m}><Switch showLabel checked={enabled.has(m)} disabled={!manage || toggle.isPending} onChange={(v) => toggle.mutate({ module: m, enabled: v })}
            label={m === "data" ? L("Données", "Data") : m === "governance" ? L("Gouvernance", "Governance") : (locale === "en" ? MODULE_LABELS[m]?.[1] : MODULE_LABELS[m]?.[0]) ?? m} /></li>))}</ul>
      </Panel>
      {can("org.apikeys.manage") && <Panel title={L("Clés d'API (intégrations)", "API keys (integrations)")} description={L("Identités de service à droits limités ; la clé n'est affichée qu'une fois.", "Limited-privilege service identities; the key is shown only once.")}
        actions={<Button size="sm" icon={<KeyRound className="size-4" />} onClick={() => { setSecret(null); setKeyOpen(true); }}>{L("Nouvelle clé", "New key")}</Button>} flush>
        {!keys.data ? <LoadingBlock /> : keys.data.length === 0 ? <p className="px-4 py-4 text-sm text-ink-3">{L("Aucune clé.", "No key.")}</p> : (
          <ul>{keys.data.map((k) => (
            <li key={k.id} className="flex flex-wrap items-center gap-3 border-b border-line px-4 py-2.5 text-sm last:border-0">
              <span className="font-medium">{k.name}</span><span className="num text-xs text-ink-3">bvk_{k.prefix}_…</span><Badge>{k.role}</Badge>
              {k.revoked ? <Badge tone="high">{L("révoquée", "revoked")}</Badge> : <span className="text-xs text-ink-3">{L("expire", "expires")} {fmtDate(k.expires_at, locale)}</span>}
              <span className="ml-auto text-xs text-ink-3">{k.last_used_at ? `${L("utilisée", "used")} ${fmtDate(k.last_used_at, locale, true)}` : L("jamais utilisée", "never used")}</span>
              {!k.revoked && <Button size="sm" variant="ghost" aria-label={L("Révoquer", "Revoke")} onClick={() => revoke.mutate(k.id)}><Trash2 className="size-4" /></Button>}
            </li>))}</ul>
        )}
      </Panel>}
      <Dialog open={keyOpen} onOpenChange={setKeyOpen} title={L("Nouvelle clé d'API", "New API key")}
        footer={secret ? <Button onClick={() => setKeyOpen(false)}>{L("J'ai copié la clé", "I copied the key")}</Button> : <><Button onClick={() => setKeyOpen(false)}>{L("Annuler", "Cancel")}</Button>
          <Button variant="primary" disabled={keyForm.name.length < 3} loading={createKey.isPending} onClick={() => createKey.mutate()}>{L("Créer", "Create")}</Button></>}>
        {secret ? <div className="flex flex-col gap-2"><Notice tone="warn">{L("Copiez cette clé maintenant et stockez-la dans un coffre de secrets : elle ne sera plus affichée.", "Copy this key now and store it in a secrets manager: it will not be shown again.")}</Notice>
          <code className="num break-all rounded bg-surface-2 p-2 text-xs">{secret}</code></div> : (
          <div className="flex flex-col gap-3">
            <Field label={L("Nom", "Name")} htmlFor="k-n"><Input id="k-n" value={keyForm.name} onChange={(e) => setKeyForm({ ...keyForm, name: e.target.value })} /></Field>
            <Field label={L("Rôle accordé", "Granted role")} htmlFor="k-r"><Select id="k-r" value={keyForm.role} onChange={(e) => setKeyForm({ ...keyForm, role: e.target.value })}>
              {roles.data?.filter((r) => r.assignable).map((r) => <option key={r.role} value={r.role}>{pick(r.label, locale, r.role)}</option>)}</Select></Field>
            <Field label={L("Validité (jours)", "Validity (days)")} htmlFor="k-e"><Input id="k-e" type="number" min={1} max={365} value={keyForm.expires_in_days} onChange={(e) => setKeyForm({ ...keyForm, expires_in_days: e.target.value })} /></Field>
          </div>)}
      </Dialog>
      <UsagePanel />
    </div>
  );
}

function UsagePanel() {
  const L = useL();
  const { locale } = useI18n();
  const u = useQuery({ queryKey: ["org", "usage"], queryFn: () => get<{ metric: string; used: number; limit: number | null; period: string }[]>("/org/usage") });
  if (!u.data) return null;
  return (
    <Panel title={L("Quotas du mois", "Monthly quotas")} description={u.data[0]?.period}>
      <table className="w-full text-sm"><tbody>{u.data.map((x) => (
        <tr key={x.metric} className="border-b border-line"><td className="py-1.5">{x.metric}</td><td className="num text-right">{fmtNumber(x.used, locale)}</td><td className="num text-right text-ink-3">/ {x.limit == null ? "∞" : fmtNumber(x.limit, locale)}</td></tr>))}</tbody></table>
    </Panel>
  );
}
