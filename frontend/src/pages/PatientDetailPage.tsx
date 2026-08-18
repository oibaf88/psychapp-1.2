import { FormEvent, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import {
  Agent2TraceOut,
  api,
  FACT_CATEGORIES,
  LinguisticPoint,
  PatientDossierOut,
  RiskAssessmentOut,
  formatDateTime,
} from "../api";
import { useAuth } from "../auth/AuthContext";
import ModelStamp from "../components/ModelStamp";
import { Agent2TraceList, RiskAssessmentTraceList } from "../components/ClinicalTraceability";
import {
  CheckInChart,
  EventTimeline,
  LevelHistoryChart,
  LinguisticSignalChart,
  PsychosocialIndexChart,
  StructuralScoreChart,
  ZScoreChart,
} from "../components/ClinicalCharts";
import {
  EvidenceFeed,
  LevelExplanationCard,
  StructuralExplanationCard,
} from "../components/ClinicalExplain";
import CopilotPanel from "../components/CopilotPanel";
import PsychosocialPanel from "../components/PsychosocialPanel";

type Tab =
  | "resumen"
  | "metricas"
  | "psicosocial"
  | "evidencia"
  | "copiloto"
  | "chat"
  | "diario"
  | "checkins"
  | "hechos"
  | "alertas"
  | "motor"
  | "plan"
  | "tecnico";

export default function PatientDetailPage() {
  const { patientId } = useParams();
  const { user } = useAuth();
  const [dossier, setDossier] = useState<PatientDossierOut | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("resumen");
  const [busy, setBusy] = useState(false);
  const [factCategory, setFactCategory] = useState(FACT_CATEGORIES[0].value);
  const [factContent, setFactContent] = useState("");
  const [message, setMessage] = useState<string | null>(null);
  const [historyBusy, setHistoryBusy] = useState<"assessments" | "agent2" | null>(null);
  const [assessmentHasMore, setAssessmentHasMore] = useState(false);
  const [agent2HasMore, setAgent2HasMore] = useState(false);
  const [highlightedEvidence, setHighlightedEvidence] = useState<string | null>(null);

  const isTherapist = user?.role === "therapist";

  async function load() {
    if (!patientId) return;
    setError(null);
    try {
      const data = await api.get<PatientDossierOut>(
        `/api/v1/professional/patients/${patientId}/dossier?window_days=30`
      );
      setDossier(data);
      setAssessmentHasMore(data.assessments.length === 30);
      setAgent2HasMore((data.agent2_traces?.length ?? 0) === 50);
    } catch (e) {
      setError((e as Error).message);
      setDossier(null);
    }
  }

  async function loadOlderAssessments() {
    if (!patientId || !dossier || historyBusy) return;
    setHistoryBusy("assessments");
    setError(null);
    try {
      const rows = await api.get<RiskAssessmentOut[]>(
        `/api/v1/professional/patients/${patientId}/assessments?limit=100&offset=${dossier.assessments.length}`
      );
      setDossier((current) => {
        if (!current) return current;
        const known = new Set(current.assessments.map((row) => row.id));
        return { ...current, assessments: [...current.assessments, ...rows.filter((row) => !known.has(row.id))] };
      });
      setAssessmentHasMore(rows.length === 100);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setHistoryBusy(null);
    }
  }

  async function loadOlderAgent2Traces() {
    if (!patientId || !dossier || historyBusy) return;
    const currentRows = dossier.agent2_traces ?? [];
    setHistoryBusy("agent2");
    setError(null);
    try {
      const rows = await api.get<Agent2TraceOut[]>(
        `/api/v1/professional/patients/${patientId}/agent2-analyses?limit=100&offset=${currentRows.length}`
      );
      setDossier((current) => {
        if (!current) return current;
        const existing = current.agent2_traces ?? [];
        const known = new Set(existing.map((row) => row.id));
        return { ...current, agent2_traces: [...existing, ...rows.filter((row) => !known.has(row.id))] };
      });
      setAgent2HasMore(rows.length === 100);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setHistoryBusy(null);
    }
  }

  useEffect(() => {
    load().catch(() => undefined);
  }, [patientId]);

  async function reevaluate() {
    if (!patientId) return;
    setBusy(true);
    setMessage(null);
    try {
      await api.post(`/api/v1/professional/patients/${patientId}/reevaluate`);
      setMessage("Motor de riesgo reevaluado (determinista).");
      await load();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function declareFact(e: FormEvent) {
    e.preventDefault();
    if (!patientId) return;
    setBusy(true);
    setMessage(null);
    try {
      await api.post(`/api/v1/professional/patients/${patientId}/facts`, {
        category: factCategory,
        content: factContent,
      });
      setFactContent("");
      setMessage(
        "Hecho registrado por profesional. El motor de riesgo se ha reevaluado (N4 solo ideación/plan; N3 crisis de consumo)."
      );
      await load();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  /** Jump from a spike in the Agent 2 chart to the sentence behind it. */
  function openEvidence(point: LinguisticPoint) {
    if (!point.trace_id) return;
    setHighlightedEvidence(point.trace_id);
    setTab("evidencia");
    requestAnimationFrame(() =>
      document.getElementById(`evidence-${point.trace_id}`)?.scrollIntoView({ behavior: "smooth", block: "center" })
    );
  }

  if (error && !dossier) {
    return (
      <div className="page">
        <p>
          <Link to="/professional">← Volver</Link>
        </p>
        <p className="error">{error}</p>
      </div>
    );
  }

  if (!dossier) return <div className="loading">Cargando historial clínico…</div>;

  const p = dossier.patient;
  const metrics = dossier.metrics;
  const patientChat = dossier.chat_messages ?? [];

  const tabs: { id: Tab; label: string }[] = [
    { id: "resumen", label: "Resumen" },
    { id: "metricas", label: "Métricas" },
    {
      id: "psicosocial",
      label: `Contexto psicosocial (${dossier.psychosocial_explanation.active_count})`,
    },
    { id: "evidencia", label: `Evidencia (${dossier.evidence.length})` },
    { id: "copiloto", label: "Copiloto clínico" },
    { id: "chat", label: `Chat del paciente (${patientChat.length})` },
    { id: "diario", label: `Diario (${dossier.diary.length})` },
    { id: "checkins", label: `Check-ins (${dossier.checkins.length})` },
    { id: "hechos", label: `Hechos (${dossier.facts.length})` },
    { id: "alertas", label: `Alertas (${dossier.alerts.length})` },
    { id: "motor", label: "Motor de riesgo" },
    { id: "plan", label: "Plan de seguridad" },
    { id: "tecnico", label: "Detalle técnico" },
  ];

  return (
    <div className="page">
      <p>
        <Link to="/professional">← Volver a pacientes</Link> ·{" "}
        <Link to="/professional/manual">Manual del terapeuta</Link>
      </p>
      <h1>
        {p.display_name}{" "}
        <span className="meta" style={{ fontWeight: 400 }}>
          · {p.email}
        </span>
      </h1>
      <p className="subtitle">
        Historial clínico completo — <strong>no requiere alerta</strong> para consultar. Asignación:{" "}
        {p.assignment_status}. Check-ins: {p.checkin_count ?? dossier.checkins.length}. Alertas abiertas:{" "}
        {p.open_alerts}.
      </p>

      <LevelExplanationCard
        explanation={dossier.level_explanation}
        actions={
          <button type="button" disabled={busy} onClick={reevaluate}>
            Reevaluar riesgo
          </button>
        }
      />
      {message && <p className="info">{message}</p>}
      {error && <p className="error">{error}</p>}

      <div className="tabs" role="tablist" aria-label="Secciones del historial clínico">
        {tabs.map((t, index) => (
          <button
            key={t.id}
            id={`tab-${t.id}`}
            type="button"
            role="tab"
            aria-selected={tab === t.id}
            aria-controls={`tabpanel-${t.id}`}
            tabIndex={tab === t.id ? 0 : -1}
            className={tab === t.id ? "tab active" : "tab"}
            onClick={() => setTab(t.id)}
            onKeyDown={(event) => {
              let nextIndex: number | null = null;
              if (event.key === "ArrowRight") nextIndex = (index + 1) % tabs.length;
              if (event.key === "ArrowLeft") nextIndex = (index - 1 + tabs.length) % tabs.length;
              if (event.key === "Home") nextIndex = 0;
              if (event.key === "End") nextIndex = tabs.length - 1;
              if (nextIndex !== null) {
                event.preventDefault();
                const nextTab = tabs[nextIndex].id;
                setTab(nextTab);
                requestAnimationFrame(() => document.getElementById(`tab-${nextTab}`)?.focus());
              }
            }}
          >
            {t.label}
          </button>
        ))}
      </div>

      <div id={`tabpanel-${tab}`} role="tabpanel" aria-labelledby={`tab-${tab}`}>
        {tab === "resumen" && (
          <>
            <StructuralExplanationCard explanation={dossier.structural_explanation} />
            <section className="card">
              <h2>Evolución</h2>
              <div className="chart-grid">
                <LevelHistoryChart daily={metrics.daily_levels} levels={metrics.levels} />
                <StructuralScoreChart points={metrics.daily_structural} />
                <PsychosocialIndexChart points={metrics.daily_psychosocial} />
              </div>
            </section>
            <section className="card">
              <h2>Alertas y hechos registrados</h2>
              <EventTimeline events={metrics.events} />
            </section>
          </>
        )}

        {tab === "metricas" && (
          <section className="card">
            <h2>Métricas del paciente</h2>
            <p className="subtitle">
              Ventana de {metrics.window_days} días. {metrics.counts.checkins ?? 0} check-ins ·{" "}
              {metrics.counts.linguistic_points ?? 0} textos analizados · {metrics.counts.assessments ?? 0}{" "}
              evaluaciones · {metrics.counts.alerts ?? 0} alertas. Generado {formatDateTime(metrics.generated_at)}.
            </p>
            <div className="chart-grid">
              <LevelHistoryChart daily={metrics.daily_levels} levels={metrics.levels} />
              <StructuralScoreChart points={metrics.daily_structural} />
              <ZScoreChart points={metrics.daily_structural} />
              <PsychosocialIndexChart points={metrics.daily_psychosocial} />
              <CheckInChart points={metrics.checkins} />
              <LinguisticSignalChart points={metrics.linguistic} onSelect={openEvidence} />
            </div>
          </section>
        )}

        {tab === "psicosocial" && (
          <section className="card">
            <h2>Contexto psicosocial</h2>
            <p className="subtitle">
              Vivienda, convivencia, apoyo, familia, dinero, ocupación, pérdidas, vínculo con el tratamiento y
              entorno de consumo — extraído de lo que el paciente ha contado en el chat y el diario. Es la
              parte de su situación que suele moverse <strong>antes</strong> que el ánimo. Cada tarjeta lleva
              la frase literal de la que sale: confírmala o refútala.
            </p>
            <PsychosocialPanel
              patientId={p.id}
              explanation={dossier.psychosocial_explanation}
              canAdjudicate={isTherapist}
              onChanged={load}
            />
          </section>
        )}

        {tab === "evidencia" && (
          <section className="card">
            <h2>Evidencia: qué escribió, qué se leyó y qué pasó</h2>
            <p className="subtitle">
              Cada tarjeta es <strong>un texto real del paciente</strong> (chat o diario), la lectura que hizo
              el Agente 2 y lo que el motor determinista concluyó a partir de ella. Si una alerta te parece
              injustificada, aquí está la frase que la produjo.
            </p>
            <EvidenceFeed items={dossier.evidence} highlightId={highlightedEvidence} />
          </section>
        )}

        {tab === "copiloto" && (
          <section className="card">
            <h2>Copiloto clínico (Agente 3)</h2>
            <CopilotPanel patientId={p.id} patientName={p.display_name} />
          </section>
        )}

        {tab === "chat" && (
          <section className="card">
            <h2>Conversación del paciente con el asistente</h2>
            <p className="subtitle">
              El chat es una <strong>fuente clínica de pleno derecho</strong>: el Agente 2 lo analiza igual que
              el diario, y una alerta de nivel 4 puede salir de un solo mensaje. Los mensajes marcados como
              «crisis» o «apoyo» son turnos en los que el sistema añadió su bloque fijo de seguridad.
            </p>
            {patientChat.length === 0 && <p className="meta">Este paciente no ha usado el chat todavía.</p>}
            <div className="transcript">
              {patientChat.map((m) => (
                <div key={m.id} className={`transcript-turn transcript-${m.role}`}>
                  <div className="meta">
                    {m.role === "user" ? p.display_name : "Asistente (Agente 1)"} ·{" "}
                    {formatDateTime(m.created_at)}
                    {m.ui_mode && m.ui_mode !== "normal" ? ` · modo ${m.ui_mode}` : ""}
                  </div>
                  <p style={{ whiteSpace: "pre-wrap" }}>{m.content}</p>
                  {m.role === "assistant" && <ModelStamp message={m} />}
                </div>
              ))}
            </div>
          </section>
        )}

        {tab === "diario" && (
          <section className="card">
            <h2>Entradas de diario</h2>
            <p className="subtitle">
              Texto literal del paciente. Igual que el chat, es fuente de análisis del Agente 2.
            </p>
            {dossier.diary.length === 0 && <p className="meta">Sin entradas.</p>}
            {dossier.diary.map((d) => (
              <article key={d.id} className="diary-entry">
                <div className="meta">{formatDateTime(d.created_at)}</div>
                <p style={{ whiteSpace: "pre-wrap" }}>{d.content}</p>
              </article>
            ))}
          </section>
        )}

        {tab === "checkins" && (
          <section className="card">
            <h2>Histórico de check-ins</h2>
            <CheckInChart points={metrics.checkins} />
            <div className="table-wrap">
              <table className="table">
                <thead>
                  <tr>
                    <th>Fecha</th>
                    <th>Ánimo</th>
                    <th>Craving</th>
                    <th>Sueño</th>
                    <th>Autoeficacia</th>
                    <th>Notas</th>
                  </tr>
                </thead>
                <tbody>
                  {dossier.checkins.map((c) => (
                    <tr key={c.id}>
                      <td className="meta">{formatDateTime(c.created_at)}</td>
                      <td>{c.mood}</td>
                      <td>{c.craving}</td>
                      <td>{c.sleep_hours}</td>
                      <td>{c.self_efficacy}</td>
                      <td>{c.notes || "—"}</td>
                    </tr>
                  ))}
                  {dossier.checkins.length === 0 && (
                    <tr>
                      <td colSpan={6}>Sin check-ins.</td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </section>
        )}

        {tab === "hechos" && (
          <section className="card">
            <h2>Hechos confirmados (no sobrescribibles por el LLM)</h2>
            <p className="subtitle">
              Un hecho es una declaración, no una inferencia. <code>ideation_active</code> y{" "}
              <code>planning</code> elevan a nivel 4 durante 48 h; <code>consumption_crisis</code> a nivel 3.
              Usa <code>correction</code> para dejar constancia de un falso positivo del Agente 2.
            </p>
            {!isTherapist && <p className="info">Solo el terapeuta asignado ve y registra hechos (RBAC).</p>}
            {isTherapist && (
              <form onSubmit={declareFact} className="stack-form" style={{ marginBottom: 16 }}>
                <label>
                  Categoría
                  <select value={factCategory} onChange={(e) => setFactCategory(e.target.value)}>
                    {FACT_CATEGORIES.map((c) => (
                      <option key={c.value} value={c.value}>
                        {c.label}
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  Contenido
                  <textarea
                    required
                    rows={2}
                    value={factContent}
                    onChange={(e) => setFactContent(e.target.value)}
                    placeholder="Hecho clínico confirmado…"
                  />
                </label>
                <button type="submit" disabled={busy}>
                  Registrar hecho y reevaluar
                </button>
              </form>
            )}
            <ul className="plain-list">
              {dossier.facts.map((f) => (
                <li key={f.id}>
                  <strong>{f.category}</strong> ({f.declared_by}) · {formatDateTime(f.created_at)}
                  <br />
                  {f.content}
                </li>
              ))}
              {dossier.facts.length === 0 && <li>Sin hechos activos.</li>}
            </ul>
          </section>
        )}

        {tab === "alertas" && (
          <section className="card">
            <h2>Alertas profesionales</h2>
            <p className="subtitle">
              Solo los niveles ≥ 3 crean alerta. Cada una muestra la regla que la disparó y la evidencia
              concreta detrás.
            </p>
            {dossier.alerts.length === 0 && <p>No hay alertas (el seguimiento no depende de ellas).</p>}
            {dossier.alerts.map((a) => (
              <div key={a.id} className={`alert-snippet alert-level-${a.alert_level}`}>
                <strong>
                  L{a.alert_level} · {a.status}
                </strong>{" "}
                — {a.title}
                <div className="meta">
                  {formatDateTime(a.created_at)}
                  {a.driver_family_label ? ` · disparada por: ${a.driver_family_label}` : ""}
                </div>
                {a.plain_explanation && <p>{a.plain_explanation}</p>}
                {a.what_now && (
                  <p>
                    <strong>Qué hacer:</strong> {a.what_now}
                  </p>
                )}
                {a.evidence?.text && (
                  <blockquote className="evidence-quote">
                    <span className="meta">
                      {a.evidence.source_label} · {formatDateTime(a.evidence.created_at)}
                    </span>
                    <br />
                    {a.evidence.text}
                  </blockquote>
                )}
                {a.resolution_notes && (
                  <p className="meta">Resolución: {a.resolution_notes}</p>
                )}
                {a.dismiss_reason && <p className="meta">Descartada: {a.dismiss_reason}</p>}
              </div>
            ))}
            <p>
              <Link to="/professional/alerts">Ir a gestión de alertas →</Link>
            </p>
          </section>
        )}

        {tab === "motor" && (
          <section className="card">
            <h2>Cómo decidió el motor cada nivel</h2>
            <p className="subtitle">
              Las reglas se evalúan en orden y gana la primera que se cumple. Esta pantalla muestra el cálculo
              tal y como se guardó en su momento; nunca lo recalcula con datos nuevos.
            </p>
            <RiskAssessmentTraceList assessments={dossier.assessments} />
            {assessmentHasMore && (
              <button type="button" disabled={historyBusy !== null} onClick={loadOlderAssessments}>
                {historyBusy === "assessments" ? "Cargando…" : "Cargar evaluaciones anteriores"}
              </button>
            )}
            <p className="meta">
              Mostrando {dossier.assessments.length} evaluaciones, de más reciente a más antigua.
            </p>
          </section>
        )}

        {tab === "plan" && (
          <section className="card">
            <h2>Plan de seguridad del paciente</h2>
            {!dossier.safety_plan && <p className="meta">Sin plan guardado.</p>}
            {dossier.safety_plan && (
              <dl className="plan-dl">
                {(
                  [
                    ["Señales de alarma", dossier.safety_plan.warning_signs],
                    ["Estrategias de afrontamiento", dossier.safety_plan.coping_strategies],
                    ["Apoyos sociales", dossier.safety_plan.social_supports],
                    ["Contactos profesionales", dossier.safety_plan.professional_contacts],
                    ["Entorno seguro", dossier.safety_plan.safe_environment],
                    ["Razones para vivir", dossier.safety_plan.reasons_to_live],
                  ] as const
                ).map(([label, val]) => (
                  <div key={label}>
                    <dt>{label}</dt>
                    <dd>{val || "—"}</dd>
                  </div>
                ))}
              </dl>
            )}
          </section>
        )}

        {tab === "tecnico" && (
          <section className="card">
            <h2>Detalle técnico</h2>
            <p className="subtitle">
              Trazabilidad completa de cada llamada al Agente 2: modelo, versión de prompt y esquema, tokens,
              latencia y errores. Útil para auditoría e incidencias; no hace falta para el trabajo clínico
              diario.
            </p>
            <Agent2TraceList
              traces={dossier.agent2_traces ?? []}
              assessments={dossier.assessments}
              legacySignalCount={
                dossier.signals.filter(
                  (signal) => signal.signal_type === "linguistic_analysis" && !signal.agent2_trace_id
                ).length
              }
            />
            {agent2HasMore && (
              <button type="button" disabled={historyBusy !== null} onClick={loadOlderAgent2Traces}>
                {historyBusy === "agent2" ? "Cargando…" : "Cargar trazas anteriores"}
              </button>
            )}
            <h3>Protocolo de notificación (texto fijo del servidor, no generado por el LLM)</h3>
            <p className="info">{dossier.professional_protocol.notes}</p>
            <h4>Plantilla Nivel 3</h4>
            <pre className="protocol-box">{dossier.professional_protocol.level3}</pre>
            <h4>Plantilla Nivel 4</h4>
            <pre className="protocol-box">{dossier.professional_protocol.level4}</pre>
          </section>
        )}
      </div>
    </div>
  );
}
