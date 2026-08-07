// API base URL resolution (browser / PWA / native wrapper):
// 1) localStorage "psychapp_api_base" (set in Ajustes — used by phone app)
// 2) VITE_API_BASE_URL at build time
// 3) "" = same-origin (Docker nginx proxies /api → backend)
const API_BASE_KEY = "psychapp_api_base";

export function getApiBase(): string {
  const stored = (localStorage.getItem(API_BASE_KEY) || "").trim().replace(/\/$/, "");
  if (stored) return stored;
  return (import.meta.env.VITE_API_BASE_URL ?? "").replace(/\/$/, "");
}

export function setApiBase(url: string | null) {
  const cleaned = (url || "").trim().replace(/\/$/, "");
  if (cleaned) localStorage.setItem(API_BASE_KEY, cleaned);
  else localStorage.removeItem(API_BASE_KEY);
}

export function getToken(): string | null {
  return localStorage.getItem("psychapp_token");
}

export function setToken(token: string | null) {
  if (token) localStorage.setItem("psychapp_token", token);
  else localStorage.removeItem("psychapp_token");
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const token = getToken();
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(options.headers as Record<string, string> | undefined),
  };
  if (token) headers["Authorization"] = `Bearer ${token}`;

  const base = getApiBase();
  const res = await fetch(`${base}${path}`, { ...options, headers });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body.detail || JSON.stringify(body);
    } catch {
      /* ignore */
    }
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export const api = {
  get: <T,>(path: string) => request<T>(path, { method: "GET" }),
  post: <T,>(path: string, body?: unknown) =>
    request<T>(path, { method: "POST", body: body !== undefined ? JSON.stringify(body) : undefined }),
  put: <T,>(path: string, body?: unknown) =>
    request<T>(path, { method: "PUT", body: body !== undefined ? JSON.stringify(body) : undefined }),
};

export type UserRole = "patient" | "therapist" | "supervisor" | "admin_clinical";

export function homePathForRole(role: UserRole | string): string {
  return role === "patient" ? "/" : "/professional";
}

// ---------------------------------------------------------------- types ---
export interface UserOut {
  id: string;
  email: string;
  display_name: string;
  role: UserRole;
  locale: string;
}

export interface CheckInIn {
  mood: number;
  craving: number;
  sleep_hours: number;
  self_efficacy: number;
  notes?: string;
}

export interface TimelinePoint {
  date: string;
  mood?: number;
  craving?: number;
  sleep_hours?: number;
  self_efficacy?: number;
  structural_score?: number;
  confidence_band?: string;
}

export interface TimelineOut {
  points: TimelinePoint[];
  baseline_available: boolean;
  window_days: number;
}

export interface ChatMessageOut {
  id: string;
  role: "user" | "assistant";
  content: string;
  ui_mode: string | null;
  created_at: string;
}

export interface ChatOut {
  reply: string;
  ui_mode: "normal" | "support" | "crisis";
  resources?: { name: string; description: string; contact: string }[];
}

export interface SafetyPlanOut {
  id: string;
  warning_signs?: string | null;
  coping_strategies?: string | null;
  social_supports?: string | null;
  professional_contacts?: string | null;
  safe_environment?: string | null;
  reasons_to_live?: string | null;
  updated_at: string;
}

export interface PatientSummaryOut {
  id: string;
  display_name: string;
  email: string;
  assignment_status: string;
  latest_alert_level?: number | null;
  latest_structural_score?: number | null;
  latest_confidence_band?: string | null;
  open_alerts: number;
  checkin_count?: number;
  last_checkin_at?: string | null;
}

export interface RiskAssessmentOut {
  id: string;
  alert_level: number;
  triggering_rules: string[] | Record<string, unknown>;
  input_signals: Record<string, unknown>;
  input_facts?: Record<string, unknown> | null;
  confidence?: number | null;
  assessment_reason: string;
  model_version: string;
  calculated_at: string;
  generated_alert_id?: string | null;
}

export interface SignalOut {
  id: string;
  signal_type: string;
  value: Record<string, unknown>;
  confidence_band?: string | null;
  timestamp: string;
}

export interface PatientDossierOut {
  patient: PatientSummaryOut;
  current_risk?: RiskAssessmentOut | null;
  timeline: TimelineOut;
  checkins: Array<{
    id: string;
    mood: number;
    craving: number;
    sleep_hours: number;
    self_efficacy: number;
    notes?: string | null;
    created_at: string;
  }>;
  diary: Array<{ id: string; content: string; created_at: string }>;
  facts: FactOut[];
  assessments: RiskAssessmentOut[];
  alerts: AlertOut[];
  signals: SignalOut[];
  safety_plan?: SafetyPlanOut | null;
  professional_protocol: Record<string, string>;
}

export interface AlertOut {
  id: string;
  user_id: string;
  alert_level: number;
  status: string;
  title: string;
  description: string;
  created_at: string;
  acknowledged_at?: string | null;
  resolved_at?: string | null;
  resolution_notes?: string | null;
  dismiss_reason?: string | null;
  patient_display_name?: string | null;
  patient_email?: string | null;
}

export interface AssignmentOut {
  id: string;
  patient_id: string;
  professional_id: string;
  status: string;
  requested_at: string;
  updated_at?: string | null;
  patient_email?: string | null;
  patient_display_name?: string | null;
  professional_email?: string | null;
  professional_display_name?: string | null;
}

export interface ConsentOut {
  id: string;
  consent_type: string;
  granted: boolean;
  version: string;
  granted_at: string;
  revoked_at?: string | null;
}

export interface FactOut {
  id: string;
  category: string;
  content: string;
  declared_by: string;
  is_active: boolean;
  created_at: string;
}

export interface NotificationOut {
  id: string;
  title?: string | null;
  body: string;
  alert_level?: number | null;
  status: string;
  created_at: string;
}

export interface AuditLogOut {
  id: string;
  actor_id?: string | null;
  actor_role?: string | null;
  action: string;
  entity_type?: string | null;
  entity_id?: string | null;
  extra?: unknown;
  created_at: string;
}

export const ROLE_LABELS: Record<UserRole, string> = {
  patient: "Paciente",
  therapist: "Terapeuta",
  supervisor: "Supervisor",
  admin_clinical: "Admin clínico",
};

export const ASSIGNMENT_STATUS_LABELS: Record<string, string> = {
  pending: "Pendiente de aceptación",
  active: "Activa",
  paused: "Pausada",
  ended: "Finalizada",
  rejected: "Rechazada",
};

export const CONSENT_LABELS: Record<string, string> = {
  data_processing: "Tratamiento de datos",
  professional_sharing: "Compartir con profesional",
  crisis_sms: "SMS de crisis (opcional)",
  research: "Uso en investigación",
};

export const FACT_CATEGORIES = [
  { value: "medication_taken", label: "Medicación tomada" },
  { value: "relapse", label: "Recaída / consumo" },
  { value: "consumption_crisis", label: "Crisis de consumo" },
  { value: "ideation_active", label: "Ideación suicida activa (autodeclarada)" },
  { value: "planning", label: "Planificación (autodeclarada)" },
  { value: "other", label: "Otro hecho" },
];
