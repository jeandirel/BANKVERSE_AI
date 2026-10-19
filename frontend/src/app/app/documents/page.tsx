"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { ColumnDef } from "@tanstack/react-table";
import { FilePlus2, ScanLine, Upload } from "lucide-react";
import { useRouter } from "next/navigation";
import { useMemo, useRef, useState } from "react";
import { BarsChart } from "@/components/ui/charts";
import { DataTable } from "@/components/ui/data-table";
import { ErrorState, Ledger, LoadingBlock, Notice, PageHeader, Panel } from "@/components/ui/layout";
import { Dialog, useToast } from "@/components/ui/overlay";
import { Badge, Button, Field, Input, Select, Switch } from "@/components/ui/primitives";
import { DemoTag } from "@/components/ui/risk";
import { ApiError, api, get, post, qs } from "@/lib/api";
import { CHECK_LABELS, DOC_TYPE_LABELS, STATUS_LABELS, label, statusTone } from "@/lib/domain/documents";
import { fmtDate, fmtNumber } from "@/lib/format";
import { useI18n, useL } from "@/lib/i18n";
import { useCan, useMe } from "@/lib/session";

type Row = { id: string; filename: string; doc_type: string | null; confidence: number | null; status: string; page_count: number;
  ocr_used: boolean; created_at: string; is_demo: boolean; checks_failed: number; checks_warn: number; processing_ms: number | null };
type Overview = { by_status: Record<string, number>; by_type: Record<string, number>; failing_checks: { check: string; count: number }[];
  avg_processing_ms: number | null; ocr_documents: number; disclaimer: string;
  classifier: { deployed: boolean; model: string | null; reject_threshold: number | null } };

function UploadDialog({ open, onOpenChange, demoOrg }: { open: boolean; onOpenChange: (v: boolean) => void; demoOrg: boolean }) {
  const L = useL();
  const router = useRouter();
  const toast = useToast();
  const input = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [drag, setDrag] = useState(false);
  const send = useMutation({
    mutationFn: () => {
      const fd = new FormData();
      fd.append("file", file as File);
      return api<{ id: string }>("/documents", { method: "POST", body: fd });
    },
    onSuccess: (r) => router.push(`/app/documents/${r.id}`),
    onError: (e) => {
      if (e instanceof ApiError && e.code === "duplicate_document" && typeof e.details.document_id === "string") {
        toast(L("Ce fichier existe déjà : ouverture du document existant.", "This file already exists: opening the existing document."), "info");
        router.push(`/app/documents/${e.details.document_id}`);
        return;
      }
      toast(e instanceof ApiError ? e.message : String(e), "error");
    },
  });
  return (
    <Dialog open={open} onOpenChange={onOpenChange} title={L("Importer un justificatif", "Upload a supporting document")}
      description={L("PDF, PNG, JPEG ou TIFF, 15 Mo maximum. Les PDF chiffrés ou contenant du JavaScript sont refusés.", "PDF, PNG, JPEG or TIFF, 15 MB max. Encrypted PDFs or PDFs with JavaScript are refused.")}
      footer={<><Button onClick={() => onOpenChange(false)}>{L("Annuler", "Cancel")}</Button>
        <Button variant="primary" disabled={!file} loading={send.isPending} onClick={() => send.mutate()}>{L("Importer et analyser", "Upload and analyse")}</Button></>}>
      <div className="flex flex-col gap-3">
        {demoOrg && <Notice tone="demo" title={L("Environnement de démonstration", "Demo environment")}>
          {L("N'importez aucun vrai document (pièce d'identité, relevé, bulletin…). Seuls les spécimens DEMO sont acceptés ici ; utilisez « Générer un spécimen ».", "Do not upload any real document (ID, statement, payslip…). Only DEMO specimens are accepted here; use “Generate a specimen”.")}
        </Notice>}
        <button type="button" onClick={() => input.current?.click()}
          onDragOver={(e) => { e.preventDefault(); setDrag(true); }} onDragLeave={() => setDrag(false)}
          onDrop={(e) => { e.preventDefault(); setDrag(false); const f = e.dataTransfer.files[0]; if (f) setFile(f); }}
          className={`flex flex-col items-center gap-2 rounded-[var(--radius-panel)] border border-dashed px-4 py-8 text-sm transition-colors ${drag ? "border-accent bg-accent-soft" : "border-line-strong bg-surface-2"}`}>
          <Upload className="size-5 text-ink-3" aria-hidden />
          <span className="text-ink">{file ? file.name : L("Déposez un fichier ou cliquez pour parcourir", "Drop a file or click to browse")}</span>
          {file && <span className="text-xs text-ink-3">{fmtNumber(file.size / 1024, "fr", 0)} Ko</span>}
        </button>
        <input ref={input} type="file" className="sr-only" accept=".pdf,.png,.jpg,.jpeg,.tif,.tiff" onChange={(e) => setFile(e.target.files?.[0] ?? null)} aria-label={L("Fichier", "File")} />
      </div>
    </Dialog>
  );
}

function SpecimenDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (v: boolean) => void }) {
  const L = useL();
  const { locale } = useI18n();
  const router = useRouter();
  const toast = useToast();
  const [docType, setDocType] = useState("bank_statement");
  const [scanned, setScanned] = useState(false);
  const [anomaly, setAnomaly] = useState(false);
  const gen = useMutation({
    mutationFn: () => post<{ id: string }>("/documents/specimens", { doc_type: docType, scanned, with_anomaly: anomaly }),
    onSuccess: (r) => router.push(`/app/documents/${r.id}`),
    onError: (e) => toast(e instanceof ApiError ? e.message : String(e), "error"),
  });
  return (
    <Dialog open={open} onOpenChange={onOpenChange} title={L("Générer un spécimen DEMO", "Generate a DEMO specimen")}
      description={L("Document fictif filigrané « SPECIMEN — DEMO », traité exactement comme un import.", "Fictitious document watermarked “SPECIMEN — DEMO”, processed exactly like an upload.")}
      footer={<><Button onClick={() => onOpenChange(false)}>{L("Annuler", "Cancel")}</Button>
        <Button variant="primary" loading={gen.isPending} onClick={() => gen.mutate()}>{L("Générer et analyser", "Generate and analyse")}</Button></>}>
      <div className="flex flex-col gap-4">
        <Field label={L("Type de document", "Document type")} htmlFor="sp-type">
          <Select id="sp-type" value={docType} onChange={(e) => setDocType(e.target.value)}>
            {Object.keys(DOC_TYPE_LABELS).map((k) => <option key={k} value={k}>{label(DOC_TYPE_LABELS, k, locale)}</option>)}
          </Select>
        </Field>
        <Switch showLabel checked={scanned} onChange={setScanned} label={L("Version scannée (photo dégradée → OCR)", "Scanned version (degraded photo → OCR)")} />
        <Switch showLabel checked={anomaly} onChange={setAnomaly} label={L("Injecter une anomalie (IBAN, totaux, SIREN, date…)", "Inject an anomaly (IBAN, totals, SIREN, date…)")} />
        {scanned && <p className="text-xs text-ink-3">{L("L'OCR prend quelques secondes.", "OCR takes a few seconds.")}</p>}
      </div>
    </Dialog>
  );
}

