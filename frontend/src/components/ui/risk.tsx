"use client";

import { cn } from "@/lib/cn";
import { useI18n } from "@/lib/i18n";
import { Badge } from "./primitives";

export type Band = "critical" | "high" | "medium" | "low" | null | undefined;

const bandColor: Record<string, string> = {
  critical: "var(--risk-critical)",
  high: "var(--risk-high)",
  medium: "var(--risk-medium)",
  low: "var(--risk-low)",
};

/**
 * Risk gauge — the platform's signature mark: a slim vertical bar whose fill height is the score and
 * whose colour is the risk band. Always paired with a text label (never colour alone).
 */
export function RiskGauge({ score, band, size = "md" }: { score: number | null | undefined; band: Band; size?: "sm" | "md" }) {
  const h = size === "sm" ? 22 : 30;
  const pct = Math.max(0.04, Math.min(1, score ?? 0));
  return (
    <span className="inline-flex items-end rounded-[2px] bg-surface-3 overflow-hidden" style={{ width: 5, height: h }}
      role="img" aria-label={score == null ? "no score" : `score ${score.toFixed(3)}`}>
      <span style={{ height: `${pct * 100}%`, background: band ? bandColor[band] : "var(--ink-3)" }} className="w-full" />
    </span>
  );
}

export function RiskBadge({ band }: { band: Band }) {
  const { t } = useI18n();
  if (!band) return <Badge>{t("risk.low")}</Badge>;
  return <Badge tone={band === "low" ? "ok" : band}>{t(`risk.${band}` as "risk.high")}</Badge>;
}

export function ScoreCell({ score, band }: { score: number | null | undefined; band: Band }) {
  return (
    <span className="inline-flex items-center gap-2">
      <RiskGauge score={score} band={band} size="sm" />
      <span className={cn("num text-sm font-medium", band === "critical" ? "text-risk-critical" : band === "high" ? "text-risk-high" : "text-ink")}>
        {score == null ? "—" : score.toFixed(3)}
      </span>
    </span>
  );
}

export function DemoTag() {
  const { t } = useI18n();
  return <Badge tone="demo">{t("common.demo")}</Badge>;
}
