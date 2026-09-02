// VITE_API_BASE_URL is the only deployed API source. Legacy localStorage
// overrides are ignored so a bad saved host cannot lock users out.
const API_BASE_KEY = "psychapp_api_base";

export function getApiBase(): string {
  return (import.meta.env.VITE_API_BASE_URL ?? "").replace(/\/$/, "");
}

export function getLegacyApiBaseOverride(): string {
  return (localStorage.getItem(API_BASE_KEY) || "").trim();
}

export function clearLegacyApiBaseOverride() {
  localStorage.removeItem(API_BASE_KEY);
}

export function setApiBase(_url: string | null) {
  clearLegacyApiBaseOverride();
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
  del: <T,>(path: string) => request<T>(path, { method: "DELETE" }),
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
  mood?: number | null;
  craving?: number | null;
  sleep_hours?: number | null;
  self_efficacy?: number | null;
  structural_score?: number | null;
  structural_calculation_version?: string | null;
  confidence_band?: string | null;
}

export interface TimelineOut {
  points: TimelinePoint[];
  baseline_available: boolean;
  window_days: number;
  daily_statistics?: DailyStatisticsOut | null;
}

/** Patient history contains only the four values entered in the check-in. */
export interface PatientTimelineOut {
  points: Pick<TimelinePoint, "date" | "mood" | "craving" | "sleep_hours" | "self_efficacy">[];
  window_days: number;
}

