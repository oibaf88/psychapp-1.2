import { useEffect, useState } from "react";
import {
  clearLegacyApiBaseOverride,
  getApiBase,
  getLegacyApiBaseOverride,
  llmSettingsApi,
  type LLMEndpointConfigIn,
  type LLMEndpointStatusOut,
  type LLMEndpointTestOut,
} from "../api";

/**
 * Settings, available in every profile.
 *
 * Two different things live here and must not be confused, which is why they
 * are separate cards with different affordances:
 *
 *   * **The API server** is where this frontend sends its own requests. It is
 *     fixed at deploy time and deliberately not editable — a browser-side
 *     override was the source of a whole class of "it works on my machine"
 *     support cases.
 *   * **The model endpoint** is where the *backend* sends inference calls.
 *     That one is editable, because pointing the app at a model you run
 *     yourself is the only way to see how it behaves on one.
 */

type Provider = "anthropic" | "openai_compatible";

interface FormState {
  provider: Provider;
  baseUrl: string;
  chatModel: string;
  analysisModel: string;
  copilotModel: string;
  apiKey: string;
  maxTokens: number;
  timeoutSeconds: number;
  label: string;
}

// Defaults that match what the common local runtimes actually serve, so the
// form is a starting point rather than a blank page.
const PRESETS: { id: string; name: string; baseUrl: string; model: string; hint: string }[] = [
  {
    id: "ollama",
    name: "Ollama",
    baseUrl: "http://localhost:11434/v1",
    model: "llama3.1:8b",
    hint: "El nombre del modelo es el de «ollama list».",
  },
  {
    id: "lmstudio",
    name: "LM Studio",
    baseUrl: "http://localhost:1234/v1",
    model: "local-model",
    hint: "Arranca el servidor local desde la pestaña «Developer».",
  },
  {
    id: "llamacpp",
    name: "llama.cpp",
    baseUrl: "http://127.0.0.1:8080/v1",
    model: "gguf-model",
    hint: "Levántalo con «llama-server -m modelo.gguf --port 8080».",
  },
  {
    id: "vllm",
    name: "vLLM",
    baseUrl: "http://localhost:8000/v1",
    model: "meta-llama/Llama-3.1-8B-Instruct",
    hint: "El modelo es el identificador con el que arrancaste el servidor.",
  },
];

function formFromStatus(status: LLMEndpointStatusOut): FormState {
  const active = status.active;
  return {
    provider: (active.provider === "openai_compatible" ? "openai_compatible" : "anthropic") as Provider,
    baseUrl: active.base_url || "",
    chatModel: active.chat_model || "",
    analysisModel: active.analysis_model || "",
    // The *explicit* value, not the resolved one. Prefilling with the
    // resolved model would silently pin the copilot to whatever chat was,
    // so changing the chat model later would leave the copilot behind.
    copilotModel: active.copilot_model_explicit || "",
    apiKey: "",
    maxTokens: active.max_tokens,
    timeoutSeconds: active.timeout_seconds,
    label: active.label || "",
  };
}

