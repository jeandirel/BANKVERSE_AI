/** Data platform helpers. */

export function qualityTone(s: string): "ok" | "medium" | "high" | "neutral" {
  return s === "pass" ? "ok" : s === "warn" ? "medium" : s === "fail" ? "high" : "neutral";
}
