import { useEffect, useMemo, useState } from "react";
import { api, AuditLogOut } from "../api";

interface AuditLogPageOut {
  items: AuditLogOut[];
  total: number;
  limit: number;
  offset: number;
}

const PAGE_GROUP_SIZE = 10;

export default function AuditPage() {
  const [rows, setRows] = useState<AuditLogOut[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(0);
  const [pageSize, setPageSize] = useState(50);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setLoading(true);
    setError(null);
    api
      .get<AuditLogPageOut>(`/api/v1/audit/page?limit=${pageSize}&offset=${page * pageSize}`)
      .then((result) => {
        setRows(result.items);
        setTotal(result.total);
      })
      .catch((e) => setError((e as Error).message))
      .finally(() => setLoading(false));
  }, [page, pageSize]);

  const pageCount = Math.max(1, Math.ceil(total / pageSize));
  const groupStart = Math.floor(page / PAGE_GROUP_SIZE) * PAGE_GROUP_SIZE;
  const groupEnd = Math.min(pageCount, groupStart + PAGE_GROUP_SIZE);
  const pageTabs = useMemo(
    () => Array.from({ length: Math.max(0, groupEnd - groupStart) }, (_, index) => groupStart + index),
    [groupStart, groupEnd],
  );

  const changePageSize = (value: number) => {
    setPageSize(value);
    setPage(0);
  };

  return (
    <div className="page">
      <h1>Auditoría clínica</h1>
      <p className="subtitle">
        Solo supervisor y admin clínico. Histórico completo de asignaciones, consentimientos, hechos y gestión de alertas.
      </p>

      <div className="alert-actions" style={{ alignItems: "center", flexWrap: "wrap", gap: "0.5rem" }}>
        <span className="meta">
          {total.toLocaleString()} eventos · página {Math.min(page + 1, pageCount)} de {pageCount}
        </span>
        <label className="meta">
          Filas por página{" "}
          <select value={pageSize} onChange={(e) => changePageSize(Number(e.target.value))}>
            <option value={25}>25</option>
            <option value={50}>50</option>
            <option value={100}>100</option>
          </select>
        </label>
      </div>

      {error && <p className="error">{error}</p>}
      {loading && <p className="meta">Cargando…</p>}

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
            {!loading && rows.length === 0 && (
              <tr>
                <td colSpan={5}>Sin eventos de auditoría aún.</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {pageCount > 1 && (
        <nav className="alert-actions" aria-label="Páginas del histórico" style={{ flexWrap: "wrap", gap: "0.4rem" }}>
          <button className="btn-secondary" type="button" disabled={page === 0} onClick={() => setPage(page - 1)}>
            Anterior
          </button>
          {groupStart > 0 && (
            <button className="btn-secondary" type="button" onClick={() => setPage(Math.max(0, groupStart - PAGE_GROUP_SIZE))}>
              … {groupStart}
            </button>
          )}
          {pageTabs.map((pageIndex) => (
            <button
              key={pageIndex}
              className={pageIndex === page ? "" : "btn-secondary"}
              type="button"
              aria-current={pageIndex === page ? "page" : undefined}
              onClick={() => setPage(pageIndex)}
            >
              {pageIndex + 1}
            </button>
          ))}
          {groupEnd < pageCount && (
            <button className="btn-secondary" type="button" onClick={() => setPage(groupEnd)}>
              {groupEnd + 1} …
            </button>
          )}
          <button
            className="btn-secondary"
            type="button"
            disabled={page >= pageCount - 1}
            onClick={() => setPage(page + 1)}
          >
            Siguiente
          </button>
        </nav>
      )}
    </div>
  );
}
