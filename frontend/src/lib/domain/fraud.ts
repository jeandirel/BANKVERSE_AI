import type { Band } from "@/components/ui/risk";
import type { MessageKey } from "@/lib/messages";

export type FraudAlertRow = {
  id: string; status: string; risk_band: Band; priority: number; score: number; expected_loss: number;
  created_at: string; sla_due_at: string | null; assigned_to: string | null; analyst_label: string | null; case_id: string | null;
  top_reason: string | null;
  transaction: { id: string; ts: string; amount: number; currency: string; merchant: string; merchant_city: string; mcc: number; channel: string; errors: string | null };
  customer: { id: string; name: string; ref: string; segment: string; is_demo: boolean };
};


export function statusLabel(t: (k: MessageKey) => string, s: string): string {
  return t(`fraud.status.${s}` as MessageKey);
}

