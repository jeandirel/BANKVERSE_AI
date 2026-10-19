"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { BookOpen, Calculator, FileUp, MessageSquarePlus, SendHorizontal, ShieldAlert, Trash2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { ErrorState, KeyValue, LoadingBlock, Notice, PageHeader, Panel } from "@/components/ui/layout";
import { Dialog, Tabs, TabPanel, useToast } from "@/components/ui/overlay";
import { Badge, Button, Field, Input, Select, Textarea } from "@/components/ui/primitives";
import { DemoTag } from "@/components/ui/risk";
import { ApiError, api, del, get, post } from "@/lib/api";
import { cn } from "@/lib/cn";
import { fmtDate, fmtNumber, fmtPct } from "@/lib/format";
import { useI18n, useL } from "@/lib/i18n";
import { useCan, useMe } from "@/lib/session";

type Citation = { n: number; chunk_id: string; scope: "public" | "private"; title: string; company: string | null; fiscal_year: number | null;
  page: number | null; kind: string; content: string; score: number };
type Msg = { id: string; role: "user" | "assistant"; content: string; citations: Citation[]; status: string; mode: string; latency_ms: number | null;
  created_at: string; meta: Record<string, unknown> };
type Status = { retriever: { deployed: boolean; version: number | null; test_recall_at_3: number | null }; public_corpus: { pages: number; chunks: number; embedded: number };
  private_kb: { documents: number; chunks: number }; llm: { available: boolean; provider: string | null }; disclaimer: string };
type Source = { scope: string; title: string; company?: string; fiscal_year?: number; page?: number; license?: string; highlight: string;
  chunks: { id: string; kind: string; content: string }[] };
type Evaluation = { version: number; status: string; metrics: { fact_retrieval: Record<string, Record<string, { "recall@3": number; "recall@5": number; "all_gold@3": number; mrr: number }>>;
  page_retrieval_test: Record<string, Record<string, number> | number>; calculator_gold_programs: Record<string, { n: number; accuracy: number }> }; model_card: { limitations: string[] } };

const NUM_RX = /-?\$?\s?\d[\d,]*(?:\.\d+)?\s?%?/g;

function SourcePanel({ cite, onClose, onPickNumber }: { cite: Citation; onClose: () => void; onPickNumber: (n: string) => void }) {
  const L = useL();
  const src = useQuery({ queryKey: ["copilot", "source", cite.scope, cite.chunk_id], queryFn: () => get<Source>(`/copilot/sources/${cite.scope}/${cite.chunk_id}`) });
  const ref = useRef<HTMLLIElement>(null);
  useEffect(() => { ref.current?.scrollIntoView({ block: "center" }); }, [src.data]);
  return (
    <Panel title={L(`Source [${cite.n}]`, `Source [${cite.n}]`)} description={cite.title} actions={<Button size="sm" variant="ghost" onClick={onClose}>{L("Fermer", "Close")}</Button>} flush>
      {src.error ? <ErrorState error={src.error} /> : !src.data ? <LoadingBlock /> : (
        <div>
          {src.data.license && <p className="px-4 pt-3 text-xs text-ink-3">{src.data.license}</p>}
          <ol className="max-h-[60vh] overflow-y-auto scroll-thin px-4 py-3 text-[13px] leading-relaxed">
            {src.data.chunks.map((c) => (
              <li key={c.id} ref={c.id === src.data.highlight ? ref : undefined}
                className={cn("rounded px-2 py-1.5", c.id === src.data.highlight ? "bg-accent-soft ring-1 ring-accent/40" : "text-ink-2", c.kind !== "text" && "font-[450]")}>
                {c.content}
              </li>
            ))}
          </ol>
          <div className="border-t border-line px-4 py-2.5">
            <p className="text-xs text-ink-3 mb-1.5">{L("Ajouter un nombre du passage cité à la calculatrice :", "Add a number from the cited passage to the calculator:")}</p>
            <div className="flex flex-wrap gap-1">
              {Array.from(new Set((cite.content.match(NUM_RX) ?? []).map((x) => x.replace(/[$\s,]/g, "")))).slice(0, 14).map((n) => (
                <button key={n} type="button" onClick={() => onPickNumber(n)} className="num rounded border border-line px-1.5 py-0.5 text-xs hover:border-accent hover:text-accent">{n}</button>
              ))}
            </div>
          </div>
        </div>
      )}
    </Panel>
  );
}

