import { FormEvent, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis, Legend } from "recharts";
import { api, FACT_CATEGORIES, PatientDossierOut } from "../api";
import { useAuth } from "../auth/AuthContext";

type Tab = "resumen" | "checkins" | "diario" | "hechos" | "evaluaciones" | "alertas" | "plan" | "protocolo";

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

  const isTherapist = user?.role === "therapist";

  async function load() {
    if (!patientId) return;
    setError(null);
    try {
      const data = await api.get<PatientDossierOut>(
        `/api/v1/professional/patients/${patientId}/dossier?window_days=30`
      );
      setDossier(data);
    } catch (e) {
      setError((e as Error).message);
      setDossier(null);
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
    { id: "evaluaciones", label: "Evaluaciones" },
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

      <div className="tabs">
        {tabs.map((t) => (
          <button
            key={t.id}
            type="button"
            className={tab === t.id ? "tab active" : "tab"}
            onClick={() => setTab(t.id)}
          >
            {t.label}
          </button>
        ))}
      </div>

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
          <h2>Histórico del motor de riesgo</h2>
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>Fecha</th>
                  <th>Nivel</th>
                  <th>Motivo</th>
                  <th>Reglas</th>
                  <th>Modelo</th>
                </tr>
              </thead>
              <tbody>
                {dossier.assessments.map((a) => (
                  <tr key={a.id}>
                    <td className="meta">{new Date(a.calculated_at).toLocaleString()}</td>
                    <td>
                      <strong>L{a.alert_level}</strong>
                    </td>
                    <td>{a.assessment_reason}</td>
                    <td className="meta">
                      {Array.isArray(a.triggering_rules)
                        ? a.triggering_rules.join(", ")
                        : JSON.stringify(a.triggering_rules)}
                    </td>
                    <td className="meta">{a.model_version}</td>
                  </tr>
                ))}
                {dossier.assessments.length === 0 && (
                  <tr>
                    <td colSpan={5}>Sin evaluaciones. Usa «Reevaluar riesgo».</td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
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
  );
}
