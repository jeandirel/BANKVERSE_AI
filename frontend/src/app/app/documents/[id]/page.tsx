"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Briefcase, Check, Download, Pencil, RefreshCw, Sparkles, X } from "lucide-react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useState } from "react";
import { ErrorState, KeyValue, LoadingBlock, Notice, PageHeader, Panel } from "@/components/ui/layout";
import { Dialog, Tabs, TabPanel, useToast } from "@/components/ui/overlay";
import { Badge, Button, Field, Input, Select, Textarea } from "@/components/ui/primitives";
import { DemoTag } from "@/components/ui/risk";
import { ApiError, download, get, patch, post } from "@/lib/api";
import { cn } from "@/lib/cn";
import { CHECK_LABELS, DOC_TYPE_LABELS, FIELD_LABELS, STATUS_LABELS, checkTone, label, statusTone } from "@/lib/domain/documents";
import { fmtDate, fmtNumber } from "@/lib/format";
import { useI18n, useL } from "@/lib/i18n";
import { useCan, useMe } from "@/lib/session";

type DocField = { id: string; name: string; value: string | null; normalized: string | null; confidence: number; page_no: number | null;
  bbox: [number, number, number, number] | null; required: boolean; source: string; corrected_at: string | null };
type Doc = { id: string; filename: string; mime_type: string; size_bytes: number; doc_type: string | null; confidence: number | null; status: string;
  page_count: number; ocr_used: boolean; created_at: string; is_demo: boolean; processing_ms: number | null; error: string | null;
  validated_by: string | null; validated_at: string | null; uploaded_by: string; sha256: string; engine: string; disclaimer: string;
  customer: { id: string; name: string; ref: string } | null;
  pages: { page_no: number; width: number; height: number; ocr_used: boolean; ocr_confidence: number | null; word_count: number; text: string;
    tables: { bbox: number[]; rows: (string | null)[][] }[] }[];
  fields: DocField[]; checks: { name: string; status: string; detail: string }[]; specimen?: { injected_anomalies: string[] } };

function PageViewer({ doc, pageNo, fields, active, onSelect }: { doc: Doc; pageNo: number; fields: DocField[]; active: string | null; onSelect: (id: string) => void }) {
  const L = useL();
  const { locale } = useI18n();
  const page = doc.pages.find((p) => p.page_no === pageNo);
  const [loaded, setLoaded] = useState(false);
  if (!page) return null;
  const boxes = fields.filter((f) => f.page_no === pageNo && f.bbox);
  return (
    <div className="relative mx-auto w-full max-w-[640px] overflow-hidden rounded-[var(--radius-control)] border border-line bg-white shadow-sm">
      {/* eslint-disable-next-line @next/next/no-img-element -- authenticated same-origin image, not optimisable */}
      <img src={`/api/v1/documents/${doc.id}/pages/${pageNo}/image`} alt={L(`Page ${pageNo} du document`, `Page ${pageNo} of the document`)}
        className="block w-full h-auto" onLoad={() => setLoaded(true)} />
      {!loaded && <div className="absolute inset-0 grid place-items-center bg-surface-2"><LoadingBlock /></div>}
      {loaded && boxes.map((f) => {
        const [x0, y0, x1, y1] = f.bbox as number[];
        const on = active === f.id;
        return (
          <button key={f.id} type="button" onClick={() => onSelect(f.id)}
            title={`${label(FIELD_LABELS, f.name, locale)} : ${f.value ?? ""}`}
            aria-label={`${label(FIELD_LABELS, f.name, locale)} : ${f.value ?? ""}`}
            className={cn("absolute rounded-[2px] border-2 transition-colors", on ? "border-accent bg-accent/20" : "border-info/60 bg-info/5 hover:bg-info/15")}
            style={{ left: `${(x0 / page.width) * 100 - 0.4}%`, top: `${(y0 / page.height) * 100 - 0.3}%`,
              width: `${((x1 - x0) / page.width) * 100 + 0.8}%`, height: `${((y1 - y0) / page.height) * 100 + 0.6}%` }} />
        );
      })}
    </div>
  );
}