function CalculatorPanel({ expr, setExpr }: { expr: string; setExpr: (v: string) => void }) {
  const L = useL();
  const { locale } = useI18n();
  const calc = useMutation({ mutationFn: () => post<{ result: number }>("/copilot/calculate", { expression: expr }) });
  return (
    <Panel title={L("Calculatrice sûre", "Safe calculator")} description={L("Opérations + − × ÷ %, parenthèses ; aucun code exécuté.", "Operations + − × ÷ %, parentheses; no code executed.")}>
      <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); if (expr.trim()) calc.mutate(); }}>
        <Input value={expr} onChange={(e) => setExpr(e.target.value)} placeholder="(5735 - 5829) / 5829 * 100" aria-label={L("Expression", "Expression")} className="num" />
        <Button type="submit" loading={calc.isPending}>=</Button>
      </form>
      {calc.data && <p className="mt-2 num text-lg font-semibold">{fmtNumber(calc.data.result, locale, 4)}</p>}
      {calc.error && <p className="mt-2 text-sm text-risk-high">{calc.error instanceof ApiError ? calc.error.message : String(calc.error)}</p>}
    </Panel>
  );
}

function EvaluationDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (v: boolean) => void }) {
  const L = useL();
  const { locale } = useI18n();
  const ev = useQuery({ queryKey: ["copilot", "evaluation"], queryFn: () => get<Evaluation>("/copilot/evaluation"), enabled: open });
  const m = ev.data?.metrics;
  const names: Record<string, string> = { bm25: "BM25", tfidf_cosine: "TF-IDF", lsa_embedding: L("Embeddings LSA", "LSA embeddings"), hybrid_rrf: L("Hybride RRF", "Hybrid RRF"), reranker_lambdarank: L("Reranker appris", "Learned reranker") };
  return (
    <Dialog wide open={open} onOpenChange={onOpenChange} title={L("Évaluation du copilote", "Copilot evaluation")}
      description={L("FinQA : questions d'experts sur des rapports 10-K, avec les passages justificatifs annotés. Jeu de test jamais vu à l'entraînement.", "FinQA: expert questions on 10-K reports with annotated supporting facts. Test set never seen in training.")}>
      {ev.error ? <ErrorState error={ev.error} /> : !m ? <LoadingBlock /> : (
        <div className="flex flex-col gap-4">
          <table className="w-full text-sm">
            <thead><tr className="border-b border-line text-left text-[12.5px] text-ink-3"><th className="py-1.5 pr-3 font-medium">{L("Méthode (test)", "Method (test)")}</th><th className="px-2 font-medium">Recall@3</th><th className="px-2 font-medium">Recall@5</th><th className="px-2 font-medium">{L("Tout@3", "All@3")}</th><th className="px-2 font-medium">MRR</th></tr></thead>
            <tbody>{Object.entries(m.fact_retrieval.test).map(([k, v]) => (
              <tr key={k} className="border-b border-line"><td className="py-1.5 pr-3">{names[k] ?? k}</td>{[v["recall@3"], v["recall@5"], v["all_gold@3"], v.mrr].map((x, i) => <td key={i} className="px-2 num">{x.toFixed(3)}</td>)}</tr>))}</tbody>
          </table>
          <KeyValue rows={[
            [L("Page retrouvée parmi 2 789 (top 5, sans filtre)", "Page found among 2,789 (top 5, no filter)"), fmtPct((m.page_retrieval_test.hybrid_rrf as Record<string, number>)["hit@5"], locale)],
            [L("Idem avec filtre société", "Same with a company filter"), fmtPct((m.page_retrieval_test.hybrid_rrf as Record<string, number>)["company_filter_hit@5"], locale)],
            [L("Calculatrice sur programmes d'experts (test)", "Calculator on expert programs (test)"), fmtPct(m.calculator_gold_programs.test.accuracy, locale)],
          ]} />
          <Notice title={L("Limites", "Limitations")}><ul className="list-disc pl-4">{ev.data?.model_card.limitations.map((x) => <li key={x}>{x}</li>)}</ul></Notice>
        </div>
      )}
    </Dialog>
  );
}

function KbDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (v: boolean) => void }) {
  const L = useL();
  const { locale } = useI18n();
  const can = useCan();
  const qc = useQueryClient();
  const toast = useToast();
  const kb = useQuery({ queryKey: ["copilot", "kb"], queryFn: () => get<{ id: string; filename: string; chunks: number; created_at: string; is_demo: boolean }[]>("/copilot/kb"), enabled: open });
  const input = useRef<HTMLInputElement>(null);
  const up = useMutation({
    mutationFn: (f: File) => { const fd = new FormData(); fd.append("file", f); return api("/copilot/kb", { method: "POST", body: fd }); },
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["copilot"] }); toast(L("Document indexé", "Document indexed")); },
    onError: (e) => toast(e instanceof ApiError ? e.message : String(e), "error"),
  });
  const rm = useMutation({ mutationFn: (id: string) => del(`/copilot/kb/${id}`), onSuccess: () => qc.invalidateQueries({ queryKey: ["copilot"] }) });
  return (
    <Dialog wide open={open} onOpenChange={onOpenChange} title={L("Base documentaire interne", "Internal knowledge base")}
      description={L("Procédures et politiques de votre banque, consultables uniquement par votre organisation.", "Your bank's procedures and policies, searchable only by your organisation.")}
      footer={can("data.manage") ? <><input ref={input} type="file" accept=".pdf,.txt,.md" className="sr-only" aria-label={L("Fichier", "File")} onChange={(e) => { const f = e.target.files?.[0]; if (f) up.mutate(f); e.target.value = ""; }} />
        <Button variant="primary" icon={<FileUp className="size-4" />} loading={up.isPending} onClick={() => input.current?.click()}>{L("Ajouter PDF, TXT ou Markdown", "Add PDF, TXT or Markdown")}</Button></> : undefined}>
      {!kb.data ? <LoadingBlock /> : kb.data.length === 0 ? <p className="text-sm text-ink-3">{L("Aucun document.", "No documents.")}</p> : (
        <ul className="divide-y divide-line">{kb.data.map((d) => (
          <li key={d.id} className="flex items-center gap-3 py-2">
            <BookOpen className="size-4 text-ink-3" aria-hidden />
            <div className="min-w-0 flex-1"><p className="truncate text-sm">{d.filename}</p><p className="text-xs text-ink-3">{d.chunks} {L("passages", "passages")} · {fmtDate(d.created_at, locale)}{d.is_demo ? " · DEMO" : ""}</p></div>
            {can("data.manage") && <Button size="sm" variant="ghost" aria-label={L("Supprimer", "Delete")} onClick={() => rm.mutate(d.id)}><Trash2 className="size-4" /></Button>}
          </li>))}</ul>
      )}
    </Dialog>
  );
}

