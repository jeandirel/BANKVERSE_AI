/** Agent run statuses (FR / EN) and badge tones. */

export const AGENT_STATUS: Record<string, { fr: string; en: string }> = {
  running: { fr: "En cours", en: "Running" },
  awaiting_approval: { fr: "En attente d'approbation", en: "Awaiting approval" },
  completed: { fr: "Terminé", en: "Completed" },
  failed: { fr: "Échec", en: "Failed" },
  blocked: { fr: "Bloqué (sécurité)", en: "Blocked (safety)" },
  refused: { fr: "Refusé", en: "Refused" },
  needs_input: { fr: "Précision requise", en: "Needs input" },
};

export function agentStatusTone(s: string): "neutral" | "accent" | "ok" | "medium" | "high" {
  return s === "completed" ? "ok" : s === "awaiting_approval" ? "medium" : s === "failed" || s === "blocked" || s === "refused" ? "high" : "neutral";
}
