export const TYPOLOGY: Record<string, { fr: string; en: string }> = {
  fan_in: { fr: "Concentration (fan-in)", en: "Fan-in" }, fan_out: { fr: "Dispersion (fan-out)", en: "Fan-out" },
  cycle: { fr: "Circularité", en: "Cycle" }, structuring: { fr: "Fractionnement", en: "Structuring" },
  pass_through: { fr: "Transit rapide", en: "Pass-through" }, cross_border: { fr: "Hors EEE", en: "Outside EEA" },
  ml_anomaly: { fr: "Anomalie de graphe (modèle)", en: "Graph anomaly (model)" },
};
