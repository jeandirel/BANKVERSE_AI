"use client";

import { useQuery } from "@tanstack/react-query";
import { ChevronDown } from "lucide-react";
import { useState } from "react";
import { ErrorState, LoadingBlock, Notice, PageHeader, Panel } from "@/components/ui/layout";
import { Tabs, TabPanel } from "@/components/ui/overlay";
import { Badge, Button, Input, Select } from "@/components/ui/primitives";
import { get, qs } from "@/lib/api";
import { cn } from "@/lib/cn";
import { fmtDate } from "@/lib/format";
import { useI18n, useL } from "@/lib/i18n";

type Entry = { id: number; created_at: string; actor: string | null; actor_type: string; action: string; resource_type: string | null; resource_id: string | null;
  outcome: string; ip: string | null; details: Record<string, unknown>; hash: string };
type SecEvent = { id: string; kind: string; severity: string; created_at: string; ip: string | null; details: Record<string, unknown>; acknowledged: boolean };

function AuditRow({ e }: { e: Entry }) {
  const { locale } = useI18n();
  const [open, setOpen] = useState(false);
  return (
    <li className="border-b border-line last:border-0">
      <button type="button" onClick={() => setOpen((v) => !v)} aria-expanded={open} className="flex w-full flex-wrap items-center gap-3 px-4 py-2 text-left text-sm hover:bg-surface-2">
        <span className="num w-36 text-xs text-ink-3">{fmtDate(e.created_at, locale, true)}</span>
        <span className="font-medium">{e.action}</span>
        <Badge tone={e.outcome === "success" ? "neutral" : "high"}>{e.outcome}</Badge>
        <span className="text-ink-2 truncate max-w-[260px]">{e.actor ?? e.actor_type}</span>
        <span className="ml-auto num text-xs text-ink-3">#{e.id} · {e.hash}…</span>
        <ChevronDown className={cn("size-4 text-ink-3", open && "rotate-180")} aria-hidden />
      </button>
      {open && <pre className="mx-4 mb-3 max-h-60 overflow-auto rounded bg-surface-2 p-2 text-[11.5px]">{JSON.stringify({ resource: `${e.resource_type ?? ""}:${e.resource_id ?? ""}`, ip: e.ip, details: e.details }, null, 2)}</pre>}
    </li>
  );
}

export default function AdminPage() {
  const { t, locale } = useI18n();
  const L = useL();
  const [tab, setTab] = useState("audit");
  const [action, setAction] = useState("");
  const [outcome, setOutcome] = useState("");
  const [offset, setOffset] = useState(0);
  const audit = useQuery({ queryKey: ["admin", "audit", action, outcome, offset], placeholderData: (p) => p,
    queryFn: () => get<{ total: number; items: Entry[] }>(`/org/audit${qs({ action, outcome, limit: 50, offset })}`) });
  const sec = useQuery({ queryKey: ["admin", "security"], queryFn: () => get<SecEvent[]>("/org/security-events"), enabled: tab === "security" });
  return (
    <div className="flex flex-col gap-6">
      <PageHeader title={t("nav.admin")} subtitle={L("Journal d'audit de votre organisation et événements de sécurité.", "Your organisation's audit log and security events.")} />
      <Notice>{L("Le journal est en ajout seul (aucune modification ni suppression possible par l'application) et chaîné par hachage : chaque entrée référence l'empreinte de la précédente.", "The log is append-only (the application cannot modify or delete entries) and hash-chained: each entry references the previous entry's fingerprint.")}</Notice>
      <Tabs value={tab} onValueChange={setTab} items={[{ value: "audit", label: L("Journal d'audit", "Audit log"), count: audit.data?.total }, { value: "security", label: L("Sécurité", "Security") }]}>
        <TabPanel value="audit" className="pt-4">
          <Panel flush actions={<div className="flex gap-2">
            <Input className="h-8 w-48" placeholder={L("Action (préfixe)", "Action (prefix)")} value={action} onChange={(e) => { setAction(e.target.value); setOffset(0); }} aria-label={L("Action", "Action")} />
            <Select className="h-8 w-32" value={outcome} onChange={(e) => { setOutcome(e.target.value); setOffset(0); }} aria-label={L("Résultat", "Outcome")}>
              <option value="">{L("Tous", "All")}</option><option value="success">success</option><option value="denied">denied</option><option value="failure">failure</option></Select></div>}>
            {audit.error ? <ErrorState error={audit.error} /> : !audit.data ? <LoadingBlock /> : (
              <>
                <ul>{audit.data.items.map((e) => <AuditRow key={e.id} e={e} />)}</ul>
                <div className="flex items-center justify-between px-4 py-2 text-sm text-ink-3">
                  <span>{offset + 1}–{Math.min(offset + 50, audit.data.total)} / {audit.data.total}</span>
                  <div className="flex gap-2"><Button size="sm" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 50))}>{L("Précédent", "Previous")}</Button>
                    <Button size="sm" disabled={offset + 50 >= audit.data.total} onClick={() => setOffset(offset + 50)}>{L("Suivant", "Next")}</Button></div>
                </div>
              </>)}
          </Panel>
        </TabPanel>
        <TabPanel value="security" className="pt-4">
          <Panel flush>{sec.error ? <ErrorState error={sec.error} /> : !sec.data ? <LoadingBlock /> : sec.data.length === 0 ? <p className="px-4 py-4 text-sm text-ink-3">{L("Aucun événement.", "No event.")}</p> : (
            <ul>{sec.data.map((e) => (
              <li key={e.id} className="flex flex-wrap items-center gap-3 border-b border-line px-4 py-2 text-sm last:border-0">
                <span className="num w-36 text-xs text-ink-3">{fmtDate(e.created_at, locale, true)}</span><Badge tone={e.severity === "high" || e.severity === "critical" ? "high" : e.severity === "medium" ? "medium" : "neutral"}>{e.severity}</Badge>
                <span className="font-medium">{e.kind}</span><span className="text-ink-3">{e.ip}</span><span className="ml-auto text-xs text-ink-3 truncate max-w-[360px]">{JSON.stringify(e.details)}</span>
              </li>))}</ul>)}
          </Panel>
        </TabPanel>
      </Tabs>
    </div>
  );
}
