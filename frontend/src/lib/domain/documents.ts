/** Document AI domain helpers: labels for fields, controls and statuses (FR / EN). */

type Pair = [string, string];

export const FIELD_LABELS: Record<string, Pair> = {
  holder: ["Titulaire", "Account holder"],
  iban: ["IBAN", "IBAN"],
  period_start: ["Début de période", "Period start"],
  period_end: ["Fin de période", "Period end"],
  opening_balance: ["Solde initial", "Opening balance"],
  closing_balance: ["Solde final", "Closing balance"],
  total_debits: ["Total des débits", "Total debits"],
  total_credits: ["Total des crédits", "Total credits"],
  invoice_number: ["N° de facture", "Invoice number"],
  invoice_date: ["Date de facture", "Invoice date"],
  siren: ["SIREN", "SIREN"],
  total_ht: ["Total HT", "Net total"],
  vat: ["TVA", "VAT"],
  vat_rate: ["Taux de TVA (%)", "VAT rate (%)"],
  total_ttc: ["Total TTC", "Gross total"],
  employer: ["Employeur", "Employer"],
  employee: ["Salarié", "Employee"],
  period: ["Période", "Period"],
  gross_salary: ["Salaire brut", "Gross salary"],
  net_salary: ["Net avant impôt", "Net before tax"],
  net_paid: ["Net payé", "Net paid"],
  company_name: ["Dénomination", "Company name"],
  legal_form: ["Forme juridique", "Legal form"],
  registration_date: ["Date d'immatriculation", "Registration date"],
  capital: ["Capital social", "Share capital"],
  address: ["Adresse", "Address"],
  customer: ["Titulaire du contrat", "Contract holder"],
  bill_date: ["Date de facture", "Bill date"],
  amount: ["Montant TTC", "Amount due"],
};

export const CHECK_LABELS: Record<string, Pair> = {
  required_fields: ["Champs obligatoires", "Required fields"],
  iban_checksum: ["Clé de contrôle IBAN", "IBAN checksum"],
  siren_luhn: ["Validité du SIREN", "SIREN validity"],
  balance_reconciliation: ["Rapprochement des soldes", "Balance reconciliation"],
  period_order: ["Cohérence de la période", "Period consistency"],
  invoice_totals: ["HT + TVA = TTC", "Net + VAT = gross"],
  vat_rate_plausible: ["Taux de TVA plausible", "Plausible VAT rate"],
  net_not_above_gross: ["Net ≤ brut", "Net ≤ gross"],
  document_freshness: ["Fraîcheur du justificatif", "Document freshness"],
  low_confidence_fields: ["Champs à faible confiance", "Low-confidence fields"],
  classification: ["Type de document", "Document type"],
};

export const STATUS_LABELS: Record<string, Pair> = {
  processing: ["En traitement", "Processing"],
  needs_review: ["À revoir", "Needs review"],
  validated: ["Validé", "Validated"],
  rejected: ["Rejeté", "Rejected"],
  failed: ["Échec", "Failed"],
};

export const DOC_TYPE_LABELS: Record<string, Pair> = {
  bank_statement: ["Relevé de compte", "Bank statement"],
  invoice: ["Facture", "Invoice"],
  payslip: ["Bulletin de paie", "Payslip"],
  company_extract: ["Extrait d'immatriculation", "Company registration extract"],
  utility_bill: ["Facture d'énergie", "Utility bill"],
};

export function statusTone(s: string): "neutral" | "accent" | "ok" | "medium" | "high" {
  return s === "validated" ? "ok" : s === "needs_review" ? "medium" : s === "rejected" || s === "failed" ? "high" : "neutral";
}

export function checkTone(s: string): "ok" | "medium" | "high" | "neutral" {
  return s === "pass" ? "ok" : s === "warn" ? "medium" : s === "fail" ? "high" : "neutral";
}

export function label(map: Record<string, Pair>, key: string | null | undefined, locale: string, fallback = "—"): string {
  if (!key) return fallback;
  const p = map[key];
  return p ? (locale === "en" ? p[1] : p[0]) : key;
}
