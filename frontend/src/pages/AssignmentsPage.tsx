import { useEffect, useState } from "react";
import { api, AssignmentOut, ASSIGNMENT_STATUS_LABELS, UserRole } from "../api";
import { useAuth } from "../auth/AuthContext";

export default function AssignmentsPage() {
  const { user } = useAuth();
  const role = (user?.role || "patient") as UserRole;
  const [rows, setRows] = useState<AssignmentOut[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  async function load() {
    setError(null);
    try {
      const data = await api.get<AssignmentOut[]>("/api/v1/assignments/mine");
      setRows(data);
    } catch (e) {
      setError((e as Error).message);
    }
  }

  useEffect(() => {
    load().catch(() => undefined);
  }, []);

  async function act(id: string, action: "accept" | "reject" | "pause" | "resume" | "end") {
    setBusy(id + action);
    setError(null);
    try {
      await api.post(`/api/v1/assignments/${id}/${action}`);
      await load();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  }

  const isPatient = role === "patient";
  const isClinicalAdmin = role === "admin_clinical";
  const isSupervisor = role === "supervisor";

  return (
    <div className="page">
      <h1>{isPatient ? "Vinculación con profesionales" : "Asignaciones paciente–profesional"}</h1>
      <p className="subtitle">
        {isPatient
          ? "Debes aceptar explícitamente cada solicitud. Al aceptar, activas el consentimiento de compartir con ese profesional."
          : isClinicalAdmin || isSupervisor
            ? "Visión global del ciclo de vida de asignaciones (pending → active → paused/ended/rejected)."
            : "Solicitudes y vínculos con tus pacientes."}
      </p>

      {error && <p className="error">{error}</p>}

      {rows.length === 0 && <p className="info">No hay asignaciones todavía.</p>}

      <div className="stack">
        {rows.map((a) => (
          <article key={a.id} className="card">
            <h2>
              {isPatient
                ? a.professional_display_name || a.professional_email || "Profesional"
                : a.patient_display_name || a.patient_email || "Paciente"}
            </h2>
            <p className="meta">
              Estado: <strong>{ASSIGNMENT_STATUS_LABELS[a.status] || a.status}</strong>
              {" · "}
              Solicitada: {new Date(a.requested_at).toLocaleString()}
            </p>
            {!isPatient && (
              <p className="meta">
                Profesional: {a.professional_display_name} ({a.professional_email})
              </p>
            )}
            {isPatient && (
              <p className="meta">
                Profesional: {a.professional_display_name} · {a.professional_email}
              </p>
            )}

            <div className="alert-actions">
              {isPatient && a.status === "pending" && (
                <>
                  <button disabled={!!busy} onClick={() => act(a.id, "accept")}>
                    Aceptar
                  </button>
                  <button className="btn-secondary" disabled={!!busy} onClick={() => act(a.id, "reject")}>
                    Rechazar
                  </button>
                </>
              )}
              {a.status === "active" && (
                <>
                  <button className="btn-secondary" disabled={!!busy} onClick={() => act(a.id, "pause")}>
                    Pausar
                  </button>
                  <button className="btn-danger" disabled={!!busy} onClick={() => act(a.id, "end")}>
                    Finalizar
                  </button>
                </>
              )}
              {a.status === "paused" && (
                <>
                  <button disabled={!!busy} onClick={() => act(a.id, "resume")}>
                    Reanudar
                  </button>
                  <button className="btn-danger" disabled={!!busy} onClick={() => act(a.id, "end")}>
                    Finalizar
                  </button>
                </>
              )}
            </div>
          </article>
        ))}
      </div>
    </div>
  );
}
