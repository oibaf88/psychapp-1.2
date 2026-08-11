import { useEffect, useState } from "react";
import { clearLegacyApiBaseOverride, getApiBase, getLegacyApiBaseOverride } from "../api";

export default function SettingsPage() {
  const [apiBase] = useState(getApiBase());
  const [legacyOverride, setLegacyOverride] = useState("");
  const [status, setStatus] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    const stored = getLegacyApiBaseOverride();
    if (stored) {
      clearLegacyApiBaseOverride();
      setLegacyOverride(stored);
      setStatus("Se borro una URL de API antigua guardada en este navegador.");
    }
  }, []);

  async function testConnection() {
    setBusy(true);
    setStatus(null);
    try {
      const url = `${apiBase}/api/v1/health`;
      const res = await fetch(url, { method: "GET" });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const body = await res.json();
      setStatus(`Conexion OK: ${JSON.stringify(body)}`);
    } catch (e) {
      setStatus(`No se pudo conectar con la API configurada: ${(e as Error).message}.`);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="page">
      <h1>Ajustes</h1>
      <p className="subtitle">La app usa el servidor API fijado en el despliegue. No se puede cambiar desde el navegador.</p>

      <section className="card">
        <h2>Servidor API activo</h2>
        <p>
          <code>{apiBase || "mismo origen"}</code>
        </p>
        {legacyOverride && (
          <p className="info">
            Se encontro y limpio una URL antigua guardada localmente: <code>{legacyOverride}</code>
          </p>
        )}
        <div className="alert-actions">
          <button type="button" className="btn-secondary" disabled={busy} onClick={testConnection}>
            {busy ? "Probando..." : "Probar conexion"}
          </button>
        </div>
        {status && <p className="info">{status}</p>}
      </section>

      <section className="card">
        <h2>Sesion</h2>
        <p className="meta">
          Si el navegador conserva datos antiguos, cierra sesion y vuelve a entrar. La direccion de API ya no depende de
          valores escritos manualmente.
        </p>
      </section>
    </div>
  );
}
