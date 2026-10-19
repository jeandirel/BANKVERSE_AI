"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import { Play } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { BarsChart, TimeSeriesChart } from "@/components/ui/charts";
import { ErrorState, KeyValue, LoadingBlock, Notice, PageHeader, Panel } from "@/components/ui/layout";
import { Tabs, TabPanel } from "@/components/ui/overlay";
import { Badge, Button, Field, Input, Select } from "@/components/ui/primitives";
import { get, post } from "@/lib/api";
import { cn } from "@/lib/cn";
import { fmtDate, fmtNumber } from "@/lib/format";
import { useI18n, useL } from "@/lib/i18n";
import { useCan } from "@/lib/session";

type SeriesMeta = { key: string; name: string; unit: string; upstream: string; last_date: string; last_value: number; change_12m: number | null; n_obs: number };
type Detail = { key: string; name: string; unit: string; upstream: string; description: string; disclaimer: string; observations: { date: string; value: number }[];
  diagnostics: { transform: string; level: Record<string, number | boolean>; first_difference: Record<string, number | boolean>;
    stl: { seasonal_strength: number; trend: number[] }; anomalies: { date: string; value: number; robust_z: number }[] } };
type Backtest = { study_version: number; horizon: number; origins: string[]; best_by_mase: string; seasonal_strength: number;
  models: Record<string, { mase: number; mae: number; rmse: number; smape: number; coverage80: number | null; coverage95: number | null; mae_by_horizon: number[]; status?: string }> };
type Point = { date: string; mean: number; lo80: number | null; hi80: number | null; lo95: number | null; hi95: number | null };
type RunResp = { series: string; forecasts: Record<string, { status: string; info: Record<string, unknown>; points: Point[] }>; history: { date: string; value: number }[];
  scenario: null | { driver: string; shock: number; baseline: number[]; shocked: number[]; shocked_lo80: number[]; shocked_hi80: number[]; dates: string[]; note: string; model: Record<string, unknown> };
  disclaimer: string; duration_ms: number };

const MODEL_LABEL: Record<string, string> = { naive: "Naïf", seasonal_naive: "Naïf saisonnier", moving_average: "Moyenne mobile", ets: "Lissage exponentiel (ETS)",
  arima: "ARIMA", sarima: "SARIMA", xgboost_lags: "XGBoost (retards)", lstm: "LSTM" };

