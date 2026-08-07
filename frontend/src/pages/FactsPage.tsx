import { FormEvent, useEffect, useState } from "react";
import { api, FactOut, FACT_CATEGORIES } from "../api";

export default function FactsPage() {
  const [facts, setFacts] = useState<FactOut[]>([]);
  const [category, setCategory] = useState(FACT_CATEGORIES[0].value);
  const [content, setContent] = useState("");
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function load() {
    setFacts(await api.get<FactOut[]>("/api/v1/facts"));
  }

  useEffect(() => {
    load().catch((e) => setError((e as Error).message));
  }, []);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setMessage(null);
    setError(null);
    try {
      await api.post("/api/v1/facts", { category, content });
      setContent("");
      setMessage(
        "Hecho registrado. Los hechos confirmados no los reescribe el LLM; el motor de riesgo se reevalúa de inmediato."
      );
      await load();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="page">
      <h1>Hechos confirmados</h1>
      <p className="subtitle">
        Muro de hechos vs inferencias: solo lo que tú (o tu profesional) confirmáis explícitamente es un hecho. Las
        señales del sistema son inferencias separadas.
      </p>

      <section className="card">
        <h2>Declarar un hecho</h2>
        <form onSubmit={onSubmit} className="stack-form">
          <label>
            Categoría
            <select value={category} onChange={(e) => setCategory(e.target.value)}>
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
              value={content}
              onChange={(e) => setContent(e.target.value)}
              required
              rows={3}
              placeholder="Describe el hecho de forma concreta…"
            />
          </label>
          <button type="submit" disabled={busy}>
            {busy ? "Guardando…" : "Registrar hecho"}
          </button>
        </form>
        {message && <p className="info">{message}</p>}
        {error && <p className="error">{error}</p>}
      </section>

      <section className="card">
        <h2>Tus hechos activos</h2>
        <ul className="plain-list">
          {facts.map((f) => (
            <li key={f.id}>
              <strong>{f.category}</strong> ({f.declared_by}) · {new Date(f.created_at).toLocaleString()}
              <br />
              {f.content}
            </li>
          ))}
          {facts.length === 0 && <li>Aún no has declarado hechos.</li>}
        </ul>
      </section>
    </div>
  );
}
