import { FormEvent, useEffect, useRef, useState } from "react";
import { api, ChatMessageOut, ChatOut } from "../api";

export default function ChatPage() {
  const [messages, setMessages] = useState<ChatMessageOut[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [crisisMode, setCrisisMode] = useState(false);
  const [resources, setResources] = useState<ChatOut["resources"]>(undefined);
  const bottomRef = useRef<HTMLDivElement>(null);

  async function load() {
    const history = await api.get<ChatMessageOut[]>("/api/v1/chat/history");
    setMessages(history);
    if (history.some((m) => m.ui_mode === "crisis")) {
      // keep crisis banner visible if the last relevant message was a crisis one
      const last = [...history].reverse().find((m) => m.role === "assistant");
      if (last?.ui_mode === "crisis") setCrisisMode(true);
    }
  }

  useEffect(() => {
    load().catch(() => undefined);
  }, []);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    if (!input.trim() || busy) return;
    const text = input;
    setInput("");
    setBusy(true);
    setMessages((prev) => [
      ...prev,
      { id: `local-${Date.now()}`, role: "user", content: text, ui_mode: null, created_at: new Date().toISOString() },
    ]);
    try {
      const res = await api.post<ChatOut>("/api/v1/chat", { message: text });
      setMessages((prev) => [
        ...prev,
        { id: `local-${Date.now()}-r`, role: "assistant", content: res.reply, ui_mode: res.ui_mode, created_at: new Date().toISOString() },
      ]);
      if (res.ui_mode === "crisis") {
        setCrisisMode(true);
        setResources(res.resources);
      }
    } catch (err) {
      setMessages((prev) => [
        ...prev,
        {
          id: `local-${Date.now()}-e`,
          role: "assistant",
          content: `No se pudo obtener respuesta: ${(err as Error).message}`,
          ui_mode: null,
          created_at: new Date().toISOString(),
        },
      ]);
    } finally {
      setBusy(false);
    }
  }

  if (crisisMode) {
    return (
      <div className="crisis-fullscreen">
        <h1>Estamos contigo</h1>
        <p>Detectamos que puedes estar pasando por un momento de mucho sufrimiento.</p>
        <p>Por favor, contacta ahora mismo con ayuda profesional:</p>
        <div className="crisis-fullscreen-actions">
          <a href="tel:024" className="crisis-cta">
            📞 Llamar a la Línea 024
          </a>
          <a href="tel:112" className="crisis-cta">
            🚑 Llamar al 112
          </a>
        </div>
        <p>No estás solo. Puedes quedarte en esta pantalla mientras contactas con ayuda.</p>
        {resources && resources.length > 0 && (
          <div className="crisis-resources">
            <h2>Otros recursos</h2>
            <ul>
              {resources
                .filter((r) => r.name !== "Línea 024" && r.name !== "112")
                .map((r) => (
                  <li key={r.name}>
                    <strong>{r.name}</strong>: {r.description}
                  </li>
                ))}
            </ul>
          </div>
        )}
        <button className="crisis-continue" onClick={() => setCrisisMode(false)}>
          He contactado con ayuda / quiero seguir hablando
        </button>
      </div>
    );
  }

  return (
    <div className="page chat-page">
      <h1>Chat de acompañamiento</h1>
      <p className="subtitle">
        Este chat es un apoyo, no un terapeuta ni un diagnóstico. Si estás en peligro inmediato, usa el botón de
        emergencia (024 / 112) en cualquier momento.
      </p>
      <div className="chat-window">
        {messages.map((m) => (
          <div key={m.id} className={`chat-bubble chat-${m.role}`}>
            {m.content}
          </div>
        ))}
        <div ref={bottomRef} />
      </div>
      <form onSubmit={onSubmit} className="chat-input-row">
        <input value={input} onChange={(e) => setInput(e.target.value)} placeholder="Escribe un mensaje..." disabled={busy} />
        <button type="submit" disabled={busy || !input.trim()}>
          {busy ? "..." : "Enviar"}
        </button>
      </form>
    </div>
  );
}
