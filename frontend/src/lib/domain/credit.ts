export const gradeTone = (g: string | null) => (g === "E" ? "critical" : g === "D" ? "high" : g === "C" ? "medium" : "ok") as "critical" | "high" | "medium" | "ok";
