import { FormEvent, useEffect, useState } from "react";
import { getApiBase, setApiBase } from "../api";

export default function SettingsPage() {
  const [apiBase, setApiBaseField] = useState(getApiBase());
  const [status, setStatus] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    setApiBaseField(getApiBase());
  }, []);

  async function testConnection() {
    setBusy(true);
    setStatus(null);
    try {
      const base = apiBase.trim().replace(/\/$/, "");
      const url = `${base}/api/v1/health`;
      const res = await fetch(url, { method: "GET" });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const body = await res.json();
      setStatus(`Conexión OK: ${JSON.stringify(body)}`);
    } catch (e) {
      setStatus(`No se pudo conectar: ${(e as Error).message}. ¿El PC está en la misma Wi‑Fi y Docker en marcha?`);
    } finally {
      setBusy(false);
    }
  }

  function onSave(e: FormEvent) {
    e.preventDefault();
    setApiBase(apiBase);
    setStatus("Guardado. La app usará este servidor en las siguientes peticiones.");
  }

  function useSameOrigin() {
    setApiBaseField("");
    setApiBase(null);
    setStatus("Usando origen actual (mismo host que la web). Ideal en el navegador del PC.");
  }

  function suggestLan() {
    // Hint only — user must paste their PC IP from ipconfig
    const hint = "http://192.168.1.213:5173";
    setApiBaseField(hint);
  }

  return (
    <div className="page">
      <h1>Ajustes / servidor</h1>
      <p className="subtitle">
        En el móvil instalado, la app necesita la dirección de tu PC en la red Wi‑Fi (donde corre Docker). Ejemplo:{" "}
        <code>http://192.168.1.213:5173</code>
      </p>

      <section className="card">
        <h2>URL del servidor PsychApp</h2>
        <form onSubmit={onSave} className="stack-form">
          <label>
            Base URL (sin barra final)
            <input
              value={apiBase}
              onChange={(e) => setApiBaseField(e.target.value)}
              placeholder="http://192.168.x.x:5173  o vacío = mismo origen"
              autoCapitalize="none"
              autoCorrect="off"
            />
          </label>
          <div className="alert-actions">
            <button type="submit">Guardar</button>
            <button type="button" className="btn-secondary" disabled={busy} onClick={testConnection}>
              Probar conexión
            </button>
            <button type="button" className="btn-secondary" onClick={useSameOrigin}>
              Mismo origen
            </button>
            <button type="button" className="btn-secondary" onClick={suggestLan}>
              Ejemplo Wi‑Fi
            </button>
          </div>
        </form>
        {status && <p className="info">{status}</p>}
      </section>

      <section className="card">
        <h2>Instalar en el móvil</h2>
        <ol className="plain-list" style={{ listStyle: "decimal", paddingLeft: "1.2rem" }}>
          <li>En el PC: <code>docker compose up -d</code> (PsychApp en marcha).</li>
          <li>Móvil y PC en la <strong>misma Wi‑Fi</strong>.</li>
          <li>
            Abre en el móvil: <code>http://&lt;IP-del-PC&gt;:5173</code>
          </li>
          <li>
            Android Chrome: menú → <strong>Instalar app</strong> / Añadir a pantalla de inicio.
          </li>
          <li>
            iPhone Safari: Compartir → <strong>Añadir a pantalla de inicio</strong>.
          </li>
          <li>En Ajustes de la app, guarda la URL del PC y prueba conexión.</li>
        </ol>
        <p className="meta">
          Si hay un APK en <code>/download/</code>, también puedes instalar el paquete Android desde la red local.
        </p>
        <p>
          <a href="/download/">Ver descargas en este servidor</a>
        </p>
      </section>
    </div>
  );
}
