import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import {
  ASSIGNMENT_STATUS_LABELS,
  BAND_LABELS,
  LEVEL_SHORT_LABELS,
  PatientSummaryOut,
  api,
  formatDateTime,
} from "../api";
import CopilotPanel from "../components/CopilotPanel";

/**
 * Standalone copilot: pick a patient from the menu and talk to Agent 3
 * about them, without first navigating into their full record.
 *
 * The same conversation is also available inside each patient's record;
 * both read and write the same per-(professional, patient) thread.
 */
export default function CopilotPage() {
  const [patients, setPatients] = useState<PatientSummaryOut[]>([]);
  const [selected, setSelected] = useState<string>("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api
      .get<PatientSummaryOut[]>("/api/v1/professional/patients")
      .then((rows) => {
        // Only patients whose record this professional may actually read.
        const readable = rows.filter((row) => row.assignment_status !== "pending");
        setPatients(readable);
        if (readable.length > 0) setSelected(readable[0].id);
      })
      .catch((e) => setError((e as Error).message))
      .finally(() => setLoading(false));
  }, []);

  const patient = useMemo(
    () => patients.find((row) => row.id === selected) || null,
    [patients, selected]
  );

  if (loading) return <div className="loading">Cargando pacientes…</div>;

  return (
    <div className="page">
      <h1>Copiloto clínico</h1>
      <p className="subtitle">
        Elige un paciente y pregunta al copiloto por su situación. Lee su expediente completo —check-ins,
        diario, chat con el asistente, hechos confirmados, señales del Agente 2, evaluaciones y alertas— y
        tiene que citar fecha y fuente en cada afirmación.{" "}
        <Link to="/professional/manual">Cómo funciona esto →</Link>
      </p>

      {error && <p className="error">{error}</p>}

      {patients.length === 0 && !error && (
        <section className="card">
          <p>
            No tienes pacientes con asignación activa o pausada. Solicita acceso desde{" "}
            <Link to="/professional">Mis pacientes</Link>; el paciente debe aceptar la solicitud.
          </p>
        </section>
      )}

      {patients.length > 0 && (
        <>
          <section className="card copilot-picker">
            <label>
              Paciente
              <select value={selected} onChange={(e) => setSelected(e.target.value)}>
                {patients.map((row) => (
                  <option key={row.id} value={row.id}>
                    {row.display_name} ({row.email})
                    {row.pending_alert_level != null
                      ? ` — alerta pendiente N${row.pending_alert_level}`
                      : row.latest_alert_level != null
                        ? ` — última eval. N${row.latest_alert_level}`
                        : ""}
                  </option>
                ))}
              </select>
            </label>
            {patient && (
              <div className="copilot-patient-facts">
                <span>
                  Asignación:{" "}
                  <strong>
                    {ASSIGNMENT_STATUS_LABELS[patient.assignment_status] || patient.assignment_status}
                  </strong>
                </span>
                <span>
                  Nivel operativo:{" "}
                  <strong
                    className={`level-pill level-${patient.pending_alert_level ?? patient.latest_alert_level ?? "na"}`}
                  >
                    {patient.pending_alert_level != null
                      ? `N${patient.pending_alert_level} pendiente${
                          patient.pending_alert_status === "acknowledged" ? " (reconocida)" : ""
                        }`
                      : patient.latest_alert_level != null
                        ? `N${patient.latest_alert_level} · ${LEVEL_SHORT_LABELS[patient.latest_alert_level]}`
                        : "sin evaluación"}
                  </strong>
                  {patient.pending_alert_level != null &&
                    patient.latest_alert_level != null &&
                    patient.latest_alert_level !== patient.pending_alert_level && (
                      <span className="meta"> · última eval. auto. N{patient.latest_alert_level}</span>
                    )}
                </span>
                <span>
                  Score estructural:{" "}
                  <strong>
                    {patient.latest_structural_score != null
                      ? Number(patient.latest_structural_score).toFixed(2)
                      : "—"}
                  </strong>{" "}
                  {patient.latest_confidence_band
                    ? `(${BAND_LABELS[patient.latest_confidence_band] || patient.latest_confidence_band})`
                    : ""}
                </span>
                <span>
                  Alertas abiertas: <strong>{patient.open_alerts}</strong>
                </span>
                <span>Último check-in: {formatDateTime(patient.last_checkin_at)}</span>
                <Link to={`/professional/patients/${patient.id}`}>Abrir historial completo →</Link>
              </div>
            )}
          </section>

          {patient && (
            <section className="card">
              <CopilotPanel patientId={patient.id} patientName={patient.display_name} />
            </section>
          )}
        </>
      )}
    </div>
  );
}
