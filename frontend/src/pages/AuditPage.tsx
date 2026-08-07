import { useEffect, useState } from "react";
import { api, AuditLogOut } from "../api";

export default function AuditPage() {
  const [rows, setRows] = useState<AuditLogOut[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .get<AuditLogOut[]>("/api/v1/audit?limit=150")
      .then(setRows)
      .catch((e) => setError((e as Error).message));
  }, []);

  return (
    <div className="page">
      <h1>Auditoría clínica</h1>
      <p className="subtitle">
        Solo supervisor y admin clínico. Trazas de asignaciones, consentimientos, hechos y gestión de alertas.
      </p>
      {error && <p className="error">{error}</p>}
      <div className="table-wrap">
        <table className="table">
          <thead>
            <tr>
              <th>Fecha</th>
              <th>Acción</th>
              <th>Rol</th>
              <th>Entidad</th>
              <th>Detalle</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.id}>
                <td className="meta">{new Date(r.created_at).toLocaleString()}</td>
                <td>
                  <code>{r.action}</code>
                </td>
                <td>{r.actor_role || "—"}</td>
                <td>
                  {r.entity_type || "—"}
                  {r.entity_id ? ` · ${String(r.entity_id).slice(0, 8)}…` : ""}
                </td>
                <td className="meta">{r.extra ? JSON.stringify(r.extra) : "—"}</td>
              </tr>
            ))}
            {rows.length === 0 && (
              <tr>
                <td colSpan={5}>Sin eventos de auditoría aún.</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
