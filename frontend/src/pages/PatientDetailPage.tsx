import { FormEvent, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis, Legend } from "recharts";
import { Agent2TraceOut, api, FACT_CATEGORIES, PatientDossierOut, RiskAssessmentOut } from "../api";
import { useAuth } from "../auth/AuthContext";
import { Agent2TraceList, RiskAssessmentTraceList } from "../components/ClinicalTraceability";

type Tab =
  | "resumen"
  | "checkins"
  | "diario"
  | "hechos"
  | "evaluaciones"
  | "agent2"
  | "alertas"
  | "plan"
  | "protocolo";

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
  const risk = dossier.current_risk;
  const level = risk?.alert_level;

  const tabs: { id: Tab; label: string }[] = [
    { id: "resumen", label: "Resumen" },
    { id: "checkins", label: `Check-ins (${dossier.checkins.length})` },
    { id: "diario", label: `Diario (${dossier.diary.length})` },
    { id: "hechos", label: `Hechos (${dossier.facts.length})` },
    { id: "evaluaciones", label: `Motor de riesgo (${dossier.assessments.length})` },
    { id: "agent2", label: `Agent 2 (${dossier.agent2_traces?.length ?? 0})` },
    { id: "alertas", label: `Alertas (${dossier.alerts.length})` },
    { id: "plan", label: "Plan seguridad" },
    { id: "protocolo", label: "Protocolo" },
  ];

  return (
    <div className="page">
      <p>
        <Link to="/professional">← Volver a pacientes</Link>
      </p>
      <h1>
        {p.display_name}{" "}
        <span className="meta" style={{ fontWeight: 400 }}>
          · {p.email}
        </span>
      </h1>
      <p className="subtitle">
        Historial clínico completo — <strong>no requiere alerta</strong> para consultar. Asignación:{" "}
        {p.assignment_status}. Check-ins: {p.checkin_count ?? dossier.checkins.length}.
      </p>

      <section className={`card risk-banner level-${level ?? "na"}`}>
        <div className="risk-banner-grid">
          <div>
            <div className="meta">Nivel actual (motor determinista)</div>
            <div className="risk-level">
              {level == null ? "Sin evaluación" : `Nivel ${level}`}
            </div>
            <div className="meta">{risk?.assessment_reason || "Aún no hay evaluación de riesgo."}</div>
          </div>
          <div>
            <div className="meta">Score estructural / banda</div>
            <div>
              {p.latest_structural_score != null ? Number(p.latest_structural_score).toFixed(2) : "—"} ·{" "}
              {p.latest_confidence_band || "—"}
            </div>
            <div className="meta">Alertas abiertas (rule_engine): {p.open_alerts}</div>
          </div>
          <div className="alert-actions">
            <button type="button" disabled={busy} onClick={reevaluate}>
              Reevaluar riesgo
            </button>
          </div>
        </div>
        {risk?.triggering_rules && (
          <p className="meta">
            Reglas: {Array.isArray(risk.triggering_rules) ? risk.triggering_rules.join(", ") : JSON.stringify(risk.triggering_rules)}
          </p>
        )}
        {message && <p className="info">{message}</p>}
        {error && <p className="error">{error}</p>}
      </section>

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
        <section className="card">
          <h2>Tendencia 30 días</h2>
          {dossier.timeline.points.length > 0 ? (
            <ResponsiveContainer width="100%" height={320}>
              <LineChart data={dossier.timeline.points}>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis dataKey="date" tick={{ fontSize: 11 }} />
                <YAxis yAxisId="left" domain={[0, 10]} />
                <YAxis yAxisId="right" orientation="right" domain={[0, 1]} />
                <Tooltip />
                <Legend />
                <Line yAxisId="left" type="monotone" dataKey="mood" name="Ánimo" stroke="#4f8ef7" connectNulls />
                <Line yAxisId="left" type="monotone" dataKey="craving" name="Craving" stroke="#f76c4f" connectNulls />
                <Line yAxisId="left" type="monotone" dataKey="sleep_hours" name="Sueño" stroke="#9b59b6" connectNulls />
                <Line
                  yAxisId="right"
                  type="monotone"
                  dataKey="structural_score"
                  name="Score estructural"
                  stroke="#2e7d32"
                  connectNulls
                  strokeWidth={2}
                />
              </LineChart>
            </ResponsiveContainer>
          ) : (
            <p>Sin puntos de timeline aún (el paciente debe registrar check-ins).</p>
          )}
          {!dossier.timeline.baseline_available && (
            <p className="info">Línea base personal aún no disponible (&lt;5 check-ins).</p>
          )}
        </section>
      )}

      {tab === "checkins" && (
        <section className="card">
          <h2>Histórico de check-ins</h2>
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
                    <td className="meta">{new Date(c.created_at).toLocaleString()}</td>
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

      {tab === "diario" && (
        <section className="card">
          <h2>Entradas de diario</h2>
          {dossier.diary.length === 0 && <p className="meta">Sin entradas.</p>}
          {dossier.diary.map((d) => (
            <article key={d.id} className="diary-entry">
              <div className="meta">{new Date(d.created_at).toLocaleString()}</div>
              <p style={{ whiteSpace: "pre-wrap" }}>{d.content}</p>
            </article>
          ))}
        </section>
      )}

      {tab === "hechos" && (
        <section className="card">
          <h2>Hechos confirmados (no sobrescribibles por el LLM)</h2>
          {!isTherapist && (
            <p className="info">Solo el terapeuta asignado ve y registra hechos (RBAC).</p>
          )}
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
              <p className="meta">
                Nota: <code>ideation_active</code> / <code>planning</code> → nivel 4;{" "}
                <code>consumption_crisis</code> → nivel 3; el resto no eleva alerta automática.
              </p>
              <button type="submit" disabled={busy}>
                Registrar hecho y reevaluar
              </button>
            </form>
          )}
          <ul className="plain-list">
            {dossier.facts.map((f) => (
              <li key={f.id}>
                <strong>{f.category}</strong> ({f.declared_by}) · {new Date(f.created_at).toLocaleString()}
                <br />
                {f.content}
              </li>
            ))}
            {dossier.facts.length === 0 && <li>Sin hechos activos.</li>}
          </ul>
        </section>
      )}

      {tab === "evaluaciones" && (
        <section className="card">
          <h2>Desglose del motor de riesgo determinista</h2>
          <p className="subtitle">
            Cada evaluación muestra la conclusión, los datos empleados y la ruta de reglas registrada por el
            servidor. Agent 2 aporta inferencias lingüísticas, pero no decide el nivel.
          </p>
          <p className="trace-integrity-note">
            <strong>Lectura clínica:</strong> esta pantalla explica el cálculo persistido; no vuelve a calcularlo
            en el navegador. Abre una evaluación para inspeccionar umbrales, valores observados y evidencia completa.
          </p>
          <RiskAssessmentTraceList assessments={dossier.assessments} />
          {assessmentHasMore && (
            <button type="button" disabled={historyBusy !== null} onClick={loadOlderAssessments}>
              {historyBusy === "assessments" ? "Cargando…" : "Cargar evaluaciones anteriores"}
            </button>
          )}
          <p className="meta">Mostrando {dossier.assessments.length} evaluaciones, de más reciente a más antigua.</p>
        </section>
      )}

      {tab === "agent2" && (
        <section className="card">
          <h2>Tracking de Agent 2 · analizador de conversación</h2>
          <p className="subtitle">
            Entrada textual, respuesta estructurada y metadatos de cada ejecución. Las respuestas son inferencias
            del modelo y deben revisarse junto con los hechos confirmados y el cálculo determinista.
          </p>
          <p className="trace-integrity-note">
            <strong>Cadena de evidencia:</strong> usa el identificador de correlación para vincular una llamada con
            su señal lingüística y la evaluación de riesgo que realmente la consumió.
          </p>
          <Agent2TraceList
            traces={dossier.agent2_traces ?? []}
            assessments={dossier.assessments}
            legacySignalCount={dossier.signals.filter(
              (signal) => signal.signal_type === "linguistic_analysis" && !signal.agent2_trace_id
            ).length}
          />
          {agent2HasMore && (
            <button type="button" disabled={historyBusy !== null} onClick={loadOlderAgent2Traces}>
              {historyBusy === "agent2" ? "Cargando…" : "Cargar trazas anteriores"}
            </button>
          )}
          <p className="meta">Mostrando {dossier.agent2_traces?.length ?? 0} trazas, de más reciente a más antigua.</p>
        </section>
      )}

      {tab === "alertas" && (
        <section className="card">
          <h2>Alertas profesionales generadas por el motor</h2>
          <p className="meta">Solo niveles ≥3 crean alerta. Puedes gestionarlas en Alertas.</p>
          {dossier.alerts.length === 0 && <p>No hay alertas (el seguimiento no depende de ellas).</p>}
          {dossier.alerts.map((a) => (
            <div key={a.id} className={`alert-snippet alert-level-${a.alert_level}`}>
              <strong>
                L{a.alert_level} · {a.status}
              </strong>{" "}
              — {a.title}
              <div className="meta">{new Date(a.created_at).toLocaleString()}</div>
              <p style={{ whiteSpace: "pre-wrap" }}>{a.description}</p>
            </div>
          ))}
          <p>
            <Link to="/professional/alerts">Ir a gestión de alertas →</Link>
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

      {tab === "protocolo" && (
        <section className="card">
          <h2>Documentación de protocolo (servidor, no LLM)</h2>
          <p className="info">{dossier.professional_protocol.notes}</p>
          <h3>Plantilla notificación Nivel 3</h3>
          <pre className="protocol-box">{dossier.professional_protocol.level3}</pre>
          <h3>Plantilla notificación Nivel 4</h3>
          <pre className="protocol-box">{dossier.professional_protocol.level4}</pre>
          <h3>Señales recientes (inferencias)</h3>
          <ul className="plain-list">
            {dossier.signals.slice(0, 15).map((s) => (
              <li key={s.id}>
                <strong>{s.signal_type}</strong> {s.confidence_band || ""} ·{" "}
                {new Date(s.timestamp).toLocaleString()}
                <br />
                <code className="meta">{JSON.stringify(s.value)}</code>
              </li>
            ))}
            {dossier.signals.length === 0 && <li>Sin señales registradas.</li>}
          </ul>
        </section>
      )}
      </div>
    </div>
  );
}