function FieldRow({ f, editable, active, onSelect, docId }: { f: DocField; editable: boolean; active: boolean; onSelect: () => void; docId: string }) {
  const L = useL();
  const { locale } = useI18n();
  const qc = useQueryClient();
  const toast = useToast();
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState(f.value ?? "");
  const save = useMutation({
    mutationFn: () => patch<Doc>(`/documents/${docId}/fields/${f.id}`, { value }),
    onSuccess: (d) => { qc.setQueryData(["documents", docId], d); setEditing(false); toast(L("Champ corrigé, contrôles recalculés", "Field corrected, controls recomputed")); },
    onError: (e) => toast(e instanceof ApiError ? e.message : String(e), "error"),
  });
  const low = f.normalized != null && f.confidence < 0.6;
  return (
    <li className={cn("border-b border-line px-4 py-2.5 last:border-b-0", active && "bg-accent-soft")}>
      <div className="flex items-start gap-3">
        <button type="button" onClick={onSelect} className="min-w-0 flex-1 text-left" disabled={!f.bbox} aria-pressed={active}>
          <p className="text-xs text-ink-3">{label(FIELD_LABELS, f.name, locale)}{f.required ? " *" : ""}</p>
          {!editing && <p className={cn("text-sm break-words", f.normalized == null ? "text-risk-high" : "text-ink")}>
            {f.value ?? L("Non trouvé", "Not found")}
            {f.normalized != null && f.normalized !== f.value && <span className="ml-2 text-xs text-ink-3 num">→ {f.normalized}</span>}
          </p>}
        </button>
        {!editing && <div className="flex items-center gap-1.5 shrink-0">
          {f.source === "corrected" ? <Badge tone="accent">{L("Corrigé", "Corrected")}</Badge>
            : <Badge tone={low ? "medium" : "neutral"}>{Math.round(f.confidence * 100)} %</Badge>}
          {editable && <Button size="sm" variant="ghost" aria-label={L("Corriger", "Correct")} onClick={() => { setValue(f.value ?? ""); setEditing(true); }}><Pencil className="size-3.5" /></Button>}
        </div>}
      </div>
      {editing && <form className="mt-2 flex gap-2" onSubmit={(e) => { e.preventDefault(); save.mutate(); }}>
        <Input autoFocus value={value} onChange={(e) => setValue(e.target.value)} aria-label={label(FIELD_LABELS, f.name, locale)} className="h-8" />
        <Button size="sm" variant="primary" type="submit" loading={save.isPending} aria-label={L("Enregistrer", "Save")}><Check className="size-4" /></Button>
        <Button size="sm" type="button" onClick={() => setEditing(false)} aria-label={L("Annuler", "Cancel")}><X className="size-4" /></Button>
      </form>}
    </li>
  );
}

