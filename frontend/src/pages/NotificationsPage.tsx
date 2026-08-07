import { useEffect, useState } from "react";
import { api, NotificationOut } from "../api";

export default function NotificationsPage() {
  const [items, setItems] = useState<NotificationOut[]>([]);
  const [error, setError] = useState<string | null>(null);

  async function load() {
    setItems(await api.get<NotificationOut[]>("/api/v1/notifications?limit=50"));
  }

  useEffect(() => {
    load().catch((e) => setError((e as Error).message));
  }, []);

  async function markRead(id: string) {
    await api.post(`/api/v1/notifications/${id}/read`);
    await load();
  }

  return (
    <div className="page">
      <h1>Notificaciones</h1>
      <p className="subtitle">Alertas in-app (y email si SMTP está configurado). Nivel 3/4 siempre generan aviso interno.</p>
      {error && <p className="error">{error}</p>}
      {items.length === 0 && <p className="info">No hay notificaciones.</p>}
      {items.map((n) => (
        <article key={n.id} className={`card ${n.alert_level ? `alert-level-${n.alert_level}` : ""}`}>
          <h2>{n.title || "Aviso"}</h2>
          <p style={{ whiteSpace: "pre-wrap" }}>{n.body}</p>
          <p className="meta">
            {n.status}
            {n.alert_level != null ? ` · nivel ${n.alert_level}` : ""} · {new Date(n.created_at).toLocaleString()}
          </p>
          {n.status !== "read" && (
            <button className="btn-secondary" onClick={() => markRead(n.id)}>
              Marcar como leída
            </button>
          )}
        </article>
      ))}
    </div>
  );
}