export interface ChatMessageOut {
  id: string;
  role: "user" | "assistant";
  content: string;
  ui_mode: string | null;
  /**
   * Which model wrote this assistant turn. Null on the patient's own
   * messages, and on replies built from the server-owned safety templates —
   * those have no model behind them, which is worth seeing as such.
   */
  provider?: string | null;
  model?: string | null;
  provider_base_url?: string | null;
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

export interface RiskRuleEvaluation {
  rule_id?: string;
  rule?: string;
  label?: string;
  level?: number;
  alert_level?: number;
  evaluated?: boolean;
  matched?: boolean;
  passed?: boolean;
  result?: boolean;
  condition?: string;
  explanation?: string;
  observed?: unknown;
  threshold?: unknown;
  evidence?: Record<string, unknown> | null;
  [key: string]: unknown;
}

export interface RiskCalculationTrace {
  engine_version?: string;
  formula?: string;
  structural_score?: number | null;
  confidence_band?: string | null;
  z_scores?: Record<string, number | null> | null;
  evaluated_rules?: RiskRuleEvaluation[] | Record<string, unknown> | null;
  rule_evaluations?: RiskRuleEvaluation[] | Record<string, unknown> | null;
  decision_path?: RiskRuleEvaluation[] | Record<string, unknown> | null;
  selected_rule?: string | null;
  stopped_at_rule?: string | null;
  thresholds?: Record<string, unknown> | null;
  components?: Record<string, unknown> | null;
  [key: string]: unknown;
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
  correlation_id?: string | null;
  analysis_trace_id?: string | null;
  agent2_trace_id?: string | null;
  linguistic_signal_id_used?: string | null;
  calculation_trace?: RiskCalculationTrace | null;
}

export interface SignalOut {
  id: string;
  signal_type: string;
  value: Record<string, unknown>;
  confidence_band?: string | null;
  timestamp: string;
  agent2_trace_id?: string | null;
}

export interface Agent2TraceOut {
  id: string;
  correlation_id?: string | null;
  status: string;
  source_type?: string | null;
  source_id?: string | null;
  provider?: string | null;
  /** Where the call went. Null for the official API, set for a self-hosted one. */
  provider_base_url?: string | null;
  model?: string | null;
  response_model?: string | null;
  requested_model?: string | null;
  effort?: string | null;
  max_tokens?: number | null;
  source_text?: string | null;
  analysis?: Record<string, unknown> | null;
  output?: Record<string, unknown> | null;
  signal_id?: string | null;
  risk_assessment_id?: string | null;
  used_by_risk_engine?: boolean;
  provider_message_id?: string | null;
  provider_request_id?: string | null;
  input_tokens?: number | null;
  output_tokens?: number | null;
  cache_creation_input_tokens?: number | null;
  cache_read_input_tokens?: number | null;
  latency_ms?: number | null;
  stop_reason?: string | null;
  error_kind?: string | null;
  error_code?: string | null;
  http_status?: number | null;
  prompt_version?: string | null;
  prompt_sha256?: string | null;
  schema_version?: string | null;
  schema_sha256?: string | null;
  app_release?: string | null;
  started_at?: string | null;
  completed_at?: string | null;
  created_at?: string | null;
}

// ------------------------------------------- therapist-facing explanations ---
/** Which KIND of evidence raised the level. This is the first thing a
 *  therapist needs, and the reason a high structural score can sit next to
 *  a level-4 alert without either being wrong. */
export type DriverFamily =
  | "hecho_confirmado"
  | "senal_linguistica"
  | "desviacion_estructural"
  | "convergencia"
  | "contexto_psicosocial"
  | "sin_criterios";

export interface EvidenceRef {
  kind?: string;
  source_type?: string | null;
  source_label?: string | null;
  source_id?: string | null;
  text?: string | null;
  excerpt?: string | null;
  created_at?: string | null;
  category?: string | null;
  declared_by?: string | null;
  analysis?: Record<string, unknown> | null;
  trace_id?: string | null;
  signal_id?: string | null;
  domain_label?: string | null;
  summary?: string | null;
  status?: string | null;
  observation_id?: string | null;
}

export interface LevelExplanationOut {
  level?: number | null;
  level_label: string;
  level_meaning: string;
  headline: string;
  rule_code?: string | null;
  rule_title?: string | null;
  rule_explanation?: string | null;
  driver_family: DriverFamily;
  driver_family_label: string;
  driver_evidence_kind?: string | null;
  what_now?: string | null;
  structural_reconciliation?: string | null;
  driver_evidence?: EvidenceRef | null;
  calculated_at?: string | null;
  assessment_id?: string | null;
  generated_alert_id?: string | null;
}

export interface StructuralVariableOut {
  key: string;
  label: string;
  note?: string | null;
  baseline_mean?: number | null;
  baseline_std?: number | null;
  recent_mean?: number | null;
  difference?: number | null;
  z_score?: number | null;
  abs_z?: number | null;
  direction: "peor" | "mejor" | "igual" | "cambio" | "sin_datos";
  reading: string;
}

export interface StructuralExplanationOut {
  score?: number | null;
  band?: string | null;
  band_label?: string | null;
  band_meaning?: string | null;
  scale_note: string;
  summary: string;
  direction_summary?: string | null;
  variables: StructuralVariableOut[];
  composite_z?: number | null;
  adverse_composite_z?: number | null;
  favourable_composite_z?: number | null;
  deterioration_score?: number | null;
  deterioration_band?: string | null;
  calculation_version?: string | null;
  baseline_is_stale?: boolean | null;
  baseline_sample_count?: number | null;
  recent_sample_count?: number | null;
  sleep_trend?: string | null;
  sleep_trend_slope?: number | null;
  caveats: string[];
}

// ------------------------------------------------- psychosocial context ---
/** A social determinant the patient mentioned, as extracted by Agent 4.
 *  `status` is the human half of the fact/inference wall: a therapist can
 *  confirm or refute, and that decision outranks the model's confidence. */
export interface PsychosocialDomainOut {
  domain: string;
  label: string;
  category: string;
  category_label: string;
  valence: "risk" | "protective" | "neutral";
  intensity: number;
  confidence: number;
  status: "inferred" | "confirmed" | "refuted";
  summary: string;
  quote: string;
  observed_at?: string | null;
  observation_id: string;
  weight: number;
  contribution: number;
  is_change: boolean;
  group?: string | null;
  group_label?: string | null;
  risk_value?: number | null;
  counts_for_scoring: boolean;
  is_stale: boolean;
  has_pending_update: boolean;
  session_question?: string | null;
}

/** One of the four deterministic indices, with the threshold it is read against. */
export interface PsychosocialIndexReadingOut {
  key: string;
  label: string;
  value?: number | null;
  state: "ok" | "alerta" | "sin_datos";
  threshold: number;
  threshold_label: string;
  meaning: string;
  note: string;
}

export interface PsychosocialIndicesOut {
  support_index?: number | null;
  material_adversity_index?: number | null;
  interpersonal_risk_index?: number | null;
  relapse_context_index?: number | null;
}

export interface PsychosocialLeaveTakingOut {
  domain: string;
  label?: string | null;
  category: string;
  category_label?: string | null;
  summary?: string | null;
  quote?: string | null;
  observed_at?: string | null;
  observation_id?: string | null;
}

export interface PsychosocialSessionQuestionOut {
  domain: string;
  domain_label: string;
  question: string;
  reason: string;
  quote?: string | null;
}

export interface PsychosocialAcuteChangeOut {
  domain: string;
  label: string;
  category: string;
  category_label: string;
  summary: string;
  quote: string;
  observed_at?: string | null;
  observation_id: string;
}

export interface PsychosocialExplanationOut {
  index?: number | null;
  band: string;
  band_label: string;
  scale_note: string;
  summary: string;
  driver_summary?: string | null;
  protective_summary?: string | null;
  domains: PsychosocialDomainOut[];
  acute_changes: PsychosocialAcuteChangeOut[];
  has_acute_change: boolean;
  acute_note?: string | null;
  caveats: string[];
  indices: PsychosocialIndicesOut;
  index_readings: PsychosocialIndexReadingOut[];
  leave_taking?: PsychosocialLeaveTakingOut | null;
  leave_taking_note?: string | null;
  interpersonal_recent_evidence: string[];
  pending_update_domains: string[];
  stale_domains: string[];
  session_questions: PsychosocialSessionQuestionOut[];
  observation_count: number;
  active_count: number;
  confirmed_count: number;
  refuted_count: number;
}

export interface PsychosocialObservationOut {
  id: string;
  domain: string;
  domain_label: string;
  category: string;
  category_label: string;
  valence: "risk" | "protective" | "neutral";
  intensity: number;
  confidence: number;
  is_change: boolean;
  status: "inferred" | "confirmed" | "refuted";
  summary: string;
  evidence_quote: string;
  source_type: string;
  source_label: string;
  source_id?: string | null;
  adjudication_note?: string | null;
  adjudicated_at?: string | null;
  observed_at?: string | null;
}

export interface PsychosocialPoint {
  at: string;
  date: string;
  index: number;
  band?: string | null;
  has_acute_change: boolean;
  active_count?: number | null;
  assessment_id: string;
}

export interface PsychosocialEventPoint {
  at: string;
  date: string;
  domain: string;
  domain_label: string;
  category: string;
  category_label: string;
  valence: "risk" | "protective" | "neutral";
  intensity: number;
  confidence: number;
  is_change: boolean;
  status: string;
  summary: string;
  quote: string;
  source_label: string;
  observation_id: string;
}

export interface EvidenceItemOut {
  trace_id: string;
  correlation_id: string;
  source_type: string;
  source_label: string;
  source_id?: string | null;
  source_text: string;
  source_excerpt: string;
  source_created_at?: string | null;
  analysed_at?: string | null;
  status: string;
  analysis?: Record<string, unknown> | null;
  flags: string[];
  reading: string;
  short_rationale?: string | null;
  signal_id?: string | null;
  assessment_id?: string | null;
  resulting_level?: number | null;
  resulting_rule?: string | null;
  used_by_risk_engine: boolean;
  alert_id?: string | null;
  alert_level?: number | null;
  alert_status?: string | null;
  alert_title?: string | null;
}

export interface CheckInPoint {
  at: string;
  date: string;
  mood: number;
  craving: number;
  sleep_hours: number;
  self_efficacy: number;
  notes?: string | null;
}

export interface StructuralPoint {
  at: string;
  date: string;
  score?: number | null;
  calculation_version?: string | null;
  band?: string | null;
  composite_z?: number | null;
  z_mood?: number | null;
  z_craving_inv?: number | null;
  z_sleep_hours?: number | null;
  z_self_efficacy?: number | null;
}

export interface LinguisticPoint {
  at: string;
  date: string;
  signal_id: string;
  rumination_score?: number | null;
  negative_valence?: number | null;
  urgency_level?: number | null;
  ambivalence?: number | null;
  ideation_direct?: boolean | null;
  ideation_indirect?: boolean | null;
  consumption_crisis?: boolean | null;
  is_active?: boolean;
  emotional_complexity?: string | null;
  short_rationale?: string | null;
  source_type?: string | null;
  source_label?: string | null;
  source_id?: string | null;
  source_excerpt?: string | null;
  trace_id?: string | null;
}

export interface LevelPoint {
  at: string;
  date: string;
  level: number;
  assessment_id: string;
  rule?: string | null;
  rule_family?: DriverFamily | null;
  reason?: string | null;
  generated_alert_id?: string | null;
}

export interface MetricEvent {
  at: string;
  date: string;
  kind: "alert" | "fact";
  level?: number | null;
  label: string;
  status: string;
  id: string;
}

export interface DailyStatisticVariable {
  key: string;
  label: string;
  kind: "numeric" | "boolean" | "categorical";
  unit?: string | null;
  source: string;
  aggregation: string;
}

export interface StatisticSummary {
  n: number;
  mean?: number | null;
  sd?: number | null;
  min?: number | null;
  max?: number | null;
  true_count?: number;
  false_count?: number;
  rate?: number | null;
  counts?: Record<string, number>;
  day_counts?: Record<string, number>;
  observed_days?: number;
  missing_days?: number;
}

export interface DailyStatisticRow {
  date: string;
  counts: Record<string, number>;
  statistics: Record<string, StatisticSummary>;
  categories: Record<string, StatisticSummary>;
  [key: string]: unknown;
}

export interface DailyCorrelation {
  x: string;
  y: string;
  n: number;
  r: number | null;
  status: "ok" | "insufficient_pairs" | "constant_series";
}

export interface DailyStatisticsOut {
  timezone: string;
  window_days: number;
  daily: DailyStatisticRow[];
  variables: DailyStatisticVariable[];
  summary: Record<string, StatisticSummary>;
  correlations: DailyCorrelation[];
  notes: string[];
}

export interface PatientMetricsOut {
  window_days: number;
  generated_at?: string | null;
  checkins: CheckInPoint[];
  structural: StructuralPoint[];
  /** One point per day (the last of that day). The raw `structural` series
   *  has one point per risk run, which is unreadable over weeks. */
  daily_structural: StructuralPoint[];
  /** Psychosocial index over time. Computed only inside a risk evaluation,
   *  so the assessment history is its history. */
  psychosocial: PsychosocialPoint[];
  daily_psychosocial: PsychosocialPoint[];
  psychosocial_events: PsychosocialEventPoint[];
  linguistic: LinguisticPoint[];
  levels: LevelPoint[];
  daily_levels: { date: string; max_level: number }[];
  events: MetricEvent[];
  counts: Record<string, number>;
  daily_statistics?: DailyStatisticsOut | null;
}

export interface PatientChatMessageOut {
  id: string;
  role: "user" | "assistant";
  content: string;
  ui_mode?: string | null;
  provider?: string | null;
  model?: string | null;
  provider_base_url?: string | null;
  created_at: string;
}

export interface CopilotMessageOut {
  id: string;
  patient_id: string;
  role: "user" | "assistant";
  content: string;
  kind: "question" | "answer" | "summary";
  requested_model?: string | null;
  context_window_days?: number | null;
  context_counts?: Record<string, unknown> | null;
  error_kind?: string | null;
  created_at: string;
}

export interface PatientDossierOut {
  patient: PatientSummaryOut;
  current_risk?: RiskAssessmentOut | null;
  level_explanation: LevelExplanationOut;
  structural_explanation: StructuralExplanationOut;
  psychosocial_explanation: PsychosocialExplanationOut;
  metrics: PatientMetricsOut;
  evidence: EvidenceItemOut[];
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
  chat_messages: PatientChatMessageOut[];
  facts: FactOut[];
  assessments: RiskAssessmentOut[];
  alerts: AlertOut[];
  signals: SignalOut[];
  agent2_traces?: Agent2TraceOut[];
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
  related_assessment_id?: string | null;
  rule_code?: string | null;
  rule_title?: string | null;
  driver_family?: DriverFamily | null;
  driver_family_label?: string | null;
  plain_explanation?: string | null;
  what_now?: string | null;
  evidence?: EvidenceRef | null;
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

export const BAND_LABELS: Record<string, string> = {
  stable: "estable",
  transition: "transición",
  unstable: "inestable",
  insufficient_data: "datos insuficientes",
};

export const DRIVER_FAMILY_SHORT: Record<string, string> = {
  hecho_confirmado: "Hecho declarado",
  senal_linguistica: "Texto del paciente",
  desviacion_estructural: "Check-ins",
  convergencia: "Varias señales",
  contexto_psicosocial: "Contexto social",
  sin_criterios: "Sin criterios",
};

export const PSYCHOSOCIAL_BAND_LABELS: Record<string, string> = {
  alta: "alta",
  moderada: "moderada",
  baja: "baja",
  sin_datos: "sin datos",
};

export const LEVEL_SHORT_LABELS: Record<number, string> = {
  0: "Autogestión",
  1: "Autogestión / sin datos",
  2: "Prevención",
  3: "Alarma profesional",
  4: "Emergencia",
};

export function formatDateTime(value?: string | null): string {
  if (!value) return "—";
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? "—" : parsed.toLocaleString();
}

export function formatDay(value?: string | null): string {
  if (!value) return "—";
  // A daily bucket is a calendar date in the API's timezone, not UTC
  // midnight. Keep it on that date even when the viewer is abroad.
  const parsed = new Date(/^\d{4}-\d{2}-\d{2}$/.test(value) ? `${value}T12:00:00` : value);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleDateString();
}

export const FACT_CATEGORIES = [
  { value: "medication_taken", label: "Medicación tomada" },
  { value: "relapse", label: "Recaída / consumo" },
  { value: "consumption_crisis", label: "Crisis de consumo" },
  { value: "ideation_active", label: "Ideación suicida activa (autodeclarada)" },
  { value: "planning", label: "Planificación (autodeclarada)" },
  { value: "other", label: "Otro hecho" },
];

// ------------------------------------------------ runtime LLM endpoint ----
// PsychDeep ships pointed at Claude. These types back the Settings screen,
// where the two inference agents can be aimed at a model you host yourself.

export interface LLMEndpointSummary {
  provider: "anthropic" | "openai_compatible" | string;
  provider_label: string;
  label: string;
  base_url: string | null;
  chat_model: string;
  analysis_model: string;
  /** Agent 3, resolved — falls back to chat_model. For display. */
  copilot_model: string;
  /** What was actually configured. Empty means "follows chat". For the form. */
  copilot_model_explicit: string;
  copilot_model_is_inherited: boolean;
  max_tokens: number;
  timeout_seconds: number;
  source: "environment" | "runtime" | string;
  config_id: string | null;
  updated_at: string | null;
  has_api_key: boolean;
}

export interface LLMEndpointStatusOut {
  active: LLMEndpointSummary;
  environment_default: LLMEndpointSummary;
  /** The deployment permits a runtime override at all. */
  override_allowed: boolean;
  /** ...and this account may perform one. Only admin_clinical may. */
  can_edit: boolean;
  is_local: boolean;
  notice: string | null;
}

export interface LLMEndpointConfigIn {
  provider: "anthropic" | "openai_compatible";
  base_url?: string | null;
  chat_model: string;
  analysis_model: string;
  /** Blank means "same model as chat" — the backend applies the fallback. */
  copilot_model?: string | null;
  /** null keeps the stored key; "" clears it. It is never sent back out. */
  api_key?: string | null;
  max_tokens: number;
  timeout_seconds: number;
  label?: string | null;
}

export interface LLMEndpointTestIn {
  provider: "anthropic" | "openai_compatible";
  base_url?: string | null;
  chat_model: string;
  analysis_model?: string | null;
  copilot_model?: string | null;
  api_key?: string | null;
  timeout_seconds: number;
}

export interface LLMEndpointTestOut {
  ok: boolean;
  detail: string;
  sample?: string | null;
  error_code?: string | null;
  base_url?: string | null;
}

export const llmSettingsApi = {
  read: () => api.get<LLMEndpointStatusOut>("/api/v1/settings/llm"),
  save: (body: LLMEndpointConfigIn) => api.put<LLMEndpointStatusOut>("/api/v1/settings/llm", body),
  reset: () => api.del<LLMEndpointStatusOut>("/api/v1/settings/llm"),
  test: (body: LLMEndpointTestIn) => api.post<LLMEndpointTestOut>("/api/v1/settings/llm/test", body),
};

/**
 * How one stored interaction names the model behind it.
 *
 * History spans endpoints: a reply from March may have come from Claude and
 * one from April from a local Llama. Rows written before provenance was
 * recorded say so plainly rather than inheriting whatever is configured
 * today — presenting today's setting as though it were the record would be
 * the one genuinely misleading option.
 */
export function modelProvenanceLabel(source: {
  provider?: string | null;
  model?: string | null;
  provider_base_url?: string | null;
}): string | null {
  if (!source.provider && !source.model) return null;
  const model = source.model || "modelo sin identificar";
  if (source.provider === "openai_compatible") {
    const host = hostOf(source.provider_base_url);
    return host ? `${model} · servidor propio (${host})` : `${model} · servidor propio`;
  }
  if (source.provider === "anthropic") return `${model} · API de Anthropic`;
  return model;
}

function hostOf(url?: string | null): string | null {
  if (!url) return null;
  try {
    return new URL(url).host;
  } catch {
    return url;
  }
}
