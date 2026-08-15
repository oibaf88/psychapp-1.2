import { FormEvent, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import {
  api,
  ASSIGNMENT_STATUS_LABELS,
  BAND_LABELS,
  PatientSummaryOut,
  ROLE_LABELS,
  UserRole,
} from "../api";
import { useAuth } from "../auth/AuthContext";

export default function ProfessionalDashboard() {
  const { user } = useAuth();
  const role = (user?.role || "therapist") as UserRole;
  const [patients, setPatients] = useState<PatientSummaryOut[]>([]);
  const [patientEmail, setPatientEmail] = useState("");
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const canRequestAccess = role === "therapist" || role === "supervisor";
  const canSeeClinicalColumns = role !== "admin_clinical";
  const isAdmin = role === "admin_clinical";
  const isSupervisor = role === "supervisor";

  async function load() {
    setPatients(await api.get<PatientSummaryOut[]>("/api/v1/professional/patients"));
  }

  useEffect(() => {
    load().catch((e) => setError((e as Error).message));
  }, []);

  async function requestAssignment(e: FormEvent) {
    e.preventDefault();
    setMessage(null);
    setError(null);
    try {
      await api.post("/api/v1/assignments/request", { patient_email: patientEmail });
      setMessage("Solicitud enviada. El paciente debe aceptarla desde Vinculaciones.");
      setPatientEmail("");
      await load();
    } catch (err) {
      setError((err as Error).message);
    }
  }

  const title =
    role === "therapist"
      ? "Mis pacientes"
      : role === "supervisor"
        ? "Panel de supervisión — pacientes"
        : "Administración clínica — roster de pacientes";

  return (
    <div className="page">
      <h1>{title}</h1>
      <p className="subtitle">
        Rol: <strong>{ROLE_LABELS[role]}</strong>
        {role === "therapist" &&
          " · Abre la ficha de cualquier paciente con asignación activa/pausada para ver el historial completo (check-ins, diario, hechos, evaluaciones). No hace falta que haya alerta."}
        {isSupervisor && " · Visibilidad de roster y alertas; puede finalizar asignaciones."}
        {isAdmin &&
          " · Sin visibilidad de señales clínicas ni gestión de alertas (RBAC). Gestiona asignaciones y auditoría."}
      </p>

      {canRequestAccess && (
        <section className="card">
          <h2>Solicitar acceso a un paciente</h2>
          <p className="meta">El paciente debe aceptar la solicitud (consentimiento professional_sharing).</p>
          <form onSubmit={requestAssignment} className="inline-form">
            <input
              type="email"
              value={patientEmail}
              onChange={(e) => setPatientEmail(e.target.value)}
              placeholder="email del paciente"
              required
            />
            <button type="submit">Solicitar</button>
          </form>
          {message && <p className="info">{message}</p>}
        </section>
      )}

      {error && <p className="error">{error}</p>}

      <div className="table-wrap">
        <table className="table">
          <thead>
            <tr>
              <th>Paciente</th>
              <th>Email</th>
              <th>Asignación</th>
              {canSeeClinicalColumns && <th>Nivel riesgo actual</th>}
              {canSeeClinicalColumns && (
                <th title="Similitud de sus check-ins de 7 días con su línea base de 21 días. 1.00 = sin cambios. NO es una escala de riesgo.">
                  Estabilidad de check-ins ⓘ
                </th>
              )}
              {canSeeClinicalColumns && <th>Check-ins</th>}
              {canSeeClinicalColumns && <th>Alertas abiertas</th>}
              <th></th>
            </tr>
          </thead>
          <tbody>
            {patients.map((p) => (
              <tr key={p.id}>
                <td>{p.display_name}</td>
                <td>{p.email}</td>
                <td>{ASSIGNMENT_STATUS_LABELS[p.assignment_status] || p.assignment_status}</td>
                {canSeeClinicalColumns && (
                  <td>
                    {p.latest_alert_level != null ? (
                      <strong className={`level-pill level-${p.latest_alert_level}`}>L{p.latest_alert_level}</strong>
                    ) : (
                      "—"
                    )}
                  </td>
                )}
                {canSeeClinicalColumns && (
                  <td className="meta">
                    {BAND_LABELS[p.latest_confidence_band || ""] || p.latest_confidence_band || "—"}
                    {p.latest_structural_score != null
                      ? ` · ${Number(p.latest_structural_score).toFixed(2)}`
                      : ""}
                  </td>
                )}
                {canSeeClinicalColumns && <td>{p.checkin_count ?? "—"}</td>}
                {canSeeClinicalColumns && <td>{p.open_alerts}</td>}
                <td>
                  {role === "admin_clinical" ? (
                    <Link to="/professional/assignments">Asignaciones</Link>
                  ) : p.assignment_status === "pending" && role === "therapist" ? (
                    <span className="meta">Esperando aceptación</span>
                  ) : (
                    <Link to={`/professional/patients/${p.id}`}>Ver historial</Link>
                  )}
                </td>
              </tr>
            ))}
            {patients.length === 0 && (
              <tr>
                <td colSpan={canSeeClinicalColumns ? 8 : 4}>
                  {role === "therapist"
                    ? "Aún no tienes pacientes. Solicita acceso por email."
                    : "No hay pacientes en el sistema."}
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
