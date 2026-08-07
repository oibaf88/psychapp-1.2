import { FormEvent, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api";

interface DiaryEntry {
  id: string;
  content: string;
  created_at: string;
}

export default function DiaryPage() {
  const [entries, setEntries] = useState<DiaryEntry[]>([]);
  const [content, setContent] = useState("");
  const [busy, setBusy] = useState(false);
  const navigate = useNavigate();

  async function load() {
    setEntries(await api.get<DiaryEntry[]>("/api/v1/diary"));
  }

  useEffect(() => {
    load().catch(() => undefined);
  }, []);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    if (!content.trim()) return;
    setBusy(true);
    try {
      const res = await api.post<{ entry: DiaryEntry; ui_mode: string }>("/api/v1/diary", { content });
      setContent("");
      await load();
      if (res.ui_mode === "crisis") {
        navigate("/chat"); // route through the chat, which will render the crisis screen
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="page">
      <h1>Tu diario</h1>
      <p className="subtitle">Un espacio privado para escribir cómo estás. Se analiza para ayudarte, nunca para juzgarte.</p>
      <form onSubmit={onSubmit} className="diary-form">
        <textarea
          value={content}
          onChange={(e) => setContent(e.target.value)}
          rows={5}
          placeholder="¿Cómo ha ido tu día?"
        />
        <button type="submit" disabled={busy || !content.trim()}>
          {busy ? "Guardando..." : "Guardar entrada"}
        </button>
      </form>

      <section className="entries">
        {entries.map((e) => (
          <article key={e.id} className="card">
            <time>{new Date(e.created_at).toLocaleString()}</time>
            <p>{e.content}</p>
          </article>
        ))}
      </section>
    </div>
  );
}