export default function DocumentDetailPage() {
  const { id } = useParams<{ id: string }>();
  const { locale } = useI18n();
  const L = useL();
  const can = useCan();
  const { data: me } = useMe();
  const router = useRouter();
  const qc = useQueryClient();
  const toast = useToast();
  const [pageNo, setPageNo] = useState(1);
  const [active, setActive] = useState<string | null>(null);
  const [tab, setTab] = useState("fields");
  const [review, setReview] = useState<null | "validated" | "rejected">(null);
  const [comment, setComment] = useState("");
  const [forced, setForced] = useState("");
  const doc = useQuery({ queryKey: ["documents", id], queryFn: () => get<Doc>(`/documents/${id}`) });
  const onErr = (e: unknown) => toast(e instanceof ApiError ? e.message : String(e), "error");
  const done = (d: Doc) => { qc.setQueryData(["documents", id], d); qc.invalidateQueries({ queryKey: ["documents", "list"] }); qc.invalidateQueries({ queryKey: ["documents", "overview"] }); };
  const reprocess = useMutation({ mutationFn: () => post<Doc>(`/documents/${id}/reprocess`, forced ? { doc_type: forced } : undefined), onSuccess: (d) => { done(d); toast(L("Document retraité", "Document reprocessed")); }, onError: onErr });
  const decide = useMutation({ mutationFn: () => post<Doc>(`/documents/${id}/review`, { decision: review, comment }),
    onSuccess: (d) => { done(d); setReview(null); setComment(""); toast(d.status === "validated" ? L("Document validé", "Document validated") : L("Document rejeté", "Document rejected")); }, onError: onErr });
  const toCase = useMutation({ mutationFn: () => post<{ case_id: string; reference: string }>(`/documents/${id}/case`), onSuccess: (r) => router.push(`/app/cases/${r.case_id}`), onError: onErr });
  const analyze = useMutation({ mutationFn: () => post(`/documents/${id}/analyze`), onError: onErr });
  if (doc.error) return <ErrorState error={doc.error} onRetry={() => doc.refetch()} />;
  const d = doc.data;
  if (!d) return <LoadingBlock />;
  const editable = can("documents.validate") && d.status === "needs_review";
  const fails = d.checks.filter((c) => c.status === "fail").length;
  const page = d.pages.find((p) => p.page_no === pageNo);
  return (
    <div className="flex flex-col gap-6">
      <Link href="/app/documents" className="inline-flex items-center gap-1.5 text-sm text-ink-2 hover:text-ink w-fit"><ArrowLeft className="size-4" />{L("Documents", "Documents")}</Link>
      <PageHeader title={d.doc_type ? label(DOC_TYPE_LABELS, d.doc_type, locale) : L("Document non classé", "Unclassified document")}
        subtitle={`${d.filename} · ${d.page_count} p. · ${fmtNumber(d.size_bytes / 1024, locale, 0)} Ko · ${L("importé le", "uploaded")} ${fmtDate(d.created_at, locale, true)}`}
        meta={<div className="flex items-center gap-2"><Badge tone={statusTone(d.status)}>{label(STATUS_LABELS, d.status, locale)}</Badge>{(d.is_demo || me?.org.kind === "demo") && <DemoTag />}</div>}
        actions={<div className="flex flex-wrap gap-2">
          <Button icon={<Download className="size-4" />} onClick={() => download(`/documents/${id}/file`, d.filename).catch(onErr)}>{L("Original", "Original")}</Button>
          {can("cases.write") && <Button icon={<Briefcase className="size-4" />} loading={toCase.isPending} onClick={() => toCase.mutate()}>{L("Ouvrir un dossier", "Open a case")}</Button>}
          {editable && <>
            <Button icon={<X className="size-4" />} onClick={() => setReview("rejected")}>{L("Rejeter", "Reject")}</Button>
            <Button variant="primary" icon={<Check className="size-4" />} onClick={() => setReview("validated")}>{L("Valider", "Validate")}</Button>
          </>}
        </div>} />
      <Notice>{d.disclaimer}</Notice>
      {d.status === "failed" && <Notice tone="warn" title={L("Traitement en échec", "Processing failed")}>{d.error}</Notice>}
      <div className="grid gap-6 xl:grid-cols-[minmax(0,1.15fr)_minmax(360px,1fr)]">
        <Panel title={L("Aperçu", "Preview")} description={page ? (page.ocr_used
          ? L(`Page ${pageNo} · texte reconnu par OCR (confiance moyenne ${page.ocr_confidence ?? "—"})`, `Page ${pageNo} · text recognised by OCR (mean confidence ${page.ocr_confidence ?? "—"})`)
          : L(`Page ${pageNo} · couche texte native (${page.word_count} mots)`, `Page ${pageNo} · native text layer (${page.word_count} words)`)) : undefined}
          actions={d.page_count > 1 ? <Select className="h-8 w-28" value={pageNo} onChange={(e) => setPageNo(Number(e.target.value))} aria-label="Page">
            {d.pages.map((p) => <option key={p.page_no} value={p.page_no}>Page {p.page_no}</option>)}</Select> : undefined}>
          {d.pages.length ? <PageViewer doc={d} pageNo={pageNo} fields={d.fields} active={active} onSelect={(fid) => { setActive(fid); setTab("fields"); }} />
            : <p className="text-sm text-ink-3">{L("Aucune page exploitable.", "No usable page.")}</p>}
        </Panel>
        <div className="flex flex-col gap-6 min-w-0">
          <Tabs value={tab} onValueChange={setTab} items={[
            { value: "fields", label: L(`Champs (${d.fields.length})`, `Fields (${d.fields.length})`) },
            { value: "checks", label: fails ? L(`Contrôles · ${fails} échec`, `Controls · ${fails} failed`) : L("Contrôles", "Controls") },
            { value: "text", label: L("Texte", "Text") }, { value: "info", label: L("Traçabilité", "Traceability") }]}>
            <TabPanel value="fields" className="pt-4">
              <Panel flush>
                {d.fields.length === 0 ? <p className="px-4 py-6 text-sm text-ink-3">{L("Aucun champ : choisissez le type de document puis retraitez-le.", "No fields: choose the document type, then reprocess it.")}</p> :
                  <ul>{d.fields.map((f) => <FieldRow key={f.id} f={f} docId={d.id} editable={editable} active={active === f.id} onSelect={() => { setActive(f.id); if (f.page_no) setPageNo(f.page_no); }} />)}</ul>}
              </Panel>
            </TabPanel>
            <TabPanel value="checks" className="pt-4">
              <Panel flush>
                <ul>{d.checks.map((c) => (
                  <li key={c.name} className="flex items-start gap-3 border-b border-line px-4 py-3 last:border-b-0">
                    <Badge tone={checkTone(c.status)} className="mt-0.5 w-16 justify-center">{c.status === "pass" ? "OK" : c.status === "warn" ? L("À voir", "Check") : L("Échec", "Fail")}</Badge>
                    <div className="min-w-0"><p className="text-sm font-medium">{label(CHECK_LABELS, c.name, locale)}</p><p className="text-sm text-ink-2 break-words">{c.detail}</p></div>
                  </li>))}</ul>
              </Panel>
            </TabPanel>
            <TabPanel value="text" className="pt-4">
              <Panel>
                <pre className="max-h-[520px] overflow-auto whitespace-pre-wrap text-[12.5px] leading-relaxed text-ink-2 font-[inherit]">{page?.text || L("Aucun texte extrait.", "No text extracted.")}</pre>
                {page && page.tables.length > 0 && <p className="mt-3 text-xs text-ink-3">{L(`${page.tables.length} tableau(x) détecté(s) sur cette page.`, `${page.tables.length} table(s) detected on this page.`)}</p>}
              </Panel>
            </TabPanel>
            <TabPanel value="info" className="pt-4">
              <Panel>
                <KeyValue rows={[
                  [L("Empreinte SHA-256", "SHA-256 fingerprint"), <span key="s" className="num text-xs break-all">{d.sha256}</span>],
                  [L("Moteur", "Engine"), d.engine === "local" ? L("Local (PyMuPDF, OpenCV, Tesseract)", "Local (PyMuPDF, OpenCV, Tesseract)") : d.engine],
                  [L("Classification", "Classification"), d.confidence != null ? `${Math.round(d.confidence * 100)} %` : "—"],
                  [L("Durée de traitement", "Processing time"), d.processing_ms != null ? `${fmtNumber(d.processing_ms / 1000, locale, 1)} s` : "—"],
                  [L("Client rattaché", "Linked customer"), d.customer ? <Link key="c" className="text-accent underline-offset-2 hover:underline" href={`/app/customers/${d.customer.id}`}>{d.customer.name}</Link> : "—"],
                  [L("Revu le", "Reviewed on"), d.validated_at ? fmtDate(d.validated_at, locale, true) : "—"],
                ]} />
              </Panel>
            </TabPanel>
          </Tabs>
          {can("documents.upload") && d.status !== "validated" && <Panel title={L("Retraiter", "Reprocess")} description={L("Forcez le type si la classification est erronée ; les corrections manuelles sont conservées.", "Force the type if the classification is wrong; manual corrections are kept.")}>
            <div className="flex flex-wrap gap-2">
              <Select className="h-9 w-56" value={forced} onChange={(e) => setForced(e.target.value)} aria-label={L("Type forcé", "Forced type")}>
                <option value="">{L("Type détecté automatiquement", "Automatically detected type")}</option>
                {Object.keys(DOC_TYPE_LABELS).map((k) => <option key={k} value={k}>{label(DOC_TYPE_LABELS, k, locale)}</option>)}
              </Select>
              <Button icon={<RefreshCw className="size-4" />} loading={reprocess.isPending} onClick={() => reprocess.mutate()}>{L("Retraiter", "Reprocess")}</Button>
              <Button variant="ghost" icon={<Sparkles className="size-4" />} loading={analyze.isPending} onClick={() => analyze.mutate()}>{L("Analyse IA générative", "Generative AI analysis")}</Button>
            </div>
          </Panel>}
        </div>
      </div>
      <Dialog open={review !== null} onOpenChange={(v) => !v && setReview(null)}
        title={review === "validated" ? L("Valider le document", "Validate the document") : L("Rejeter le document", "Reject the document")}
        description={review === "validated"
          ? L("Vous attestez avoir vérifié les champs extraits. La personne qui a importé le document ne peut pas le valider (quatre yeux).", "You confirm you have checked the extracted fields. The person who uploaded the document cannot validate it (four eyes).")
          : L("Indiquez le motif du rejet ; il est conservé dans le journal d'audit.", "State the reason for rejection; it is kept in the audit log.")}
        footer={<><Button onClick={() => setReview(null)}>{L("Annuler", "Cancel")}</Button>
          <Button variant={review === "rejected" ? "danger" : "primary"} disabled={comment.trim().length < 3} loading={decide.isPending} onClick={() => decide.mutate()}>
            {review === "validated" ? L("Valider", "Validate") : L("Rejeter", "Reject")}</Button></>}>
        <Field label={L("Commentaire", "Comment")} htmlFor="rv-c"><Textarea id="rv-c" rows={3} value={comment} onChange={(e) => setComment(e.target.value)} /></Field>
        {review === "validated" && fails > 0 && <div className="mt-3"><Notice tone="warn">{L(`${fails} contrôle(s) en échec : justifiez la validation dans le commentaire.`, `${fails} failed control(s): justify the validation in the comment.`)}</Notice></div>}
      </Dialog>
    </div>
  );
}
