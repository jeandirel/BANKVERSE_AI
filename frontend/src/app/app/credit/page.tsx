"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import type { ColumnDef } from "@tanstack/react-table";
import { Plus } from "lucide-react";
import { useRouter } from "next/navigation";
import { useMemo, useState } from "react";
import { BarsChart } from "@/components/ui/charts";
import { DataTable } from "@/components/ui/data-table";
import { ErrorState, KeyValue, Ledger, LoadingBlock, Notice, PageHeader, Panel } from "@/components/ui/layout";
import { Dialog, Tabs, TabPanel, useToast } from "@/components/ui/overlay";
import { Badge, Button, Field, Input, Select } from "@/components/ui/primitives";
import { DemoTag } from "@/components/ui/risk";
import { ApiError, get, post, qs } from "@/lib/api";
import { fmtMoney, fmtNumber, fmtPct } from "@/lib/format";
import { useI18n, useL } from "@/lib/i18n";
import { useCan, useMe } from "@/lib/session";
import { gradeTone } from "@/lib/domain/credit";

type Row = { id: string; reference: string; status: string; product: string; requested_limit: number; pd: number | null; grade: string | null;
  decision: string | null; created_at: string; customer: { id: string; name: string; ref: string; segment: string; is_demo: boolean } };
type Overview = { applications_by_status: Record<string, number>; by_grade: Record<string, number>; grade_bounds: Record<string, string>;
  decisions: Record<string, number>; average_pd: number; requested_exposure: number; notice: string;
  model: null | { version: number; algorithm: string; test_auc: number; test_gini: number; test_brier: number; reference_cutoff: number } };
type FairGroup = { group: string; n: number; observed_default_rate: number; mean_predicted_pd: number; auc: number | null; review_rate_at_cutoff: number; tpr_at_cutoff: number | null };
type ModelInfo = { version: number; algorithm: string; metrics: {
  comparison: { model: string; family: string; calibration: string; val_auc: number; test_auc: number; test_gini: number; test_pr_auc: number; test_brier: number; test_ece: number; test_ks: number; psi_train_vs_test: number }[];
  fairness: Record<string, { groups: FairGroup[]; review_rate_gap: number; equal_opportunity_gap: number; calibration_gap_max: number }>;
  protected_attributes_ablation: { test_auc_policy_model: number; test_auc_with_protected: number };
  grades_test: { grade: string; pd_range: string; n: number; observed_default_rate: number | null }[];
  test: { calibration: { p_mean: number; y_rate: number }[] } }; model_card: { limitations: string[]; excluded_attributes: string[]; horizon: string } };


function NewApplicationDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (v: boolean) => void }) {
  const L = useL();
  const router = useRouter();
  const toast = useToast();
  const [q, setQ] = useState("");
  const custs = useQuery({ queryKey: ["customers", "search", q], queryFn: () => get<{ items: { id: string; name: string; ref: string; segment: string }[] }>(`/customers${qs({ q, page_size: 20 })}`), enabled: open });
  const [customer, setCustomer] = useState("");
  const [limit, setLimit] = useState("3000");
  const [status, setStatus] = useState(["0", "0", "0", "0", "0", "0"]);
  const [bill, setBill] = useState("1200");
  const [paid, setPaid] = useState("400");
  const create = useMutation({
    mutationFn: () => post<{ id: string }>("/credit/applications", {
      customer_id: customer, requested_limit: Number(limit), pay_status: status.map(Number),
      bill_amt: Array(6).fill(Number(bill)), pay_amt: Array(6).fill(Number(paid)),
    }),
    onSuccess: (r) => router.push(`/app/credit/applications/${r.id}`),
    onError: (e) => toast(e instanceof ApiError ? e.message : String(e), "error"),
  });
  return (
    <Dialog open={open} onOpenChange={onOpenChange} title={L("Nouvelle demande de crédit", "New credit application")}
      description={L("La demande est scorée immédiatement ; la décision reste celle de l'analyste.", "The application is scored immediately; the decision remains the analyst's.")}
      footer={<><Button onClick={() => onOpenChange(false)}>{L("Annuler", "Cancel")}</Button><Button variant="primary" disabled={!customer} loading={create.isPending} onClick={() => create.mutate()}>{L("Créer et scorer", "Create and score")}</Button></>}>
      <div className="flex flex-col gap-3">
        <Field label={L("Rechercher un client", "Find a customer")} htmlFor="cq"><Input id="cq" value={q} onChange={(e) => setQ(e.target.value)} /></Field>
        <Field label={L("Client", "Customer")} htmlFor="cu">
          <Select id="cu" value={customer} onChange={(e) => setCustomer(e.target.value)}>
            <option value="">{L("Choisir…", "Choose…")}</option>
            {(custs.data?.items ?? []).map((c) => <option key={c.id} value={c.id}>{c.name} · {c.ref} · {c.segment}</option>)}
          </Select>
        </Field>
        <Field label={L("Plafond demandé (EUR)", "Requested limit (EUR)")} htmlFor="li"><Input id="li" type="number" value={limit} onChange={(e) => setLimit(e.target.value)} /></Field>
        <Field label={L("Statut de paiement des 6 derniers mois (−2 à 8, M-1 en premier)", "Payment status, last 6 months (−2 to 8, M-1 first)")}>
          <div className="grid grid-cols-6 gap-2">{status.map((v, i) => <Input key={i} aria-label={`M-${i + 1}`} type="number" min={-2} max={8} value={v} onChange={(e) => setStatus((s) => s.map((x, j) => (j === i ? e.target.value : x)))} />)}</div>
        </Field>
        <div className="grid grid-cols-2 gap-3">
          <Field label={L("Encours mensuel moyen (EUR)", "Average monthly balance (EUR)")} htmlFor="bi"><Input id="bi" type="number" value={bill} onChange={(e) => setBill(e.target.value)} /></Field>
          <Field label={L("Remboursement mensuel moyen (EUR)", "Average monthly repayment (EUR)")} htmlFor="pa"><Input id="pa" type="number" value={paid} onChange={(e) => setPaid(e.target.value)} /></Field>
        </div>
      </div>
    </Dialog>
  );
}