export default function DocumentsPage() {
  const { t, locale } = useI18n();
  const L = useL();
  const can = useCan();
  const { data: me } = useMe();
  const qc = useQueryClient();
  const [status, setStatus] = useState("");
  const [docType, setDocType] = useState("");
  const [q, setQ] = useState("");
  const [page, setPage] = useState(1);
  const [uploadOpen, setUploadOpen] = useState(false);
  const [specOpen, setSpecOpen] = useState(false);
  const ov = useQuery({ queryKey: ["documents", "overview"], queryFn: () => get<Overview>("/documents/overview") });
  const list = useQuery({ queryKey: ["documents", "list", status, docType, q, page], placeholderData: (p) => p,
    queryFn: () => get<{ total: number; items: Row[] }>(`/documents${qs({ status, doc_type: docType, q, page })}`) });
  const cols = useMemo<ColumnDef<Row, unknown>[]>(() => [
    { accessorKey: "filename", header: L("Fichier", "File"), cell: ({ row }) => (
      <div className="min-w-0"><p className="truncate max-w-[280px]">{row.original.filename}</p>
        <p className="text-xs text-ink-3">{row.original.page_count} p.{row.original.ocr_used ? " · OCR" : ""}{row.original.is_demo ? " · DEMO" : ""}</p></div>) },
    { accessorKey: "doc_type", header: L("Type", "Type"), cell: ({ row }) => row.original.doc_type
      ? <span>{label(DOC_TYPE_LABELS, row.original.doc_type, locale)}<span className="ml-1.5 text-xs text-ink-3 num">{row.original.confidence != null ? `${Math.round(row.original.confidence * 100)} %` : ""}</span></span>
      : <span className="text-ink-3">{L("Non reconnu", "Unrecognised")}</span> },
    { accessorKey: "status", header: t("common.status"), cell: ({ getValue }) => <Badge tone={statusTone(String(getValue()))}>{label(STATUS_LABELS, String(getValue()), locale)}</Badge> },
    { id: "checks", header: L("Contrôles", "Controls"), cell: ({ row }) => row.original.checks_failed + row.original.checks_warn === 0
      ? <span className="text-ink-3">{L("Aucune alerte", "No alert")}</span>
      : <div className="flex gap-1.5">{row.original.checks_failed > 0 && <Badge tone="high">{row.original.checks_failed} {L("échec", "failed")}</Badge>}
        {row.original.checks_warn > 0 && <Badge tone="medium">{row.original.checks_warn} {L("à vérifier", "to check")}</Badge>}</div> },
    { accessorKey: "created_at", header: L("Importé", "Uploaded"), cell: ({ getValue }) => <span className="text-ink-2">{fmtDate(String(getValue()), locale, true)}</span> },
  ], [t, L, locale]);
  const o = ov.data;
  const total = o ? Object.values(o.by_status).reduce((a, b) => a + b, 0) : 0;
  return (
    <div className="flex flex-col gap-6">
      <PageHeader title={t("nav.documents")}
        subtitle={L("Classement, extraction et contrôles de cohérence des justificatifs KYC / KYB, validés par un analyste.", "Classification, extraction and consistency controls for KYC / KYB supporting documents, validated by an analyst.")}
        meta={me?.org.kind === "demo" ? <DemoTag /> : undefined}
        actions={can("documents.upload") ? <div className="flex gap-2">
          <Button icon={<ScanLine className="size-4" />} onClick={() => setSpecOpen(true)}>{L("Générer un spécimen", "Generate a specimen")}</Button>
          <Button variant="primary" icon={<FilePlus2 className="size-4" />} onClick={() => setUploadOpen(true)}>{L("Importer", "Upload")}</Button>
        </div> : undefined} />
      {ov.error ? <ErrorState error={ov.error} onRetry={() => qc.invalidateQueries({ queryKey: ["documents"] })} /> : !o ? <LoadingBlock /> : (
        <>
          <Notice>{o.disclaimer}</Notice>
          <Ledger items={[
            { label: L("Documents", "Documents"), value: fmtNumber(total, locale) },
            { label: L("À revoir", "Needs review"), value: fmtNumber(o.by_status.needs_review ?? 0, locale), tone: (o.by_status.needs_review ?? 0) > 0 ? "medium" : undefined },
            { label: L("Validés", "Validated"), value: fmtNumber(o.by_status.validated ?? 0, locale) },
            { label: L("Contrôles en échec", "Failed controls"), value: fmtNumber(o.failing_checks.reduce((a, b) => a + b.count, 0), locale), tone: "high" },
            { label: L("Classifieur", "Classifier"), value: o.classifier.deployed ? (o.classifier.model ?? "—") : L("Non déployé", "Not deployed"),
              hint: o.avg_processing_ms ? L(`Traitement moyen ${fmtNumber(o.avg_processing_ms / 1000, locale, 1)} s`, `Average processing ${fmtNumber(o.avg_processing_ms / 1000, locale, 1)} s`) : undefined },
          ]} />
          {!o.classifier.deployed && <Notice tone="warn" title={L("Classification automatique indisponible", "Automatic classification unavailable")}>
            {L("Aucune version du classifieur n'est approuvée et déployée : le type de chaque document doit être choisi manuellement.", "No classifier version is approved and deployed: each document's type must be chosen manually.")}
          </Notice>}
        </>
      )}
      <div className="grid gap-6 xl:grid-cols-[1fr_320px]">
        <Panel flush title={L("File de revue", "Review queue")} actions={
          <div className="flex flex-wrap gap-2">
            <Input className="h-8 w-44" placeholder={L("Texte, nom de fichier…", "Text, file name…")} value={q} onChange={(e) => { setQ(e.target.value); setPage(1); }} aria-label={t("common.search")} />
            <Select className="h-8 w-36" value={status} onChange={(e) => { setStatus(e.target.value); setPage(1); }} aria-label={t("common.status")}>
              <option value="">{t("common.all")}</option>
              {Object.keys(STATUS_LABELS).map((s) => <option key={s} value={s}>{label(STATUS_LABELS, s, locale)}</option>)}
            </Select>
            <Select className="h-8 w-44" value={docType} onChange={(e) => { setDocType(e.target.value); setPage(1); }} aria-label={L("Type", "Type")}>
              <option value="">{L("Tous les types", "All types")}</option>
              {Object.keys(DOC_TYPE_LABELS).map((k) => <option key={k} value={k}>{label(DOC_TYPE_LABELS, k, locale)}</option>)}
              <option value="unknown">{L("Non reconnu", "Unrecognised")}</option>
            </Select>
          </div>}>
          {list.error ? <ErrorState error={list.error} /> : <DataTable columns={cols} data={list.data?.items} loading={list.isLoading} total={list.data?.total}
            page={page} onPageChange={setPage} rowHref={(r) => `/app/documents/${r.id}`}
            empty={<div className="px-4 py-10 text-center text-sm text-ink-3">{L("Aucun document. Importez un fichier ou générez un spécimen DEMO.", "No documents. Upload a file or generate a DEMO specimen.")}</div>} />}
        </Panel>
        {o && <Panel title={L("Contrôles en échec", "Failed controls")} description={L("Nombre de documents par contrôle", "Documents per control")}>
          {o.failing_checks.length === 0 ? <p className="text-sm text-ink-3">{L("Aucun contrôle en échec.", "No failed control.")}</p> :
            <BarsChart horizontal data={o.failing_checks.map((c) => ({ c: label(CHECK_LABELS, c.check, locale), n: c.count }))} xKey="c"
              series={[{ key: "n", label: L("Documents", "Documents") }]} height={Math.max(160, o.failing_checks.length * 34)} />}
        </Panel>}
      </div>
      <UploadDialog open={uploadOpen} onOpenChange={setUploadOpen} demoOrg={me?.org.kind === "demo"} />
      <SpecimenDialog open={specOpen} onOpenChange={setSpecOpen} />
    </div>
  );
}
