/** Model registry helpers. */

export const MODULE_LABELS: Record<string, [string, string]> = {
  fraud: ["Fraude", "Fraud"], aml: ["LCB-FT", "AML"], credit: ["Crédit", "Credit"], customer: ["Client", "Customer"], forecasting: ["Prévision", "Forecasting"],
  documents: ["Documents", "Documents"], copilot: ["Copilote", "Copilot"], agents: ["Agents", "Agents"],
};

export const VERSION_STATUS: Record<string, [string, string]> = {
  candidate: ["Candidate (en revue)", "Candidate (in review)"], approved: ["Approuvée", "Approved"], rejected: ["Rejetée", "Rejected"],
  production: ["En production", "In production"], archived: ["Archivée", "Archived"],
};

export function driftTone(s: string | null | undefined): "ok" | "medium" | "high" | "neutral" {
  return s === "stable" ? "ok" : s === "moderate" ? "medium" : s === "significant" ? "high" : "neutral";
}

export function versionTone(s: string): "ok" | "medium" | "high" | "neutral" | "accent" {
  return s === "production" ? "ok" : s === "approved" ? "accent" : s === "candidate" ? "medium" : s === "rejected" ? "high" : "neutral";
}