export default function CreditPage() {
  const { t, locale } = useI18n();
  const L = useL();
  const can = useCan();
  const { data: me } = useMe();
  const [tab, setTab] = useState("applications");
  const [status, setStatus] = useState("");
  const [grade, setGrade] = useState("");
  const [q, setQ] = useState("");
  const [page, setPage] = useState(1);
  const [newOpen, setNewOpen] = useState(false);
  const ov = useQuery({ queryKey: ["credit", "overview"], queryFn: () => get<Overview>("/credit/overview") });
  const apps = useQuery({ queryKey: ["credit", "apps", status, grade, q, page], placeholderData: (p) => p,
    queryFn: () => get<{ total: number; items: Row[] }>(`/credit/applications${qs({ status, grade, q, page })}`) });
  const model = useQuery({ queryKey: ["credit", "model"], queryFn: () => get<ModelInfo>("/credit/model"), enabled: tab !== "applications" });
  const cols = useMemo<ColumnDef<Row, unknown>[]>(() => [
    { accessorKey: "reference", header: L("Référence", "Reference"), cell: ({ getValue }) => <span className="num">{String(getValue())}</span> },
    { id: "c", header: t("common.customer"), cell: ({ row }) => <div><p>{row.original.customer.name}</p><p className="text-xs text-ink-3">{row.original.customer.segment}</p></div> },
    { accessorKey: "requested_limit", header: L("Plafond demandé", "Requested limit"), meta: { align: "right" }, cell: ({ getValue }) => fmtMoney(Number(getValue()), locale, "EUR", 0) },
    { accessorKey: "pd", header: "PD", meta: { align: "right" }, cell: ({ getValue }) => getValue() == null ? "—" : fmtPct(Number(getValue()), locale, 1) },
    { accessorKey: "grade", header: "Grade", cell: ({ getValue }) => getValue() ? <Badge tone={gradeTone(String(getValue()))}>{String(getValue())}</Badge> : "—" },
    { accessorKey: "status", header: t("common.status"), cell: ({ row }) => <span className="text-ink-2">{row.original.status}{row.original.decision ? ` · ${row.original.decision}` : ""}</span> },
  ], [t, L, locale]);
  const o = ov.data;
  const m = model.data?.metrics;
  return (
    <div className="flex flex-col gap-6">
      <PageHeader title={t("nav.credit")} subtitle={L("Probabilité de défaut à 1 mois, facteurs explicatifs et simulations ; la décision appartient à l'analyste.", "1-month probability of default, explanatory factors and simulations; the decision belongs to the analyst.")}
        meta={me?.org.kind === "demo" ? <DemoTag /> : undefined}
        actions={can("credit.assess") ? <Button variant="primary" icon={<Plus className="size-4" />} onClick={() => setNewOpen(true)}>{L("Nouvelle demande", "New application")}</Button> : undefined} />
      {ov.error ? <ErrorState error={ov.error} /> : !o ? <LoadingBlock /> : (
        <>
          <Notice>{o.notice}</Notice>
          <Ledger items={[
            { label: L("Demandes", "Applications"), value: fmtNumber(Object.values(o.applications_by_status).reduce((a, b) => a + b, 0), locale) },
            { label: L("À décider", "Awaiting decision"), value: fmtNumber((o.applications_by_status.scored ?? 0) + (o.applications_by_status.under_review ?? 0), locale) },
            { label: L("PD moyenne", "Average PD"), value: fmtPct(o.average_pd, locale) },
            { label: L("Grades D–E", "Grades D–E"), value: fmtNumber((o.by_grade.D ?? 0) + (o.by_grade.E ?? 0), locale), tone: "high" },
            { label: L("Modèle", "Model"), value: o.model ? `v${o.model.version}` : "—", hint: o.model ? `AUC ${o.model.test_auc.toFixed(3)} · Gini ${o.model.test_gini.toFixed(3)} · Brier ${o.model.test_brier.toFixed(3)}` : undefined },
          ]} />
        </>
      )}
      <Tabs value={tab} onValueChange={setTab} items={[{ value: "applications", label: L("Demandes", "Applications") }, { value: "model", label: L("Performance et calibration", "Performance and calibration") }, { value: "fairness", label: L("Équité", "Fairness") }]}>
        <TabPanel value="applications" className="pt-4">
          <div className="grid gap-6 xl:grid-cols-[1fr_320px]">
            <Panel flush title={L("Portefeuille de demandes", "Application pipeline")} actions={
              <div className="flex gap-2">
                <Input className="h-8 w-40" placeholder={t("common.search")} value={q} onChange={(e) => { setQ(e.target.value); setPage(1); }} aria-label={t("common.search")} />
                <Select className="h-8 w-36" value={status} onChange={(e) => setStatus(e.target.value)} aria-label={t("common.status")}>
                  <option value="">{t("common.all")}</option><option value="scored">{L("Scorées", "Scored")}</option><option value="under_review">{L("En revue", "Under review")}</option><option value="decided">{L("Décidées", "Decided")}</option>
                </Select>
                <Select className="h-8 w-24" value={grade} onChange={(e) => setGrade(e.target.value)} aria-label="Grade">
                  <option value="">Grade</option>{["A", "B", "C", "D", "E"].map((g) => <option key={g}>{g}</option>)}
                </Select>
              </div>}>
              {apps.error ? <ErrorState error={apps.error} /> : <DataTable columns={cols} data={apps.data?.items} loading={apps.isLoading} total={apps.data?.total} page={page} onPageChange={setPage} rowHref={(r) => `/app/credit/applications/${r.id}`} />}
            </Panel>
            {o && <Panel title={L("Répartition par grade", "Grade distribution")}>
              <BarsChart data={Object.entries(o.by_grade).map(([g, n]) => ({ g: `${g} (${o.grade_bounds[g]})`, n }))} xKey="g" series={[{ key: "n", label: L("Demandes", "Applications") }]} height={220} />
            </Panel>}
          </div>
        </TabPanel>
        <TabPanel value="model" className="pt-4">
          {model.error ? <ErrorState error={model.error} /> : !m ? <LoadingBlock /> : (
            <div className="grid gap-6 lg:grid-cols-2">
              <Panel title={L("Comparaison des modèles (test)", "Model comparison (test)")} flush className="lg:col-span-2">
                <div className="overflow-x-auto"><table className="w-full text-sm">
                  <thead><tr className="border-b border-line text-left text-[12.5px] text-ink-3">{["Modèle", "Famille", "Calibration", "AUC val", "AUC test", "Gini", "PR-AUC", "Brier", "ECE", "KS", "PSI"].map((h) => <th key={h} className="px-3 py-2 font-medium">{h}</th>)}</tr></thead>
                  <tbody>{m.comparison.map((c) => (
                    <tr key={c.model} className="border-b border-line"><td className="px-3 py-1.5 font-medium">{c.model}{c.model === model.data?.algorithm.split(" ")[0] ? " ★" : ""}</td><td className="px-3 py-1.5 text-ink-2">{c.family}</td><td className="px-3 py-1.5">{c.calibration}</td>
                      {[c.val_auc, c.test_auc, c.test_gini, c.test_pr_auc, c.test_brier, c.test_ece, c.test_ks, c.psi_train_vs_test].map((v, i) => <td key={i} className="px-3 py-1.5 num">{v.toFixed(3)}</td>)}</tr>))}</tbody>
                </table></div>
              </Panel>
              <Panel title={L("Calibration (champion, test)", "Calibration (champion, test)")} description={L("PD moyenne prédite vs taux de défaut observé par décile", "Mean predicted PD vs observed default rate by decile")}>
                <BarsChart data={m.test.calibration.map((b, i) => ({ d: `D${i + 1}`, pred: b.p_mean, obs: b.y_rate }))} xKey="d"
                  series={[{ key: "pred", label: L("PD prédite", "Predicted PD") }, { key: "obs", label: L("Défaut observé", "Observed default") }]} fmt={(v) => fmtPct(v, locale, 0)} height={240} />
              </Panel>
              <Panel title={L("Grades sur l'échantillon de test", "Grades on the test sample")}>
                <KeyValue rows={m.grades_test.map((g) => [`${g.grade} (${g.pd_range})`, `${fmtNumber(g.n, locale)} · ${L("défaut observé", "observed default")} ${g.observed_default_rate == null ? "—" : fmtPct(g.observed_default_rate, locale)}`])} />
                <p className="mt-3 text-xs text-ink-3">{model.data?.model_card.horizon}</p>
              </Panel>
            </div>
          )}
        </TabPanel>
        <TabPanel value="fairness" className="pt-4">
          {!m ? <LoadingBlock /> : (
            <div className="flex flex-col gap-6">
              <Notice title={L("Attributs exclus du modèle", "Attributes excluded from the model")}>
                {model.data?.model_card.excluded_attributes.join(", ")}. {L(`AUC test sans ces attributs : ${m.protected_attributes_ablation.test_auc_policy_model.toFixed(3)} ; avec : ${m.protected_attributes_ablation.test_auc_with_protected.toFixed(3)}.`, `Test AUC without these attributes: ${m.protected_attributes_ablation.test_auc_policy_model.toFixed(3)}; with: ${m.protected_attributes_ablation.test_auc_with_protected.toFixed(3)}.`)}
              </Notice>
              {Object.entries(m.fairness).map(([attr, f]) => (
                <Panel key={attr} title={attr} description={L(`Écart de taux de revue : ${fmtPct(f.review_rate_gap, locale)} · écart d'égalité des chances : ${fmtPct(f.equal_opportunity_gap, locale)} · écart de calibration max : ${fmtPct(f.calibration_gap_max, locale)}`, `Review-rate gap: ${fmtPct(f.review_rate_gap, locale)} · equal-opportunity gap: ${fmtPct(f.equal_opportunity_gap, locale)} · max calibration gap: ${fmtPct(f.calibration_gap_max, locale)}`)} flush>
                  <div className="overflow-x-auto"><table className="w-full text-sm">
                    <thead><tr className="border-b border-line text-left text-[12.5px] text-ink-3">{[L("Groupe", "Group"), "n", L("Défaut observé", "Observed default"), L("PD moyenne", "Mean PD"), "AUC", L("Taux de revue", "Review rate"), "TPR"].map((h) => <th key={h} className="px-3 py-2 font-medium">{h}</th>)}</tr></thead>
                    <tbody>{f.groups.map((g) => (
                      <tr key={g.group} className="border-b border-line"><td className="px-3 py-1.5">{g.group}</td><td className="px-3 py-1.5 num">{g.n}</td>
                        <td className="px-3 py-1.5 num">{fmtPct(g.observed_default_rate, locale)}</td><td className="px-3 py-1.5 num">{fmtPct(g.mean_predicted_pd, locale)}</td>
                        <td className="px-3 py-1.5 num">{g.auc?.toFixed(3) ?? "—"}</td><td className="px-3 py-1.5 num">{fmtPct(g.review_rate_at_cutoff, locale)}</td><td className="px-3 py-1.5 num">{g.tpr_at_cutoff == null ? "—" : fmtPct(g.tpr_at_cutoff, locale)}</td></tr>))}</tbody>
                  </table></div>
                </Panel>
              ))}
            </div>
          )}
        </TabPanel>
      </Tabs>
      <NewApplicationDialog open={newOpen} onOpenChange={setNewOpen} />
    </div>
  );
}
