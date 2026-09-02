import { FormEvent, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { api, AssignmentOut, CheckInIn, PatientTimelineOut, formatDay } from "../api";

const emptyForm: CheckInIn = { mood: 5, craving: 3, sleep_hours: 7, self_efficacy: 5, notes: "" };

export default function PatientDashboard() {
  const [timeline, setTimeline] = useState<PatientTimelineOut | null>(null);
  const [form, setForm] = useState<CheckInIn>(emptyForm);
  const [submitting, setSubmitting] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [pendingLinks, setPendingLinks] = useState<AssignmentOut[]>([]);

  async function loadTimeline() {
    const data = await api.get<PatientTimelineOut>("/api/v1/timeline?window_days=30");
    setTimeline(data);
  }

  useEffect(() => {
    loadTimeline().catch(() => setMessage("No se pudo cargar tu historial."));
    api
      .get<AssignmentOut[]>("/api/v1/assignments/mine")
      .then((rows) => setPendingLinks(rows.filter((r) => r.status === "pending")))
      .catch(() => undefined);
  }, []);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    setMessage(null);
    try {
      await api.post("/api/v1/checkins", form);
      setMessage("Check-in registrado. Gracias por dedicarte este momento.");
      setForm(emptyForm);
      await loadTimeline();
    } catch (err) {
      setMessage((err as Error).message);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="page">
      <h1>Tu acompañamiento</h1>

      {pendingLinks.length > 0 && (
        <section className="card alert-level-3">
          <h2>Solicitudes de vinculación pendientes</h2>
          <p>
            Tienes {pendingLinks.length} profesional(es) pidiendo acceso a tu seguimiento. Debes aceptar o rechazar
            desde <Link to="/assignments">Vinculaciones</Link>.
          </p>
        </section>
      )}

      <section className="card">
        <h2>Check-in de hoy</h2>
        <form onSubmit={onSubmit} className="checkin-form">
          <label>
            Estado de ánimo (0-10): {form.mood}
            <input
              type="range"
              min={0}
              max={10}
              value={form.mood}
              onChange={(e) => setForm({ ...form, mood: Number(e.target.value) })}
            />
          </label>
          <label>
            Craving / deseo de consumo (0-10): {form.craving}
            <input
              type="range"
              min={0}
              max={10}
              value={form.craving}
              onChange={(e) => setForm({ ...form, craving: Number(e.target.value) })}
            />
          </label>
          <label>
            Horas de sueño anoche
            <input
              type="number"
              step="0.5"
              min={0}
              max={24}
              value={form.sleep_hours}
              onChange={(e) => setForm({ ...form, sleep_hours: Number(e.target.value) })}
            />
          </label>
          <label>
            Confianza en poder manejar la situación de hoy (0-10): {form.self_efficacy}
            <input
              type="range"
              min={0}
              max={10}
              value={form.self_efficacy}
              onChange={(e) => setForm({ ...form, self_efficacy: Number(e.target.value) })}
            />
          </label>
          <label>
            Notas (opcional)
            <textarea
              value={form.notes}
              onChange={(e) => setForm({ ...form, notes: e.target.value })}
              placeholder="¿Algo que quieras registrar hoy?"
            />
          </label>
          <button type="submit" disabled={submitting}>
            {submitting ? "Guardando..." : "Guardar check-in"}
          </button>
        </form>
        {message && <p className="info">{message}</p>}
      </section>

      <section className="card">
        <h2>Tu tendencia (últimos 30 días)</h2>
        {timeline && timeline.points.length > 0 ? (
          <>
            <ResponsiveContainer width="100%" height={320}>
              <LineChart data={timeline.points} margin={{ top: 8, right: 8, bottom: 4, left: -16 }}>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis dataKey="date" tick={{ fontSize: 11 }} tickFormatter={formatDay} minTickGap={24} />
                <YAxis yAxisId="left" domain={[0, 10]} tick={{ fontSize: 11 }} />
                <YAxis yAxisId="sleep" orientation="right" domain={[0, 24]} tick={{ fontSize: 11 }} tickFormatter={(value: number) => `${value} h`} />
                <Tooltip labelFormatter={formatDay} />
                <Legend />
                <Line yAxisId="left" type="monotone" dataKey="mood" name="Ánimo" stroke="#3987e5" strokeWidth={2} connectNulls={false} dot={{ r: 2 }} />
                <Line yAxisId="left" type="monotone" dataKey="craving" name="Craving" stroke="#d95926" strokeWidth={2} connectNulls={false} dot={{ r: 2 }} />
                <Line yAxisId="left" type="monotone" dataKey="self_efficacy" name="Autoeficacia" stroke="#c98500" strokeWidth={2} connectNulls={false} dot={{ r: 2 }} />
                <Line yAxisId="sleep" type="monotone" dataKey="sleep_hours" name="Sueño (h)" stroke="#199e70" strokeWidth={2} strokeDasharray="5 3" connectNulls={false} dot={{ r: 2 }} />
              </LineChart>
            </ResponsiveContainer>
            <p className="meta">Ánimo, craving y autoeficacia: 0–10. Sueño: horas, en el eje derecho.</p>
          </>
        ) : (
          <p>Sin datos todavía.</p>
        )}
      </section>
    </div>
  );
}
