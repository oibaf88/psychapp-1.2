import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, AlertOut, formatDateTime } from "../api";
import { useAuth } from "../auth/AuthContext";

export default function AlertsPage() {
  const { user } = useAuth();
  const [alerts, setAlerts] = useState<AlertOut[]>([]);
  const [status, setStatus] = useState("open");
  const [notes, setNotes] = useState<Record<string, string>>({});
  const [dismiss, setDismiss] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);

  const canManage = user?.role === "therapist" || user?.role === "supervisor";

  async function load() {
    setError(null);
    try {
      const q = status ? `?status=${encodeURIComponent(status)}` : "";
      setAlerts(await api.get<AlertOut[]>(`/api/v1/professional/alerts${q}`));
    } catch (e) {
      setError((e as Error).message);
      setAlerts([]);
    }
  }

  useEffect(() => {
    load().catch(() => undefined);
  }, [status]);

  async function acknowledge(id: string) {
    await api.post(`/api/v1/professional/alerts/${id}/acknowledge`);
    await load();
  }

  async function resolve(id: string) {
    await api.post(`/api/v1/professional/alerts/${id}/resolve`, {
      resolution_notes: notes[id] || "Revisado en consulta.",
    });
    await load();
  }

  async function dismissAlert(id: string) {
    await api.post(`/api/v1/professional/alerts/${id}/dismiss`, {
      dismiss_reason: dismiss[id] || "",
    });
    await load();
  }

  if (user?.role === "admin_clinical") {
    return (
      <div className="page">
        <h1>Alertas clínicas</h1>
        <p className="info">
          El rol <strong>admin_clinical</strong> no gestiona alertas clínicas (RBAC). Usa{" "}
          <Link to="/professional/assignments">Asignaciones</Link> y{" "}
          <Link to="/professional/audit">Auditoría</Link>.
        </p>
      </div>
    );
  }

  return (
    <div className="page">
      <h1>Alertas profesionales</h1>
      <p className="subtitle">
        Niveles 3–4 escalan a profesionales asignados. Gestión: reconocer → resolver o descartar (nivel 4 exige
        justificación).
      </p>

      <div className="filters card">
        <label>
          Estado
          <select value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="open">Abiertas</option>
            <option value="acknowledged">Reconocidas</option>
            <option value="resolved">Resueltas</option>
            <option value="dismissed">Descartadas</option>
            <option value="">Todas</option>
          </select>
        </label>
        <button className="btn-secondary" type="button" onClick={() => load()}>
          Actualizar
        </button>
      </div>

      {error && <p className="error">{error}</p>}
      {alerts.length === 0 && !error && <p>No hay alertas con ese filtro.</p>}

      {alerts.map((a) => (
        <article key={a.id} className={`card alert-level-${a.alert_level}`}>
          <h2>
            {a.alert_level >= 4 ? "🔴" : a.alert_level >= 3 ? "🟠" : "🟡"} Nivel {a.alert_level}: {a.title}
          </h2>
          <p>
            Paciente:{" "}
            <Link to={`/professional/patients/${a.user_id}`}>
              {a.patient_display_name || a.patient_email || a.user_id}
            </Link>
          </p>
          {/* Why it fired, in the therapist's terms, before the raw description. */}
          {a.driver_family_label && (
            <p className="alert-driver">
              <strong>Disparada por:</strong> {a.driver_family_label}
              {a.rule_title ? ` — ${a.rule_title}` : ""}
            </p>
          )}
          {a.plain_explanation && <p>{a.plain_explanation}</p>}

          {/* The sentence or declaration behind the alert. An alert without
              its source text cannot be triaged or dismissed responsibly. */}
          {a.evidence?.text && (
            <blockquote className="evidence-quote">
              <span className="meta">
                {a.evidence.source_label} · {formatDateTime(a.evidence.created_at)}
                {a.evidence.category ? ` · ${a.evidence.category}` : ""}
                {a.evidence.declared_by ? ` · declarado por ${a.evidence.declared_by}` : ""}
              </span>
              <br />
              {a.evidence.text}
            </blockquote>
          )}
          {!a.evidence?.text &&
            (a.driver_family === "desviacion_estructural" || a.driver_family === "convergencia") && (
              <p className="meta">
                Origen: evolución de los check-ins. Abre la ficha del paciente para ver las gráficas.
              </p>
            )}

          {a.what_now && (
            <p>
              <strong>Qué hacer:</strong> {a.what_now}
            </p>
          )}

          <details className="alert-raw">
            <summary>Descripción original del motor</summary>
            <p style={{ whiteSpace: "pre-wrap" }}>{a.description}</p>
          </details>

          <p className="meta">
            Estado: {a.status} · {new Date(a.created_at).toLocaleString()}
            {a.acknowledged_at && ` · reconocida ${new Date(a.acknowledged_at).toLocaleString()}`}
            {a.resolved_at && ` · resuelta ${new Date(a.resolved_at).toLocaleString()}`}
          </p>
          {a.resolution_notes && <p className="meta">Notas: {a.resolution_notes}</p>}
          {a.dismiss_reason && <p className="meta">Descarte: {a.dismiss_reason}</p>}

          {canManage && (a.status === "open" || a.status === "acknowledged") && (
            <div className="alert-actions stack-actions">
              {a.status === "open" && <button onClick={() => acknowledge(a.id)}>Marcar como recibida</button>}
              <input
                placeholder="Notas de resolución"
                value={notes[a.id] || ""}
                onChange={(e) => setNotes({ ...notes, [a.id]: e.target.value })}
              />
              <button onClick={() => resolve(a.id)}>Resolver</button>
              <input
                placeholder={a.alert_level === 4 ? "Justificación de descarte (obligatoria L4)" : "Motivo de descarte"}
                value={dismiss[a.id] || ""}
                onChange={(e) => setDismiss({ ...dismiss, [a.id]: e.target.value })}
              />
              <button className="btn-secondary" onClick={() => dismissAlert(a.id)}>
                Descartar
              </button>
            </div>
          )}
        </article>
      ))}
    </div>
  );
}
