"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Copy, UserPlus } from "lucide-react";
import { useState } from "react";
import { ErrorState, LoadingBlock, Notice, PageHeader, Panel } from "@/components/ui/layout";
import { Dialog, Tabs, TabPanel, useToast } from "@/components/ui/overlay";
import { Badge, Button, Field, Input, Select } from "@/components/ui/primitives";
import { ApiError, del, get, patch, post } from "@/lib/api";
import { fmtDate } from "@/lib/format";
import { pick, useI18n, useL } from "@/lib/i18n";
import { useCan, useMe } from "@/lib/session";

type Member = { membership_id: string; user_id: string; email: string; full_name: string; role: string; role_label: { fr?: string; en?: string }; status: string;
  mfa_enabled: boolean; last_login_at: string | null; joined_at: string };
type Role = { role: string; label: { fr?: string; en?: string }; permissions: string[]; assignable: boolean };
type Invite = { id: string; email: string; role: string; created_at: string; expires_at: string; accepted_at?: string | null };

export default function TeamPage() {
  const { t, locale } = useI18n();
  const L = useL();
  const can = useCan();
  const { data: me } = useMe();
  const qc = useQueryClient();
  const toast = useToast();
  const [tab, setTab] = useState("members");
  const [inviteOpen, setInviteOpen] = useState(false);
  const [form, setForm] = useState({ email: "", role: "fraud_analyst" });
  const [link, setLink] = useState<string | null>(null);
  const manage = can("org.members.manage");
  const members = useQuery({ queryKey: ["team", "members"], queryFn: () => get<Member[]>("/org/members") });
  const roles = useQuery({ queryKey: ["org", "roles"], queryFn: () => get<Role[]>("/org/roles") });
  const invites = useQuery({ queryKey: ["team", "invites"], queryFn: () => get<Invite[]>("/org/invitations"), enabled: manage });
  const onErr = (e: unknown) => toast(e instanceof ApiError ? e.message : String(e), "error");
  const upd = useMutation({ mutationFn: (v: { id: string; role?: string; status?: string }) => patch(`/org/members/${v.id}`, { role: v.role, status: v.status }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["team"] }); toast(L("Membre mis à jour", "Member updated")); }, onError: onErr });
  const invite = useMutation({ mutationFn: () => post<{ invite_path: string }>("/org/invitations", form),
    onSuccess: (r) => { setLink(`${location.origin}${r.invite_path}`); qc.invalidateQueries({ queryKey: ["team", "invites"] }); }, onError: onErr });
  const revoke = useMutation({ mutationFn: (id: string) => del(`/org/invitations/${id}`), onSuccess: () => qc.invalidateQueries({ queryKey: ["team", "invites"] }), onError: onErr });
  const assignable = (roles.data ?? []).filter((r) => r.assignable);
  const allPerms = Array.from(new Set((roles.data ?? []).flatMap((r) => r.permissions))).sort();
  return (
    <div className="flex flex-col gap-6">
      <PageHeader title={t("nav.team")} subtitle={L("Membres, rôles et invitations. Les droits sont vérifiés côté serveur à chaque requête.", "Members, roles and invitations. Permissions are checked server-side on every request.")}
        actions={manage ? <Button variant="primary" icon={<UserPlus className="size-4" />} onClick={() => { setLink(null); setInviteOpen(true); }}>{L("Inviter", "Invite")}</Button> : undefined} />
      <Tabs value={tab} onValueChange={setTab} items={[{ value: "members", label: L("Membres", "Members"), count: members.data?.length }, ...(manage ? [{ value: "invites", label: L("Invitations", "Invitations"), count: invites.data?.length }] : []),
        { value: "roles", label: L("Matrice des droits", "Permission matrix") }]}>
        <TabPanel value="members" className="pt-4">
          <Panel flush>{members.error ? <ErrorState error={members.error} /> : !members.data ? <LoadingBlock /> : (
            <div className="overflow-x-auto"><table className="w-full text-sm">
              <thead><tr className="border-b border-line text-left text-[12.5px] text-ink-3">{[L("Membre", "Member"), L("Rôle", "Role"), "MFA", L("Statut", "Status"), L("Dernière connexion", "Last login")].map((h) => <th key={h} className="px-3 py-2 font-medium">{h}</th>)}</tr></thead>
              <tbody>{members.data.map((m) => {
                const self = m.user_id === me?.user?.id;
                return (
                  <tr key={m.membership_id} className="border-b border-line">
                    <td className="px-3 py-2"><p className="font-medium">{m.full_name}{self ? ` (${L("vous", "you")})` : ""}</p><p className="text-xs text-ink-3">{m.email}</p></td>
                    <td className="px-3 py-2">{manage && !self && assignable.some((r) => r.role === m.role) ? (
                      <Select className="h-8 w-52" value={m.role} aria-label={L("Rôle", "Role")} onChange={(e) => upd.mutate({ id: m.membership_id, role: e.target.value })}>
                        {assignable.map((r) => <option key={r.role} value={r.role}>{pick(r.label, locale, r.role)}</option>)}</Select>) : pick(m.role_label, locale, m.role)}</td>
                    <td className="px-3 py-2">{m.mfa_enabled ? <Badge tone="ok">{L("activée", "on")}</Badge> : <Badge tone="medium">{L("non", "off")}</Badge>}</td>
                    <td className="px-3 py-2">{manage && !self ? <Button size="sm" variant="ghost" onClick={() => upd.mutate({ id: m.membership_id, status: m.status === "active" ? "suspended" : "active" })}>
                      <Badge tone={m.status === "active" ? "ok" : "high"}>{m.status}</Badge></Button> : <Badge tone={m.status === "active" ? "ok" : "high"}>{m.status}</Badge>}</td>
                    <td className="px-3 py-2 text-ink-3">{m.last_login_at ? fmtDate(m.last_login_at, locale, true) : "—"}</td>
                  </tr>);
              })}</tbody>
            </table></div>)}
          </Panel>
        </TabPanel>
        <TabPanel value="invites" className="pt-4">
          <Panel flush>{!invites.data ? <LoadingBlock /> : invites.data.length === 0 ? <p className="px-4 py-4 text-sm text-ink-3">{L("Aucune invitation en cours.", "No pending invitation.")}</p> : (
            <ul>{invites.data.map((i) => (
              <li key={i.id} className="flex flex-wrap items-center gap-3 border-b border-line px-4 py-2.5 text-sm last:border-0">
                <span className="font-medium">{i.email}</span><Badge>{i.role}</Badge><span className="text-xs text-ink-3">{L("expire", "expires")} {fmtDate(i.expires_at, locale, true)}</span>
                <Button className="ml-auto" size="sm" variant="ghost" onClick={() => revoke.mutate(i.id)}>{L("Révoquer", "Revoke")}</Button>
              </li>))}</ul>)}
          </Panel>
        </TabPanel>
        <TabPanel value="roles" className="pt-4">
          <Panel flush description={L("Permissions accordées par rôle (vérifiées par l'API, pas seulement masquées dans l'interface).", "Permissions granted per role (enforced by the API, not just hidden in the interface).")}>
            {!roles.data ? <LoadingBlock /> : (
              <div className="overflow-x-auto"><table className="text-xs">
                <thead><tr className="border-b border-line"><th className="sticky left-0 bg-surface px-3 py-2 text-left font-medium text-ink-3">{L("Permission", "Permission")}</th>
                  {roles.data.map((r) => <th key={r.role} className="px-2 py-2 font-medium text-ink-2 [writing-mode:vertical-rl] rotate-180 h-36 text-left">{pick(r.label, locale, r.role)}</th>)}</tr></thead>
                <tbody>{allPerms.map((p) => (
                  <tr key={p} className="border-b border-line"><td className="sticky left-0 bg-surface px-3 py-1 num">{p}</td>
                    {roles.data.map((r) => <td key={r.role} className="px-2 py-1 text-center">{r.permissions.includes(p) ? <span className="text-accent" aria-label={L("oui", "yes")}>●</span> : <span className="text-line-strong" aria-label={L("non", "no")}>·</span>}</td>)}</tr>))}</tbody>
              </table></div>)}
          </Panel>
        </TabPanel>
      </Tabs>
      <Dialog open={inviteOpen} onOpenChange={setInviteOpen} title={L("Inviter un membre", "Invite a member")}
        description={L("Aucun service d'e-mail n'est configuré ici : transmettez le lien à usage unique par un canal sûr.", "No e-mail service is configured here: share the single-use link over a secure channel.")}
        footer={link ? <Button onClick={() => setInviteOpen(false)}>{L("Fermer", "Close")}</Button> : <><Button onClick={() => setInviteOpen(false)}>{L("Annuler", "Cancel")}</Button>
          <Button variant="primary" disabled={!/^\S+@\S+\.\S+$/.test(form.email)} loading={invite.isPending} onClick={() => invite.mutate()}>{L("Créer l'invitation", "Create invitation")}</Button></>}>
        {link ? <div className="flex flex-col gap-2"><Notice tone="warn">{L("Lien valable une seule fois, avec expiration.", "Single-use link with expiry.")}</Notice>
          <div className="flex gap-2"><code className="num flex-1 break-all rounded bg-surface-2 p-2 text-xs">{link}</code>
            <Button size="sm" icon={<Copy className="size-3.5" />} onClick={() => navigator.clipboard?.writeText(link).then(() => toast(L("Copié", "Copied")))}>{L("Copier", "Copy")}</Button></div></div> : (
          <div className="flex flex-col gap-3">
            <Field label="E-mail" htmlFor="iv-e"><Input id="iv-e" type="email" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} /></Field>
            <Field label={L("Rôle", "Role")} htmlFor="iv-r"><Select id="iv-r" value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value })}>
              {assignable.map((r) => <option key={r.role} value={r.role}>{pick(r.label, locale, r.role)}</option>)}</Select></Field>
          </div>)}
      </Dialog>
    </div>
  );
}