export default function SettingsPage() {
  const [apiBase] = useState(getApiBase());
  const [legacyOverride, setLegacyOverride] = useState("");
  const [apiStatus, setApiStatus] = useState<string | null>(null);
  const [apiBusy, setApiBusy] = useState(false);

  const [status, setStatus] = useState<LLMEndpointStatusOut | null>(null);
  const [form, setForm] = useState<FormState | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saved, setSaved] = useState<string | null>(null);
  const [testResult, setTestResult] = useState<LLMEndpointTestOut | null>(null);
  const [busy, setBusy] = useState<"" | "save" | "test" | "reset">("");

  useEffect(() => {
    const stored = getLegacyApiBaseOverride();
    if (stored) {
      clearLegacyApiBaseOverride();
      setLegacyOverride(stored);
      setApiStatus("Se borró una URL de API antigua guardada en este navegador.");
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    llmSettingsApi
      .read()
      .then((value) => {
        if (cancelled) return;
        setStatus(value);
        setForm(formFromStatus(value));
      })
      .catch((e: Error) => !cancelled && setLoadError(e.message));
    return () => {
      cancelled = true;
    };
  }, []);

  async function testApiConnection() {
    setApiBusy(true);
    setApiStatus(null);
    try {
      const res = await fetch(`${apiBase}/api/v1/health`, { method: "GET" });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const body = await res.json();
      setApiStatus(`Conexión OK: ${JSON.stringify(body)}`);
    } catch (e) {
      setApiStatus(`No se pudo conectar con la API configurada: ${(e as Error).message}.`);
    } finally {
      setApiBusy(false);
    }
  }

  function patch(changes: Partial<FormState>) {
    setForm((current) => (current ? { ...current, ...changes } : current));
    setSaved(null);
    setSaveError(null);
  }

  function applyPreset(id: string) {
    const preset = PRESETS.find((item) => item.id === id);
    if (!preset) return;
    patch({
      provider: "openai_compatible",
      baseUrl: preset.baseUrl,
      chatModel: preset.model,
      analysisModel: preset.model,
      // Left blank so it follows chat: a local runtime usually has one model
      // loaded, and pinning it here would survive a later change of chat model.
      copilotModel: "",
      label: preset.name,
    });
    setTestResult(null);
  }

  function payload(current: FormState): LLMEndpointConfigIn {
    return {
      provider: current.provider,
      base_url: current.provider === "openai_compatible" ? current.baseUrl : null,
      chat_model: current.chatModel,
      analysis_model: current.analysisModel,
      // Blank is a real answer: the backend reads it as "same as chat".
      copilot_model: current.copilotModel,
      // An untouched field means "keep the stored key", never "clear it":
      // the current key is never sent to the browser, so a blank box here
      // carries no information about it.
      api_key: current.apiKey ? current.apiKey : null,
      max_tokens: current.maxTokens,
      timeout_seconds: current.timeoutSeconds,
      label: current.label,
    };
  }

  async function runTest() {
    if (!form) return;
    setBusy("test");
    setTestResult(null);
    setSaveError(null);
    try {
      const result = await llmSettingsApi.test({
        provider: form.provider,
        base_url: form.provider === "openai_compatible" ? form.baseUrl : null,
        chat_model: form.chatModel,
        analysis_model: form.analysisModel || form.chatModel,
        copilot_model: form.copilotModel || form.chatModel,  // the test needs a concrete name
        api_key: form.apiKey || null,
        timeout_seconds: Math.min(form.timeoutSeconds, 60),
      });
      setTestResult(result);
    } catch (e) {
      setSaveError((e as Error).message);
    } finally {
      setBusy("");
    }
  }

  async function save() {
    if (!form) return;
    setBusy("save");
    setSaveError(null);
    setSaved(null);
    try {
      const next = await llmSettingsApi.save(payload(form));
      setStatus(next);
      setForm(formFromStatus(next));
      setSaved("Guardado. Las próximas conversaciones y análisis usarán este modelo.");
    } catch (e) {
      setSaveError((e as Error).message);
    } finally {
      setBusy("");
    }
  }

  async function reset() {
    setBusy("reset");
    setSaveError(null);
    setSaved(null);
    try {
      const next = await llmSettingsApi.reset();
      setStatus(next);
      setForm(formFromStatus(next));
      setTestResult(null);
      setSaved("Se ha vuelto al modelo configurado en el despliegue.");
    } catch (e) {
      setSaveError((e as Error).message);
    } finally {
      setBusy("");
    }
  }

  const active = status?.active;
  // Two different reasons the form can be read-only, and they need different
  // instructions: the deployment has the feature off, or this account is not
  // an administrator. `notice` above already explains which.
  const disabledByDeployment = Boolean(status && !status.override_allowed);
  const locked = Boolean(status && !status.can_edit);

  return (
    <div className="page">
      <h1>Ajustes</h1>
      <p className="subtitle">Servidor de la aplicación y modelo de lenguaje que atiende a los agentes.</p>

      <section className="card">
        <h2>Servidor API activo</h2>
        <p>
          <code>{apiBase || "mismo origen"}</code>
        </p>
        <p className="meta">
          Es donde esta interfaz envía sus peticiones. Se fija en el despliegue y no se puede cambiar desde el
          navegador.
        </p>
        {legacyOverride && (
          <p className="info">
            Se encontró y limpió una URL antigua guardada localmente: <code>{legacyOverride}</code>
          </p>
        )}
        <div className="alert-actions">
          <button type="button" className="btn-secondary" disabled={apiBusy} onClick={testApiConnection}>
            {apiBusy ? "Probando..." : "Probar conexión"}
          </button>
        </div>
        {apiStatus && <p className="info">{apiStatus}</p>}
      </section>

      <section className="card">
        <h2>Modelo de lenguaje</h2>
        <p className="meta">
          Es el modelo al que el <strong>backend</strong> envía las llamadas del Agente 1 (conversación) y del Agente 2
          y 4 (análisis). Puedes apuntarlo a un servidor propio para probar tu modelo local con la aplicación real.
        </p>

        {loadError && <p className="error">No se pudo leer la configuración del modelo: {loadError}</p>}

        {active && (
          <div className="llm-active">
            <div>
              <span className="llm-active-label">En uso ahora</span>
              <strong>{active.chat_model}</strong>
              <span className="meta"> · {active.provider_label}</span>
            </div>
            <dl className="llm-active-grid">
              <div>
                <dt>Modelo de análisis</dt>
                <dd>{active.analysis_model}</dd>
              </div>
              <div>
                <dt>Modelo del copiloto</dt>
                <dd>{active.copilot_model || active.chat_model}</dd>
              </div>
              <div>
                <dt>Endpoint</dt>
                <dd>{active.base_url ? <code>{active.base_url}</code> : "API oficial de Anthropic"}</dd>
              </div>
              <div>
                <dt>Origen</dt>
                <dd>{active.source === "runtime" ? "Configurado desde esta pantalla" : "Configuración del despliegue"}</dd>
              </div>
              <div>
                <dt>Desde</dt>
                <dd>{active.updated_at ? new Date(active.updated_at).toLocaleString() : "Inicio del despliegue"}</dd>
              </div>
            </dl>
          </div>
        )}

        {status?.notice && <p className={status.is_local ? "warning" : "info"}>{status.notice}</p>}

        {disabledByDeployment && (
          <p className="meta">
            Para habilitarlo, arranca el backend con <code>LLM_ALLOW_RUNTIME_OVERRIDE=true</code>. Viene desactivado a
            propósito: en un despliegue compartido, quien usa la aplicación no es quien la administra.
          </p>
        )}

        {form && !locked && (
          <>
            <div className="llm-provider-choice">
              <label className={form.provider === "anthropic" ? "llm-option is-selected" : "llm-option"}>
                <input
                  type="radio"
                  name="llm-provider"
                  checked={form.provider === "anthropic"}
                  onChange={() => patch({ provider: "anthropic" })}
                />
                <span>
                  <strong>Claude (API de Anthropic)</strong>
                  <span className="meta">La configuración con la que se despliega la aplicación.</span>
                </span>
              </label>
              <label className={form.provider === "openai_compatible" ? "llm-option is-selected" : "llm-option"}>
                <input
                  type="radio"
                  name="llm-provider"
                  checked={form.provider === "openai_compatible"}
                  onChange={() => patch({ provider: "openai_compatible" })}
                />
                <span>
                  <strong>Modelo propio</strong>
                  <span className="meta">Cualquier servidor con API compatible con OpenAI.</span>
                </span>
              </label>
            </div>

            {form.provider === "openai_compatible" && (
              <>
                <div className="llm-presets">
                  <span className="meta">Rellenar con los valores de:</span>
                  {PRESETS.map((preset) => (
                    <button
                      key={preset.id}
                      type="button"
                      className="btn-chip"
                      onClick={() => applyPreset(preset.id)}
                      title={preset.hint}
                    >
                      {preset.name}
                    </button>
                  ))}
                </div>

                <label className="field">
                  <span>URL del servidor</span>
                  <input
                    type="url"
                    value={form.baseUrl}
                    placeholder="http://localhost:11434/v1"
                    onChange={(e) => patch({ baseUrl: e.target.value })}
                  />
                  <span className="meta">
                    Si el backend corre en Docker, <code>localhost</code> es el contenedor: usa{" "}
                    <code>host.docker.internal</code> o la IP de tu equipo.
                  </span>
                </label>
              </>
            )}

            <div className="field-row">
              <label className="field">
                <span>Modelo de conversación (Agente 1)</span>
                <input
                  type="text"
                  value={form.chatModel}
                  onChange={(e) => patch({ chatModel: e.target.value })}
                />
              </label>
              <label className="field">
                <span>Modelo de análisis (Agentes 2 y 4)</span>
                <input
                  type="text"
                  value={form.analysisModel}
                  onChange={(e) => patch({ analysisModel: e.target.value })}
                />
              </label>
            </div>

            <div className="field-row">
              <label className="field">
                <span>Modelo del copiloto clínico (Agente 3)</span>
                <input
                  type="text"
                  value={form.copilotModel}
                  placeholder={form.chatModel || "Igual que el de conversación"}
                  onChange={(e) => patch({ copilotModel: e.target.value })}
                />
                <span className="meta">
                  Déjalo vacío para usar el mismo que la conversación. Lee expedientes largos para un
                  profesional que no está esperando delante de la pantalla, así que admite un ajuste más
                  lento y más a fondo.
                </span>
              </label>
            </div>

            <div className="field-row">
              <label className="field">
                <span>API key {form.provider === "openai_compatible" && "(opcional)"}</span>
                <input
                  type="password"
                  value={form.apiKey}
                  placeholder={active?.has_api_key ? "Guardada — déjalo vacío para conservarla" : "Sin clave"}
                  onChange={(e) => patch({ apiKey: e.target.value })}
                />
              </label>
              <label className="field">
                <span>Tokens máximos</span>
                <input
                  type="number"
                  min={256}
                  max={32768}
                  value={form.maxTokens}
                  onChange={(e) => patch({ maxTokens: Number(e.target.value) })}
                />
              </label>
              <label className="field">
                <span>Espera máxima (s)</span>
                <input
                  type="number"
                  min={5}
                  max={600}
                  value={form.timeoutSeconds}
                  onChange={(e) => patch({ timeoutSeconds: Number(e.target.value) })}
                />
                <span className="meta">Un modelo local en CPU puede tardar bastante.</span>
              </label>
            </div>

            <label className="field">
              <span>Nombre para identificarlo</span>
              <input
                type="text"
                value={form.label}
                placeholder="Mi Llama 3.1 en el portátil"
                onChange={(e) => patch({ label: e.target.value })}
              />
            </label>

            <div className="alert-actions">
              <button type="button" className="btn-secondary" disabled={busy !== ""} onClick={runTest}>
                {busy === "test" ? "Probando..." : "Probar el endpoint"}
              </button>
              <button type="button" disabled={busy !== ""} onClick={save}>
                {busy === "save" ? "Guardando..." : "Guardar y usar este modelo"}
              </button>
              {active?.source === "runtime" && (
                <button type="button" className="btn-secondary" disabled={busy !== ""} onClick={reset}>
                  {busy === "reset" ? "Restaurando..." : "Volver al modelo del despliegue"}
                </button>
              )}
            </div>

            {testResult && (
              <p className={testResult.ok ? "info" : "error"}>
                {testResult.ok ? "El endpoint responde. " : ""}
                {testResult.detail}
              </p>
            )}
            {saveError && <p className="error">{saveError}</p>}
            {saved && <p className="info">{saved}</p>}
          </>
        )}
      </section>

      <section className="card">
        <h2>Qué cambia y qué no</h2>
        <ul className="plain-list">
          <li>
            <strong>Queda registrado qué modelo produjo cada cosa.</strong> Cada respuesta del chat y cada análisis
            guarda su proveedor, su modelo y su endpoint. El historial de un paciente se lee igual aunque una parte se
            grabara con Claude y otra con tu modelo local: cada entrada dice de dónde salió.
          </li>
          <li>
            <strong>El motor de riesgo no cambia.</strong> Los niveles de alerta se calculan con reglas deterministas
            sobre datos guardados. Ningún modelo, ni Claude ni el tuyo, decide un nivel.
          </li>
          <li>
            <strong>La detección lingüística sí depende del modelo.</strong> Un modelo más débil puede pasar por alto
            una señal que Claude sí marca. Las señales que sí detecte entran en el motor exactamente igual.
          </li>
          <li>
            <strong>El texto del paciente viaja al servidor que indiques.</strong> Con un modelo en tu equipo no sale de
            tu red; con uno remoto, va a donde apunte esa URL.
          </li>
        </ul>
      </section>

      <section className="card">
        <h2>Sesión</h2>
        <p className="meta">
          Si el navegador conserva datos antiguos, cierra sesión y vuelve a entrar. La dirección de API ya no depende de
          valores escritos manualmente.
        </p>
      </section>
    </div>
  );
}