function Message({ m, onCite }: { m: Msg; onCite: (c: Citation) => void }) {
  const L = useL();
  if (m.role === "user") return <div className="ml-auto max-w-[80%] rounded-[var(--radius-panel)] bg-accent-soft px-3.5 py-2.5 text-sm">{m.content}</div>;
  const blocked = m.status === "blocked" || m.status === "refused";
  const parts = m.mode === "extractive" && m.citations.length ? null : m.content;
  return (
    <div className="max-w-[92%]">
      <div className="mb-1.5 flex items-center gap-2 text-xs text-ink-3">
        {blocked ? <Badge tone="high"><ShieldAlert className="size-3" />{m.status === "blocked" ? L("Bloqué", "Blocked") : L("Refusé", "Refused")}</Badge>
          : m.status === "insufficient_evidence" ? <Badge tone="medium">{L("Preuves insuffisantes", "Insufficient evidence")}</Badge>
            : <Badge tone={m.mode === "llm" ? "accent" : "neutral"}>{m.mode === "llm" ? L("Synthèse LLM sourcée", "Sourced LLM synthesis") : L("Extractif", "Extractive")}</Badge>}
        {m.latency_ms != null && <span className="num">{m.latency_ms} ms</span>}
      </div>
      {parts ? <p className="whitespace-pre-wrap text-sm leading-relaxed">{parts}</p> : (
        <div className="text-sm leading-relaxed">
          <p className="text-ink-2 mb-2">{L("Passages les plus pertinents (aucune synthèse générative configurée) :", "Most relevant passages (no generative synthesis configured):")}</p>
          <ol className="flex flex-col gap-2">
            {m.citations.map((c) => (
              <li key={c.n} className="rounded-[var(--radius-control)] border border-line bg-surface px-3 py-2">
                <button type="button" onClick={() => onCite(c)} className="mb-1 flex flex-wrap items-center gap-1.5 text-left text-xs text-ink-3 hover:text-accent">
                  <span className="num rounded bg-surface-2 px-1.5 font-semibold text-ink">[{c.n}]</span>
                  <span>{c.scope === "public" ? `${c.company} · ${c.fiscal_year} · p. ${c.page}` : c.title}</span>
                  <span>· {c.kind === "text" ? L("texte", "text") : L("ligne de tableau", "table row")}</span>
                </button>
                <p className="text-ink">{c.content}</p>
              </li>
            ))}
          </ol>
        </div>
      )}
      {parts && m.citations.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-1.5">{m.citations.map((c) => (
          <button key={c.n} type="button" onClick={() => onCite(c)} className="rounded-full border border-line px-2 py-0.5 text-xs text-ink-2 hover:border-accent hover:text-accent">
            [{c.n}] {c.scope === "public" ? `${c.company} ${c.fiscal_year} p.${c.page}` : c.title}</button>))}</div>
      )}
    </div>
  );
}