export default function ForecastingStudio() {
  const { t, locale } = useI18n();
  const L = useL();
  const can = useCan();
  const series = useQuery({ queryKey: ["fc", "series"], queryFn: () => get<SeriesMeta[]>("/forecasting/series") });
  const [key, setKey] = useState("euribor_3m");
  const [tab, setTab] = useState("forecast");
  const [models, setModels] = useState<string[]>(["naive", "ets", "arima"]);
  const [horizon, setHorizon] = useState(12);
  const [driver, setDriver] = useState("");
  const [shock, setShock] = useState("1");
  const [band, setBand] = useState("arima");
  const detail = useQuery({ queryKey: ["fc", "detail", key], queryFn: () => get<Detail>(`/forecasting/series/${key}`) });
  const bt = useQuery({ queryKey: ["fc", "bt", key], queryFn: () => get<Backtest>(`/forecasting/backtest/${key}`), retry: false });
  const run = useMutation({ mutationFn: () => post<RunResp>("/forecasting/run", {
    series: key, horizon, models, scenario: driver ? { driver, shock: Number(shock) } : null, save: true }) });
  useEffect(() => { run.reset(); }, [key]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { if (bt.data?.best_by_mase && !models.includes(bt.data.best_by_mase)) setModels((m) => [...m, bt.data!.best_by_mase]); }, [bt.data]); // eslint-disable-line react-hooks/exhaustive-deps
  const meta = series.data?.find((s) => s.key === key);
  const fmt = (v: number) => fmtNumber(v, locale, Math.abs(v) < 10 ? 3 : 1);

  const chart = useMemo(() => {
    const r = run.data;
    if (!r) return null;
    const rows: Record<string, Record<string, unknown>> = {};
    for (const h of r.history) rows[h.date] = { d: h.date.slice(0, 7), actual: h.value };
    for (const [m, f] of Object.entries(r.forecasts)) {
      if (f.status !== "ok") continue;
      for (const p of f.points) {
        rows[p.date] = { ...(rows[p.date] ?? { d: p.date.slice(0, 7) }), [m]: p.mean };
        if (m === band && p.lo80 != null && p.hi80 != null) rows[p.date].band = [p.lo80, p.hi80];
      }
    }
    if (r.scenario) r.scenario.dates.forEach((d, i) => { rows[d] = { ...(rows[d] ?? { d: d.slice(0, 7) }), scenario: r.scenario!.shocked[i] }; });
    return Object.keys(rows).sort().map((k) => rows[k]);
  }, [run.data, band]);

  return (
    <div className="flex flex-col gap-6">
      <PageHeader title={t("nav.forecasting")} subtitle={L("Séries macro-financières publiques, prévisions hors échantillon avec intervalles, backtests et scénarios.", "Public macro-financial series, out-of-sample forecasts with intervals, backtests and scenarios.")} />
      {series.error ? <ErrorState error={series.error} /> : !series.data ? <LoadingBlock /> : (
        <div className="flex gap-2 overflow-x-auto scroll-thin pb-1" role="tablist" aria-label={L("Séries", "Series")}>
          {series.data.map((s) => (
            <button key={s.key} role="tab" aria-selected={s.key === key} onClick={() => setKey(s.key)}
              className={cn("min-w-44 shrink-0 rounded-[var(--radius-panel)] border px-3 py-2.5 text-left", s.key === key ? "border-accent bg-accent-soft" : "border-line bg-surface hover:bg-surface-2")}>
              <p className="text-[12.5px] text-ink-2 truncate">{s.name}</p>
              <p className="num text-lg font-semibold">{fmt(s.last_value)} <span className="text-xs font-normal text-ink-3">{s.unit}</span></p>
              <p className="text-xs text-ink-3">{fmtDate(s.last_date, locale)}{s.change_12m != null ? ` · 12 m ${s.change_12m > 0 ? "+" : ""}${fmt(s.change_12m)}` : ""}</p>
            </button>
          ))}
        </div>
      )}
      <Notice>{detail.data?.disclaimer ?? L("Les prévisions ne sont jamais des certitudes financières.", "Forecasts are never financial certainties.")}</Notice>
      <Tabs value={tab} onValueChange={setTab} items={[{ value: "forecast", label: L("Prévision", "Forecast") }, { value: "backtest", label: L("Backtest", "Backtest") }, { value: "diagnostics", label: L("Diagnostics", "Diagnostics") }]}>
        <TabPanel value="forecast" className="pt-4">
          <div className="grid gap-6 xl:grid-cols-[320px_1fr]">
            <Panel title={L("Paramètres", "Settings")}>
              <div className="flex flex-col gap-4">
                <Field label={L("Modèles", "Models")}>
                  <div className="flex flex-col gap-1.5">{Object.entries(MODEL_LABEL).map(([m, lab]) => (
                    <label key={m} className="flex items-center gap-2 text-sm"><input type="checkbox" checked={models.includes(m)} onChange={(e) => setModels((xs) => e.target.checked ? [...xs, m] : xs.filter((x) => x !== m))} />
                      {lab}{bt.data?.best_by_mase === m && <Badge tone="accent">{L("meilleur backtest", "best backtest")}</Badge>}</label>))}</div>
                </Field>
                <Field label={L("Horizon (mois)", "Horizon (months)")} htmlFor="h"><Input id="h" type="number" min={1} max={24} value={horizon} onChange={(e) => setHorizon(Number(e.target.value))} /></Field>
                <Field label={L("Scénario : facteur explicatif (SARIMAX)", "Scenario: explanatory driver (SARIMAX)")} htmlFor="drv">
                  <Select id="drv" value={driver} onChange={(e) => setDriver(e.target.value)}>
                    <option value="">{L("Aucun", "None")}</option>{series.data?.filter((s) => s.key !== key).map((s) => <option key={s.key} value={s.key}>{s.name}</option>)}
                  </Select>
                </Field>
                {driver && <Field label={L("Choc cumulé sur l'horizon (unités du facteur)", "Cumulative shock over the horizon (driver units)")} htmlFor="sh"><Input id="sh" type="number" step="0.1" value={shock} onChange={(e) => setShock(e.target.value)} /></Field>}
                <Button variant="primary" icon={<Play className="size-4" />} disabled={!can("forecast.run") || models.length === 0} loading={run.isPending} onClick={() => run.mutate()}>{L("Calculer", "Run")}</Button>
                {!can("forecast.run") && <p className="text-xs text-ink-3">{L("Votre rôle permet la consultation uniquement.", "Your role is read-only.")}</p>}
              </div>
            </Panel>
            <Panel title={meta?.name ?? key} description={meta ? `${meta.upstream} · ${meta.unit}` : undefined}
              actions={run.data ? <Select className="h-8 w-48" value={band} onChange={(e) => setBand(e.target.value)} aria-label={L("Intervalle affiché", "Interval shown")}>
                {Object.keys(run.data.forecasts).map((m) => <option key={m} value={m}>{L("Intervalle 80 % :", "80% interval:")} {MODEL_LABEL[m]}</option>)}</Select> : undefined}>
              {run.error ? <ErrorState error={run.error} /> : !chart ? (
                detail.data ? <TimeSeriesChart height={360} data={detail.data.observations.map((o) => ({ d: o.date.slice(0, 7), v: o.value }))} xKey="d" series={[{ key: "v", label: detail.data.name, color: "var(--ink-2)" }]} fmt={fmt} /> : <LoadingBlock />
              ) : (
                <>
                  <TimeSeriesChart height={380} data={chart} xKey="d" fmt={fmt}
                    series={[{ key: "actual", label: L("Observé", "Observed"), color: "var(--ink-2)" },
                      ...Object.keys(run.data!.forecasts).filter((m) => run.data!.forecasts[m].status === "ok").slice(0, 4).map((m) => ({ key: m, label: MODEL_LABEL[m], dashed: true })),
                      ...(run.data!.scenario ? [{ key: "scenario", label: L("Scénario", "Scenario"), color: "var(--series-4)" }] : [])]}
                    band={{ key: "band", label: L("Intervalle 80 %", "80% interval") }} />
                  <div className="mt-4 overflow-x-auto"><table className="w-full text-sm">
                    <thead><tr className="border-b border-line text-left text-[12.5px] text-ink-3"><th className="px-2 py-1.5 font-medium">{L("Modèle", "Model")}</th><th className="px-2 py-1.5 font-medium">{L("Paramètres", "Parameters")}</th>
                      <th className="px-2 py-1.5 font-medium text-right">{L(`Prévision à ${horizon} mois`, `${horizon}-month forecast`)}</th><th className="px-2 py-1.5 font-medium text-right">80 %</th><th className="px-2 py-1.5 font-medium text-right">95 %</th></tr></thead>
                    <tbody>{Object.entries(run.data!.forecasts).map(([m, f]) => {
                      const last = f.points?.[f.points.length - 1];
                      return <tr key={m} className="border-b border-line"><td className="px-2 py-1.5">{MODEL_LABEL[m]}</td><td className="px-2 py-1.5 text-xs text-ink-3">{f.status === "ok" ? JSON.stringify(f.info) : f.status}</td>
                        <td className="px-2 py-1.5 text-right num">{last ? fmt(last.mean) : "—"}</td><td className="px-2 py-1.5 text-right num text-ink-2">{last?.lo80 != null ? `${fmt(last.lo80)} – ${fmt(last.hi80!)}` : "—"}</td>
                        <td className="px-2 py-1.5 text-right num text-ink-2">{last?.lo95 != null ? `${fmt(last.lo95)} – ${fmt(last.hi95!)}` : "—"}</td></tr>;
                    })}</tbody></table></div>
                  {run.data!.scenario && <Notice title={L("Scénario", "Scenario")}>{run.data!.scenario.note} {L("Coefficient estimé du facteur :", "Estimated driver coefficient:")} {String(run.data!.scenario.model.exog_coefficient)} · {L("écart final vs référence :", "final gap vs baseline:")} {fmt(run.data!.scenario.shocked[run.data!.scenario.shocked.length - 1] - run.data!.scenario.baseline[run.data!.scenario.baseline.length - 1])}</Notice>}
                </>
              )}
            </Panel>
          </div>
        </TabPanel>
        <TabPanel value="backtest" className="pt-4">
          {bt.error ? <ErrorState error={bt.error} /> : !bt.data ? <LoadingBlock /> : (
            <div className="grid gap-6 lg:grid-cols-2">
              <Panel title={L("Erreurs hors échantillon (origines glissantes)", "Out-of-sample errors (rolling origins)")} description={`${L("Horizon", "Horizon")} ${bt.data.horizon} · ${bt.data.origins.length} ${L("origines", "origins")} : ${bt.data.origins.join(", ")}`} flush className="lg:col-span-2">
                <div className="overflow-x-auto"><table className="w-full text-sm">
                  <thead><tr className="border-b border-line text-left text-[12.5px] text-ink-3">{[L("Modèle", "Model"), "MASE", "MAE", "RMSE", "sMAPE", L("Couverture 80 %", "Coverage 80%"), L("Couverture 95 %", "Coverage 95%")].map((h) => <th key={h} className="px-3 py-2 font-medium">{h}</th>)}</tr></thead>
                  <tbody>{Object.entries(bt.data.models).sort((a, b) => (a[1].mase ?? 99) - (b[1].mase ?? 99)).map(([m, v]) => (
                    <tr key={m} className="border-b border-line"><td className="px-3 py-1.5">{MODEL_LABEL[m]}{bt.data!.best_by_mase === m ? " ★" : ""}</td>
                      {v.status === "failed" ? <td colSpan={6} className="px-3 py-1.5 text-ink-3">{L("échec", "failed")}</td> : <>
                        <td className="px-3 py-1.5 num">{v.mase.toFixed(3)}</td><td className="px-3 py-1.5 num">{fmt(v.mae)}</td><td className="px-3 py-1.5 num">{fmt(v.rmse)}</td>
                        <td className="px-3 py-1.5 num">{v.smape.toFixed(3)}</td><td className="px-3 py-1.5 num">{v.coverage80?.toFixed(2) ?? "—"}</td><td className="px-3 py-1.5 num">{v.coverage95?.toFixed(2) ?? "—"}</td></>}</tr>))}</tbody>
                </table></div>
                <p className="px-4 py-3 text-xs text-ink-3">{L("MASE < 1 : meilleur que la marche aléatoire en moyenne. Une couverture inférieure au niveau nominal signale des intervalles trop étroits.", "MASE < 1: better than a random walk on average. Coverage below the nominal level signals intervals that are too narrow.")}</p>
              </Panel>
              <Panel title={L("Erreur absolue moyenne par horizon", "Mean absolute error by horizon")} className="lg:col-span-2">
                <TimeSeriesChart height={260} xKey="h" fmt={fmt} data={Array.from({ length: bt.data.horizon }, (_, i) => ({ h: `+${i + 1}`, ...Object.fromEntries(Object.entries(bt.data!.models).filter(([, v]) => v.mae_by_horizon).slice(0, 5).map(([m, v]) => [m, v.mae_by_horizon[i]])) }))}
                  series={Object.keys(bt.data.models).filter((m) => bt.data!.models[m].mae_by_horizon).slice(0, 5).map((m) => ({ key: m, label: MODEL_LABEL[m] }))} />
              </Panel>
            </div>
          )}
        </TabPanel>
        <TabPanel value="diagnostics" className="pt-4">
          {!detail.data ? <LoadingBlock /> : (
            <div className="grid gap-6 lg:grid-cols-2">
              <Panel title={L("Stationnarité (ADF / KPSS)", "Stationarity (ADF / KPSS)")} description={`${L("Transformation", "Transform")} : ${detail.data.diagnostics.transform}`}>
                <KeyValue rows={(["level", "first_difference"] as const).map((k) => [k === "level" ? L("Niveau", "Level") : L("Différence première", "First difference"),
                  `ADF p=${detail.data!.diagnostics[k].adf_pvalue} · KPSS p=${detail.data!.diagnostics[k].kpss_pvalue} · ${detail.data!.diagnostics[k].stationary ? L("stationnaire", "stationary") : L("non stationnaire", "non-stationary")}`])} />
                <p className="mt-3 text-sm">{L("Force saisonnière (STL)", "Seasonal strength (STL)")} : <span className="num">{detail.data.diagnostics.stl.seasonal_strength}</span></p>
              </Panel>
              <Panel title={L("Anomalies (résidus STL, |z| > 3,5)", "Anomalies (STL residuals, |z| > 3.5)")}>
                {detail.data.diagnostics.anomalies.length === 0 ? <p className="text-sm text-ink-3">{L("Aucune anomalie détectée.", "No anomaly detected.")}</p> : (
                  <BarsChart data={detail.data.diagnostics.anomalies.map((a) => ({ d: a.date.slice(0, 7), z: a.robust_z }))} xKey="d" series={[{ key: "z", label: "z", color: "var(--risk-high)" }]} height={220} />
                )}
              </Panel>
              <Panel title={L("Tendance (STL)", "Trend (STL)")} className="lg:col-span-2">
                <TimeSeriesChart height={260} xKey="d" fmt={fmt} data={detail.data.observations.map((o, i) => ({ d: o.date.slice(0, 7), obs: o.value, trend: detail.data!.diagnostics.stl.trend[i] }))}
                  series={[{ key: "obs", label: L("Observé", "Observed"), color: "var(--ink-3)" }, { key: "trend", label: L("Tendance", "Trend") }]} />
              </Panel>
            </div>
          )}
        </TabPanel>
      </Tabs>
    </div>
  );
}