export default function CopilotPage() {
  const { t, locale } = useI18n();
  const L = useL();
  const { data: me } = useMe();
  const qc = useQueryClient();
  const toast = useToast();
  const [conv, setConv] = useState<string | null>(null);
  const [q, setQ] = useState("");
  const [scope, setScope] = useState<"all" | "public" | "private">("all");
  const [company, setCompany] = useState("");
  const [year, setYear] = useState("");
  const [cite, setCite] = useState<Citation | null>(null);
  const [expr, setExpr] = useState("");
  const [evalOpen, setEvalOpen] = useState(false);
  const [kbOpen, setKbOpen] = useState(false);
  const [side, setSide] = useState("history");
  const bottom = useRef<HTMLDivElement>(null);
  const status = useQuery({ queryKey: ["copilot", "status"], queryFn: () => get<Status>("/copilot/status") });
  const convs = useQuery({ queryKey: ["copilot", "conversations"], queryFn: () => get<{ id: string; title: string; updated_at: string | null; created_at: string }[]>("/copilot/conversations") });
  const companies = useQuery({ queryKey: ["copilot", "companies"], queryFn: () => get<{ company: string; pages: number; first_year: number; last_year: number }[]>("/copilot/companies") });
  const thread = useQuery({ queryKey: ["copilot", "conv", conv], queryFn: () => get<{ messages: Msg[] }>(`/copilot/conversations/${conv}`), enabled: !!conv });
  const ask = useMutation({
    mutationFn: (question: string) => post<{ conversation_id: string; message: Msg }>("/copilot/ask", {
      question, conversation_id: conv, scope, company: company || null, year: year ? Number(year) : null }),
    onSuccess: (r) => { setConv(r.conversation_id); setQ(""); qc.invalidateQueries({ queryKey: ["copilot", "conv", r.conversation_id] }); qc.invalidateQueries({ queryKey: ["copilot", "conversations"] }); },
    onError: (e) => toast(e instanceof ApiError ? e.message : String(e), "error"),
  });
  const remove = useMutation({ mutationFn: (id: string) => del(`/copilot/conversations/${id}`), onSuccess: () => { setConv(null); qc.invalidateQueries({ queryKey: ["copilot", "conversations"] }); } });
  useEffect(() => { bottom.current?.scrollIntoView({ block: "end" }); }, [thread.data, ask.isPending]);
  const s = status.data;
  const pending = ask.isPending ? ask.variables : null;
  const examples = [
    L("Quel est le volume de paiements d'American Express ?", "What is American Express's payments volume?"),
    L("Quel délai pour traiter une alerte de fraude critique ?", "What is the deadline for a critical fraud alert?"),
    "what was the change in net revenue from 2007 to 2008?",
  ];
  return (
    <div className="flex flex-col gap-6">
      <PageHeader title={t("nav.copilot")} subtitle={L("Questions sur les rapports annuels publics et les procédures de votre banque, avec citation des passages.", "Questions on public annual reports and your bank's procedures, with cited passages.")}
        meta={me?.org.kind === "demo" ? <DemoTag /> : undefined}
        actions={<div className="flex gap-2"><Button onClick={() => setKbOpen(true)} icon={<BookOpen className="size-4" />}>{L("Base interne", "Internal KB")}</Button><Button variant="ghost" onClick={() => setEvalOpen(true)}>{L("Évaluation", "Evaluation")}</Button></div>} />
      {status.error ? <ErrorState error={status.error} /> : s && (
        <>
          <Notice>{s.disclaimer}</Notice>
          {!s.retriever.deployed && <Notice tone="warn" title={L("Copilote indisponible", "Copilot unavailable")}>{L("Aucune version du moteur de recherche n'est approuvée et déployée.", "No retriever version is approved and deployed.")}</Notice>}
          {s.retriever.deployed && s.public_corpus.embedded < s.public_corpus.chunks && <Notice tone="warn">{L(`Index vectoriel incomplet : ${s.public_corpus.embedded} / ${s.public_corpus.chunks} passages.`, `Vector index incomplete: ${s.public_corpus.embedded} / ${s.public_corpus.chunks} passages.`)}</Notice>}
        </>
      )}
      <div className="grid gap-6 xl:grid-cols-[260px_minmax(0,1fr)_340px]">
        <Panel flush title={L("Conversations", "Conversations")} actions={<Button size="sm" variant="ghost" icon={<MessageSquarePlus className="size-4" />} onClick={() => { setConv(null); setCite(null); }}>{L("Nouvelle", "New")}</Button>}>
          {!convs.data ? <LoadingBlock /> : convs.data.length === 0 ? <p className="px-4 py-4 text-sm text-ink-3">{L("Aucune conversation.", "No conversations.")}</p> : (
            <ul className="max-h-[60vh] overflow-y-auto scroll-thin">{convs.data.map((c) => (
              <li key={c.id} className={cn("group flex items-center border-b border-line last:border-0", conv === c.id && "bg-accent-soft")}>
                <button type="button" className="min-w-0 flex-1 px-4 py-2.5 text-left" onClick={() => { setConv(c.id); setCite(null); }}>
                  <p className="truncate text-sm">{c.title}</p><p className="text-xs text-ink-3">{fmtDate(c.updated_at ?? c.created_at, locale, true)}</p>
                </button>
                <button type="button" aria-label={L("Supprimer", "Delete")} className="mr-2 rounded p-1 text-ink-3 opacity-0 group-hover:opacity-100 focus:opacity-100 hover:text-risk-high" onClick={() => remove.mutate(c.id)}><Trash2 className="size-4" /></button>
              </li>))}</ul>
          )}
        </Panel>
        <section className="flex min-h-[560px] flex-col rounded-[var(--radius-panel)] border border-line bg-surface" aria-label={L("Conversation", "Conversation")}>
          <div className="flex-1 overflow-y-auto scroll-thin px-4 py-4 flex flex-col gap-5" aria-live="polite">
            {!conv && !pending && (
              <div className="m-auto max-w-md text-center">
                <p className="font-serif text-xl">{L("Posez une question sourcée", "Ask a sourced question")}</p>
                <p className="mt-2 text-sm text-ink-3">{s ? L(`${fmtNumber(s.public_corpus.pages, locale)} pages de rapports 10-K (FinQA) et ${s.private_kb.documents} document(s) interne(s).`, `${fmtNumber(s.public_corpus.pages, locale)} 10-K report pages (FinQA) and ${s.private_kb.documents} internal document(s).`) : ""}</p>
                <div className="mt-4 flex flex-col gap-2">{examples.map((e) => <button key={e} type="button" onClick={() => setQ(e)} className="rounded-[var(--radius-control)] border border-line px-3 py-2 text-left text-sm text-ink-2 hover:border-accent">{e}</button>)}</div>
              </div>
            )}
            {thread.data?.messages.map((m) => <Message key={m.id} m={m} onCite={(c) => { setCite(c); setSide("source"); }} />)}
            {pending && <><div className="ml-auto max-w-[80%] rounded-[var(--radius-panel)] bg-accent-soft px-3.5 py-2.5 text-sm">{pending}</div><LoadingBlock label={L("Recherche des passages…", "Retrieving passages…")} /></>}
            <div ref={bottom} />
          </div>
          <form className="border-t border-line p-3 flex flex-col gap-2" onSubmit={(e) => { e.preventDefault(); if (q.trim().length >= 3) ask.mutate(q.trim()); }}>
            <Textarea rows={2} value={q} onChange={(e) => setQ(e.target.value)} placeholder={L("Votre question…", "Your question…")} aria-label={L("Question", "Question")}
              onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); if (q.trim().length >= 3) ask.mutate(q.trim()); } }} />
            <div className="flex flex-wrap items-center gap-2">
              <Select className="h-8 w-44" value={scope} onChange={(e) => setScope(e.target.value as typeof scope)} aria-label={L("Sources", "Sources")}>
                <option value="all">{L("Toutes les sources", "All sources")}</option><option value="public">{L("Rapports publics", "Public reports")}</option><option value="private">{L("Procédures internes", "Internal procedures")}</option>
              </Select>
              <Select className="h-8 w-36" value={company} onChange={(e) => setCompany(e.target.value)} aria-label={L("Société", "Company")} disabled={scope === "private"}>
                <option value="">{L("Toute société", "Any company")}</option>{companies.data?.map((c) => <option key={c.company} value={c.company}>{c.company} ({c.pages})</option>)}
              </Select>
              <Input className="h-8 w-24" type="number" min={1990} max={2030} placeholder={L("Année", "Year")} value={year} onChange={(e) => setYear(e.target.value)} aria-label={L("Exercice", "Fiscal year")} disabled={scope === "private"} />
              <Button className="ml-auto" variant="primary" type="submit" icon={<SendHorizontal className="size-4" />} loading={ask.isPending} disabled={q.trim().length < 3}>{L("Envoyer", "Send")}</Button>
            </div>
          </form>
        </section>
        <div className="flex flex-col gap-6 min-w-0">
          <Tabs value={side} onValueChange={setSide} items={[{ value: "history", label: L("Outils", "Tools") }, { value: "source", label: L("Source", "Source") }]}>
            <TabPanel value="history" className="pt-4 flex flex-col gap-6">
              <CalculatorPanel expr={expr} setExpr={setExpr} />
              {s && <Panel title={L("Moteur", "Engine")}>
                <KeyValue rows={[
                  [L("Recherche", "Retrieval"), s.retriever.deployed ? `v${s.retriever.version} · recall@3 ${s.retriever.test_recall_at_3?.toFixed(3)}` : L("non déployée", "not deployed")],
                  [L("Synthèse", "Synthesis"), s.llm.available ? s.llm.provider : L("extractive (aucun LLM configuré)", "extractive (no LLM configured)")],
                  [L("Corpus public", "Public corpus"), `${fmtNumber(s.public_corpus.chunks, locale)} ${L("passages", "passages")}`],
                  [L("Base interne", "Internal KB"), `${s.private_kb.documents} doc. · ${s.private_kb.chunks} ${L("passages", "passages")}`],
                ]} />
              </Panel>}
            </TabPanel>
            <TabPanel value="source" className="pt-4">
              {cite ? <SourcePanel cite={cite} onClose={() => { setCite(null); setSide("history"); }} onPickNumber={(n) => setExpr((e) => `${e}${e && !/[+\-*/( ]$/.test(e) ? " " : ""}${n.replace(/%$/, " %")}`)} />
                : <p className="text-sm text-ink-3">{L("Cliquez sur une citation pour afficher le passage dans son contexte.", "Click a citation to show the passage in context.")}</p>}
            </TabPanel>
          </Tabs>
        </div>
      </div>
      <EvaluationDialog open={evalOpen} onOpenChange={setEvalOpen} />
      <KbDialog open={kbOpen} onOpenChange={setKbOpen} />
    </div>
  );
}
