import { useState } from "react";
import { Link } from "react-router-dom";

/**
 * In-app therapist manual.
 *
 * Mirrors docs/MANUAL_TERAPEUTA.md. It lives in the product rather than in
 * a wiki because the questions it answers ("what is this number?", "why is
 * this patient at level 4 with a good score?") come up while looking at a
 * patient, not before.
 */

type SectionId =
  | "resumen"
  | "roles"
  | "fuentes"
  | "agentes"
  | "score"
  | "psicosocial"
  | "motor"
  | "paradoja"
  | "alertas"
  | "ficha"
  | "copiloto"
  | "errores"
  | "privacidad"
  | "glosario";

const SECTIONS: { id: SectionId; label: string }[] = [
  { id: "resumen", label: "1. En una página" },
  { id: "roles", label: "2. Roles y permisos" },
  { id: "fuentes", label: "3. De dónde salen los datos" },
  { id: "agentes", label: "4. Los cuatro agentes" },
  { id: "score", label: "5. El score estructural" },
  { id: "psicosocial", label: "5b. El índice psicosocial" },
  { id: "motor", label: "6. El motor de riesgo" },
  { id: "paradoja", label: "7. Score alto + alerta 4" },
  { id: "alertas", label: "8. Alertas" },
  { id: "ficha", label: "9. La ficha, pestaña a pestaña" },
  { id: "copiloto", label: "10. El copiloto clínico" },
  { id: "errores", label: "11. Errores de interpretación" },
  { id: "privacidad", label: "12. Privacidad y auditoría" },
  { id: "glosario", label: "13. Glosario" },
];

export default function ManualPage() {
  const [active, setActive] = useState<SectionId>("resumen");

  return (
    <div className="page manual-page">
      <p>
        <Link to="/professional">← Volver a pacientes</Link>
      </p>
      <h1>Manual del terapeuta</h1>
      <p className="subtitle">
        Cómo funciona la app, cómo se genera cada alerta, qué es exactamente el score estructural y qué hace
        —y qué no hace— cada agente de IA.
      </p>

      <div className="manual-callout">
        <strong>PsychApp no es un dispositivo médico ni un sistema de triaje autónomo.</strong> No diagnostica,
        no predice conductas y no sustituye ningún juicio clínico. Recoge lo que el paciente registra, calcula
        desviaciones respecto a su propia normalidad y avisa cuando se cumplen criterios explícitos. La decisión
        siempre es tuya.
      </div>

      <div className="manual-layout">
        <nav className="manual-nav" aria-label="Índice del manual">
          {SECTIONS.map((section) => (
            <button
              key={section.id}
              type="button"
              className={active === section.id ? "manual-nav-item active" : "manual-nav-item"}
              onClick={() => setActive(section.id)}
            >
              {section.label}
            </button>
          ))}
        </nav>

        <article className="manual-content card">
          {active === "resumen" && (
            <>
              <h2>1. En una página</h2>
              <div className="table-wrap">
                <table className="table">
                  <thead>
                    <tr>
                      <th>Pieza</th>
                      <th>Qué hace</th>
                      <th>Quién decide</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr>
                      <td>Check-ins</td>
                      <td>El paciente puntúa a diario ánimo, craving, sueño y autoeficacia.</td>
                      <td>Paciente</td>
                    </tr>
                    <tr>
                      <td>Diario</td>
                      <td>Texto libre del paciente.</td>
                      <td>Paciente</td>
                    </tr>
                    <tr>
                      <td>Chat</td>
                      <td>Conversación del paciente con el Agente 1.</td>
                      <td>Paciente</td>
                    </tr>
                    <tr>
                      <td>Agente 1</td>
                      <td>Responde al paciente. Nunca calcula riesgo.</td>
                      <td>LLM (Claude)</td>
                    </tr>
                    <tr>
                      <td>Agente 2</td>
                      <td>Lee cada texto (diario y chat) y devuelve señales estructuradas.</td>
                      <td>LLM (Claude)</td>
                    </tr>
                    <tr>
                      <td>Score estructural</td>
                      <td>Compara los últimos 7 días de check-ins con la línea base de 21 días.</td>
                      <td>Estadística local, sin IA</td>
                    </tr>
                    <tr className="row-highlight">
                      <td>Motor de riesgo</td>
                      <td>Decide el nivel 0–4 aplicando reglas fijas en orden.</td>
                      <td>
                        <strong>Código determinista, sin IA</strong>
                      </td>
                    </tr>
                    <tr>
                      <td>Alertas</td>
                      <td>Se crean automáticamente en niveles 3 y 4.</td>
                      <td>Motor determinista</td>
                    </tr>
                    <tr>
                      <td>Agente 3 (copiloto)</td>
                      <td>Te resume y responde preguntas sobre un paciente. Solo lectura.</td>
                      <td>LLM (Claude)</td>
                    </tr>
                    <tr>
                      <td>Agente 4</td>
                      <td>Extrae determinantes sociales (vivienda, apoyo, dinero, pérdidas…) de lo que escribe.</td>
                      <td>LLM (Claude)</td>
                    </tr>
                    <tr>
                      <td>Índice psicosocial</td>
                      <td>Pondera esos determinantes con pesos fijos.</td>
                      <td>Aritmética local, sin IA</td>
                    </tr>
                  </tbody>
                </table>
              </div>
              <p className="manual-key">
                Lo importante: <strong>ningún modelo de lenguaje decide el nivel de alarma</strong>. Los agentes
                2 y 4 aportan observaciones sobre el texto; el motor determinista decide. El Agente 3 no puede
                escribir nada en el historial clínico.
              </p>
            </>
          )}

          {active === "roles" && (
            <>
              <h2>2. Roles y permisos (RBAC)</h2>
              <div className="table-wrap">
                <table className="table">
                  <thead>
                    <tr>
                      <th>Rol</th>
                      <th>Ve pacientes</th>
                      <th>Historial clínico</th>
                      <th>Hechos</th>
                      <th>Alertas</th>
                      <th>Copiloto</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr>
                      <td>Terapeuta</td>
                      <td>Solo los suyos (asignación activa o pausada)</td>
                      <td>Sí</td>
                      <td>Ve y registra</td>
                      <td>Sí</td>
                      <td>Sí</td>
                    </tr>
                    <tr>
                      <td>Supervisor</td>
                      <td>Todos</td>
                      <td>Sí</td>
                      <td>No</td>
                      <td>Sí</td>
                      <td>Sí</td>
                    </tr>
                    <tr>
                      <td>Admin clínico</td>
                      <td>Roster (nombre y email)</td>
                      <td>No</td>
                      <td>No</td>
                      <td>No</td>
                      <td>No</td>
                    </tr>
                    <tr>
                      <td>Paciente</td>
                      <td>—</td>
                      <td>Solo lo suyo</td>
                      <td>Registra los suyos</td>
                      <td>—</td>
                      <td>—</td>
                    </tr>
                  </tbody>
                </table>
              </div>
              <ul>
                <li>
                  La asignación la solicitas por email y <strong>la tiene que aceptar el paciente</strong>{" "}
                  (consentimiento <code>professional_sharing</code>).
                </li>
                <li>
                  Una asignación <code>pending</code> no da acceso al historial.
                </li>
                <li>
                  Todo acceso al historial, a la evidencia, al chat del paciente y al copiloto queda registrado
                  en auditoría con tu identidad, el paciente y la hora.
                </li>
              </ul>
            </>
          )}

          {active === "fuentes" && (
            <>
              <h2>3. De dónde salen los datos</h2>
              <h3>Check-ins (dato declarado)</h3>
              <p>
                Cuatro valores diarios: <strong>ánimo</strong> (0–10, más alto mejor),{" "}
                <strong>craving</strong> (0–10, más alto peor), <strong>horas de sueño</strong> y{" "}
                <strong>autoeficacia</strong> (0–10, más alto mejor). Son la <em>única</em> fuente del score
                estructural.
              </p>
              <h3>Diario (texto libre)</h3>
              <p>Se guarda íntegro y se envía al Agente 2 para su análisis.</p>
              <h3>Chat (texto libre)</h3>
              <p>
                <strong>Cada mensaje del paciente pasa por el Agente 2 exactamente igual que una entrada de
                diario.</strong> Una alerta de nivel 4 puede originarse en un único mensaje de chat, y por eso el
                chat completo es visible en la pestaña «Chat del paciente» de su ficha.
              </p>
              <h3>Hechos confirmados (declaraciones)</h3>
              <p>
                Categorías: <code>medication_taken</code>, <code>relapse</code>, <code>consumption_crisis</code>,{" "}
                <code>ideation_active</code>, <code>planning</code>, <code>correction</code>, <code>other</code>.
                Un hecho lo declara <strong>una persona</strong>, nunca el sistema, y ningún modelo puede
                sobrescribirlo («muro de hechos vs. inferencias»).
              </p>
            </>
          )}

          {active === "agentes" && (
            <>
              <h2>4. Los cuatro agentes</h2>
              <h3>Agente 1 — conversacional</h3>
              <p>
                Habla con el paciente. Recibe el nivel ya calculado como contexto de solo lectura y nunca
                menciona niveles ni puntuaciones al paciente. En niveles 3 y 4 el servidor{" "}
                <strong>añade siempre</strong> un bloque fijo con 024 y 112 después de su respuesta: ese bloque
                no depende del modelo y se envía aunque la llamada falle o sea rechazada.
              </p>
              <h3>Agente 2 — analista lingüístico</h3>
              <p>No habla con nadie. Lee un texto y devuelve un objeto estructurado y validado:</p>
              <div className="table-wrap">
                <table className="table">
                  <thead>
                    <tr>
                      <th>Campo</th>
                      <th>Tipo</th>
                      <th>¿Entra en una regla de riesgo?</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr>
                      <td>
                        <code>rumination_score</code>
                      </td>
                      <td>0–1</td>
                      <td>Sí, como señal convergente (&gt; 0.60) y en la regla extrema (&gt; 0.85)</td>
                    </tr>
                    <tr>
                      <td>
                        <code>negative_valence</code>
                      </td>
                      <td>0–1</td>
                      <td>No</td>
                    </tr>
                    <tr>
                      <td>
                        <code>urgency_level</code>
                      </td>
                      <td>0–1</td>
                      <td>No</td>
                    </tr>
                    <tr>
                      <td>
                        <code>ambivalence</code>
                      </td>
                      <td>0–1</td>
                      <td>No</td>
                    </tr>
                    <tr>
                      <td>
                        <code>emotional_complexity</code>
                      </td>
                      <td>low/medium/high</td>
                      <td>No</td>
                    </tr>
                    <tr>
                      <td>
                        <code>ideation_indirect</code>
                      </td>
                      <td>booleano</td>
                      <td>No — informativo para ti</td>
                    </tr>
                    <tr className="row-highlight">
                      <td>
                        <code>ideation_direct</code>
                      </td>
                      <td>booleano</td>
                      <td>
                        <strong>Sí → nivel 4</strong>
                      </td>
                    </tr>
                    <tr className="row-highlight">
                      <td>
                        <code>consumption_crisis</code>
                      </td>
                      <td>booleano</td>
                      <td>
                        <strong>Sí → nivel 3</strong>
                      </td>
                    </tr>
                    <tr>
                      <td>
                        <code>short_rationale</code>
                      </td>
                      <td>texto</td>
                      <td>No</td>
                    </tr>
                  </tbody>
                </table>
              </div>
              <p>
                Si el modelo devuelve algo que no cumple el esquema, la salida se descarta entera y el motor
                sigue sin señal lingüística para ese texto. Cada llamada queda registrada en «Detalle técnico».
              </p>
              <h3>Agente 4 — extractor psicosocial</h3>
              <p>
                No habla con nadie, igual que el Agente 2, y lee las mismas dos fuentes. Devuelve observaciones
                estructuradas con dominio, categoría, valencia, intensidad, confianza, si es un{" "}
                <strong>cambio</strong> reciente, un resumen y —obligatorio— la <strong>frase literal</strong>{" "}
                del paciente que la sostiene.
              </p>
              <p>Tres filtros antes de que una observación entre en la base de datos:</p>
              <ol>
                <li>
                  <strong>Esquema estricto</strong>: si la salida no valida, se descarta entera.
                </li>
                <li>
                  <strong>Coherencia dominio/categoría</strong>: una categoría de «economía» declarada bajo
                  «vivienda» se descarta en lugar de adivinar.
                </li>
                <li>
                  <strong>Cita comprobada</strong>: la frase debe aparecer <em>literalmente</em> en el texto del
                  paciente. Una cita inventada no llega nunca a tu pantalla.
                </li>
              </ol>

              <h3>Agente 3 — copiloto clínico</h3>
              <p>
                Habla <strong>contigo</strong>, nunca con el paciente. Recibe su expediente con fechas y fuentes
                y está obligado a citarlas. Es <strong>estrictamente de solo lectura</strong>: no puede crear
                hechos, señales, evaluaciones ni alertas, ni cambiar el nivel de riesgo.
              </p>
            </>
          )}

          {active === "score" && (
            <>
              <h2>5. El score estructural, en detalle</h2>
              <p className="manual-key">
                Mide <strong>similitud con su propia normalidad reciente</strong>. No gravedad, no riesgo, no
                bienestar.
              </p>
              <h3>Cómo se calcula</h3>
              <ol>
                <li>
                  <strong>Línea base</strong>: todos los check-ins de los últimos <strong>21 días</strong>. Con
                  menos de <strong>5</strong>, no hay línea base y la banda es <code>insufficient_data</code>.
                </li>
                <li>
                  Media y desviación típica poblacional por variable. El craving se invierte antes (
                  <code>craving_inv = 10 − craving</code>) para que menos craving sea favorable. El sueño se interpreta en ambas direcciones: más horas no implica siempre mejora.
                </li>
                <li>
                  <strong>Ventana reciente</strong>: media de los últimos <strong>7 días</strong>.
                </li>
                <li>
                  <strong>z por variable</strong>:{" "}
                  <code>z = (media_reciente − media_base) / máx(desviación_base, mínimo_técnico)</code>.
                  El mínimo es 1 punto en escalas 0–10 y 0,5 h en sueño; no son cortes clínicos.
                </li>
                <li>
                  <strong>z compuesto</strong>: media de los <strong>valores absolutos</strong> de los cuatro z.
                </li>
                <li>
                  <strong>Score</strong>: <code>score = 1 / (1 + z_compuesto)</code>, sin saturación artificial a cero.
                </li>
              </ol>

              <h3>Bandas</h3>
              <div className="table-wrap">
                <table className="table">
                  <thead>
                    <tr>
                      <th>Banda</th>
                      <th>Score</th>
                      <th>Lectura</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr>
                      <td>
                        <code>stable</code>
                      </td>
                      <td>≥ 1/2,2 (≈ 0,455)</td>
                      <td>Los últimos 7 días se parecen a su línea base.</td>
                    </tr>
                    <tr>
                      <td>
                        <code>transition</code>
                      </td>
                      <td>≥ 1/2,95 y &lt; 1/2,2</td>
                      <td>Desviación moderada.</td>
                    </tr>
                    <tr>
                      <td>
                        <code>unstable</code>
                      </td>
                      <td>&lt; 1/2,95 (≈ 0,339)</td>
                      <td>Se alejan claramente de su línea base.</td>
                    </tr>
                    <tr>
                      <td>
                        <code>insufficient_data</code>
                      </td>
                      <td>—</td>
                      <td>Menos de 5 check-ins en 21 días, o ninguno en los últimos 7.</td>
                    </tr>
                  </tbody>
                </table>
              </div>

              <h3>Ejemplo numérico</h3>
              <p className="meta">
                Base: ánimo 6.0 (σ 1.0) · craving_inv 5.0 (σ 1.5) · sueño 7.0 (σ 1.0) · autoeficacia 6.0 (σ 1.0).
                Últimos 7 días: 4.0 / 3.5 / 5.5 / 5.0.
              </p>
              <pre className="protocol-box">{`z_ánimo        = (4.0 − 6.0) / 1.0 = −2.00
z_craving_inv  = (3.5 − 5.0) / 1.5 = −1.00
z_sueño        = (5.5 − 7.0) / 1.0 = −1.50
z_autoeficacia = (5.0 − 6.0) / 1.0 = −1.00

z_compuesto = (2.00 + 1.00 + 1.50 + 1.00) / 4 = 1.375
score       = 1 / (1 + 1.375) = 0.421  →  banda "transition"`}</pre>
              <p>Los cuatro z son negativos: la desviación es adversa.</p>

              <h3>La trampa importante: el score es ciego a la dirección</h3>
              <p>
                El compuesto usa <strong>valores absolutos</strong>. Un paciente que mejora mucho y de golpe
                también baja su score y puede aparecer como <code>unstable</code>. Por eso el panel muestra
                siempre la <strong>desviación adversa media</strong> y la{" "}
                <strong>desviación favorable media</strong>, y lo dice explícitamente si domina la favorable.{" "}
                <strong>Las reglas usan un componente separado de deterioro.</strong> En ánimo, craving invertido y autoeficacia toma máx(−z, 0); en sueño toma |z| como cambio bilateral para revisar. Promedia los cuatro ejes, sin compensar cambios adversos con mejoras. Los datos ausentes son desconocidos, no ceros. Las evaluaciones antiguas conservan su fórmula y versión.
              </p>

              <h3>Qué NO es el score</h3>
              <ul>
                <li>No compara al paciente con otros ni con ninguna norma poblacional. Solo consigo mismo.</li>
                <li>No incluye nada del diario ni del chat.</li>
                <li>No incluye hechos confirmados.</li>
                <li>
                  Un score alto significa «sin cambios respecto a su normalidad», <strong>nunca</strong> «sin
                  riesgo». Un paciente con ideación estable y crónica puede tener 0.95.
                </li>
              </ul>
            </>
          )}

          {active === "psicosocial" && (
            <>
              <h2>5b. El índice psicosocial, en detalle</h2>
              <h3>Por qué existe</h3>
              <p>
                El deterioro emocional suele ser lo <strong>último</strong> que cambia. Lo primero que cambia es
                la situación: alguien se muda «una temporada» a casa de un colega, deja de quedar con la gente
                del gimnasio, le retiran una ayuda, se muere una abuela, vuelve a un piso donde se consume. Cada
                una de esas frases parece conversación intrascendente, y hasta esta versión el sistema las
                tiraba.
              </p>
              <p className="manual-key">
                Mide <strong>adversidad del contexto de vida</strong>. Al contrario que el score estructural,
                aquí <strong>más alto es peor</strong>.
              </p>

              <h3>Cómo se calcula</h3>
              <ol>
                <li>
                  De cada dominio cuenta <strong>solo la observación más reciente</strong>, y solo si tiene menos
                  de <strong>90 días</strong>.
                </li>
                <li>
                  Las observaciones <strong>refutadas</strong> por ti se excluyen por completo.
                </li>
                <li>
                  Cada observación aporta <code>peso × confianza_efectiva × intensidad</code>, donde la confianza
                  efectiva es <strong>1.0 si tú la confirmaste</strong> y la del modelo si sigue inferida.
                </li>
                <li>
                  <code>índice = clamp(media_pond(adversos) − 0.35 × media_pond(protectores), 0, 1)</code>
                </li>
              </ol>
              <p>
                Los factores protectores <strong>restan hasta un 35 %</strong> de la adversidad, nunca la
                cancelan: alguien con buen apoyo puede seguir perdiendo su vivienda.
              </p>

              <h3>Pesos por dominio</h3>
              <div className="table-wrap">
                <table className="table">
                  <thead>
                    <tr>
                      <th>Dominio</th>
                      <th>Peso</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(
                      [
                        ["Vivienda", "1.00"],
                        ["Apoyo social", "1.00"],
                        ["Acceso a medios lesivos", "0.95"],
                        ["Pérdidas y rupturas", "0.90"],
                        ["Vínculos y rutina", "0.85"],
                        ["Entorno de consumo", "0.85"],
                        ["Acceso a tratamiento", "0.80"],
                        ["Situación económica", "0.80"],
                        ["Familia", "0.75"],
                        ["Convivencia", "0.70"],
                        ["Ocupación", "0.60"],
                        ["Estigma", "0.55"],
                        ["Situación legal", "0.50"],
                      ] as const
                    ).map(([domain, weight]) => (
                      <tr key={domain}>
                        <td>{domain}</td>
                        <td>{weight}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <p className="manual-key">
                Estos pesos son un <strong>criterio de diseño explícito</strong>, no un instrumento psicométrico
                validado. Están aquí para que puedas discutirlos, no para que los aceptes.
              </p>

              <h3>Bandas</h3>
              <p>
                <strong>alta</strong> ≥ 0.60 · <strong>moderada</strong> 0.35–0.60 · <strong>baja</strong> &lt;
                0.35 · <strong>sin datos</strong> si no hay observaciones activas.
              </p>

              <h3>Cambios agudos: las señales «inocuas»</h3>
              <p>Una observación cuenta como cambio agudo si cumple las cuatro cosas:</p>
              <ul>
                <li>
                  el modelo la marcó como <code>is_change</code>;
                </li>
                <li>es adversa;</li>
                <li>
                  su categoría está en la lista de cambios agudos (mudanza, pérdida de vivienda, aislamiento
                  creciente, ruptura, duelo, pérdida de empleo o de ayuda, deudas, abandono de tratamiento,
                  vuelta a entorno de consumo, pérdida de rutina, acceso a medios…);
                </li>
                <li>
                  tiene menos de <strong>14 días</strong> y confianza efectiva ≥ 0.5.
                </li>
              </ul>
              <p>
                Esto es lo que dispara la regla 8, y es el mecanismo por el que un «nada, que me he ido unos días
                a casa de un colega» puede acabar en tu pantalla.
              </p>

              <h3>El límite de seguridad, explícito</h3>
              <p className="manual-key">
                <strong>El índice psicosocial por sí solo nunca genera una alerta profesional.</strong> Como
                máximo llega a nivel 2. Para alcanzar nivel 3 tiene que converger con una señal independiente:
                inestabilidad estructural, sueño empeorando o rumiación alta. Una extracción demasiado entusiasta
                no puede sacarte de una sesión.
              </p>

              <h3>Tu papel: confirmar y refutar</h3>
              <p>
                Cada observación empieza como <em>inferida</em>. En la pestaña «Contexto psicosocial» puedes{" "}
                <strong>confirmar</strong> (pasa a contar al 100 % de su intensidad, ignorando la confianza del
                modelo), <strong>refutar</strong> (deja de contar por completo) o <strong>deshacer</strong>. Cada
                acción queda auditada y <strong>reevalúa el motor al instante</strong>, así que refutar puede
                bajar el nivel del paciente en el momento.
              </p>

              <h3>Qué NO es el índice</h3>
              <ul>
                <li>No es un diagnóstico ni una predicción.</li>
                <li>No es un instrumento validado ni comparable entre pacientes.</li>
                <li>
                  Un índice bajo puede significar «buen contexto» <strong>o</strong> «no ha hablado de ello». El
                  panel te dice cuántos dominios hay activos precisamente por eso.
                </li>
                <li>No sustituye a preguntar. Es un recordatorio de qué preguntar.</li>
              </ul>
            </>
          )}

          {active === "motor" && (
            <>
              <h2>6. El motor de riesgo: cómo se genera cada nivel</h2>
              <p className="manual-key">N0–N4 son prioridades operativas de revisión, no probabilidades ni una escala clínica validada. La ideación indirecta vigente requiere como mínimo N3, aunque haya mejoras o factores protectores. Una inferencia textual no confirma intención ni plan.</p>
              <p>La indagación de seguridad se orienta por <a href="https://cssrs.columbia.edu/wp-content/uploads/C-SSRS-Screener-with-Triage-Points-for-outpatientambulatory-2026.pdf" target="_blank" rel="noreferrer">C-SSRS</a> y <a href="https://library.samhsa.gov/sites/default/files/safet-flyer-pep24-01-036.pdf" target="_blank" rel="noreferrer">SAFE-T</a>. El seguimiento de consumo considera dominios de <a href="https://www.mentalhealth.va.gov/healthcare-providers/docs/VA_OMH_The_Brief_Addiction-Monitor_Instuctions_508.pdf" target="_blank" rel="noreferrer">BAM</a> y ASSIST; la patología dual requiere evaluación integrada según SAMHSA TIP 42. No se calculan puntuaciones de esos instrumentos desde texto libre o autocheckings: requieren sus preguntas y periodos. Los umbrales del producto necesitan validación clínica prospectiva.</p>
              <p>
                Es código determinista, sin IA. Se evalúan <strong>dieciocho reglas en orden fijo</strong> y gana{" "}
                <strong>la primera que se cumple</strong>.
              </p>
              <div className="table-wrap">
                <table className="table">
                  <thead>
                    <tr>
                      <th>#</th>
                      <th>Regla</th>
                      <th>Nivel</th>
                      <th>Condición</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(
                      [
                        ["1", "N4_declaracion_ideacion_o_plan", 4, "Declaración crítica ideation_active o planning en 48 h; requiere valoración urgente"],
                        ["2", "N4_senal_linguistica_ideacion_directa", 4, "Ideación directa activa y reciente, actual o retenida en la ventana de 12 h"],
                        ["3", "N4_convergencia_interpersonal_despedida", 4, "Ideación indirecta + señal interpersonal vigente + despedida; valoración urgente"],
                        ["4", "N3_senal_linguistica_ideacion_indirecta", 3, "Posible ideación no explicitada activa en 12 h: valoración prioritaria sin compensación"],
                        ["5", "N3_convergencia_critica_extrema", 3, "Componente adverso > 2.4, rumiación > 0.85 y sueño descendente; no emergencia inferida de la suma"],
                        ["6", "N3_declaracion_crisis_consumo", 3, "Crisis de consumo declarada en 48 h"],
                        ["7", "N3_senal_linguistica_crisis_consumo", 3, "Crisis de consumo inferida activa en 12 h"],
                        ["8", "N3_unstable_persistente_con_convergencia", 3, "Banda de deterioro unstable ≥ 3 días distintos con sueño descendente o rumiación elevada"],
                        ["9", "N3_unstable_persistente", 3, "Banda de deterioro unstable ≥ 5 días distintos"],
                        ["10", "N3_desestabilizacion_psicosocial_aguda", 3, "Cambio adverso en 14 días y otra señal convergente"],
                        ["11", "N3_riesgo_interpersonal_alto", 3, "Carga percibida y pertenencia frustrada elevadas, expresadas en 14 días"],
                        ["12", "N3_riesgo_recaida_contextual", 3, "Contexto de consumo elevado y craving ascendente"],
                        ["13", "N3_convergencia_psicosocial_estructural", 3, "Índice psicosocial ≥ 0.60 y banda de deterioro unstable"],
                        ["14", "N2_desviacion_moderada", 2, "Banda de deterioro transition o primer día unstable, sin criterios superiores"],
                        ["15", "N2_vulnerabilidad_psicosocial", 2, "Apoyo bajo, adversidad material, cambio adverso o índice psicosocial ≥ 0.50"],
                        ["16", "N0_estable", 0, "Sin deterioro estadístico suficiente ni criterios superiores; no equivale a seguridad clínica"],
                        ["17", "N1_datos_insuficientes_o_sin_criterios", 1, "Datos insuficientes para calcular deterioro"],
                        ["18", "N1_sin_criterios_superiores", 1, "Regla de cierre"],
                      ] as const
                    ).map(([n, code, level, condition]) => (
                      <tr key={code}>
                        <td>{n}</td>
                        <td>
                          <code>{code}</code>
                        </td>
                        <td>
                          <span className={`level-pill level-${level}`}>N{level}</span>
                        </td>
                        <td>{condition}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              <h3>Ventanas temporales, y por qué importan</h3>
              <ul>
                <li>
                  <strong>Hechos críticos: 48 h.</strong> Una declaración de ideación mantiene el nivel 4 dos
                  días y luego deja de contar por sí sola. Si sigue vigente, regístrala de nuevo.
                </li>
                <li>
                  <strong>Señales del Agente 2: 12 h.</strong> Un texto de anteayer no puede mantener al paciente
                  en nivel 4 indefinidamente.
                </li>
                <li>
                  <strong>Persistencia estructural: días naturales distintos.</strong> Cinco check-ins el mismo
                  martes cuentan como <em>un</em> día.
                </li>
                <li>
                  <strong>Contexto psicosocial: 90 días activos, 14 días para «cambio agudo».</strong> Una
                  situación de vida persiste mucho más que un marcador lingüístico, pero un <em>cambio</em> solo
                  es agudo durante dos semanas.
                </li>
              </ul>

              <h3>Qué significa cada nivel</h3>
              <div className="table-wrap">
                <table className="table">
                  <thead>
                    <tr>
                      <th>Nivel</th>
                      <th>Nombre</th>
                      <th>Qué pasa</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr>
                      <td>0</td>
                      <td>Autogestión</td>
                      <td>Nada. Seguimiento habitual.</td>
                    </tr>
                    <tr>
                      <td>1</td>
                      <td>Autogestión / sin datos</td>
                      <td>
                        <strong>No es «bajo riesgo»</strong>: puede ser «no lo sé».
                      </td>
                    </tr>
                    <tr>
                      <td>2</td>
                      <td>Prevención</td>
                      <td>
                        La app refuerza autorregulación. <strong>No crea alerta.</strong>
                      </td>
                    </tr>
                    <tr>
                      <td>3</td>
                      <td>Alarma profesional</td>
                      <td>Crea alerta + notificación. Revisión humana en cuanto sea posible.</td>
                    </tr>
                    <tr>
                      <td>4</td>
                      <td>Emergencia</td>
                      <td>Crea alerta + notificación. El paciente ve el bloque fijo con 024 y 112.</td>
                    </tr>
                  </tbody>
                </table>
              </div>
              <p>
                <strong>Cuándo se calcula:</strong> en cada mensaje de chat, en cada entrada de diario, al
                registrar un hecho y cuando pulsas «Reevaluar riesgo». Cada cálculo guarda una evaluación
                inmutable, de modo que una evaluación antigua siempre se explica con los datos con los que se
                decidió.
              </p>
            </>
          )}

          {active === "paradoja" && (
            <>
              <h2>7. Por qué «score 0.91 estable» + «alerta nivel 4» NO es un error</h2>
              <p>Es el caso que más confunde, y es coherente por diseño:</p>
              <ul>
                <li>
                  El <strong>score estructural</strong> solo mira <strong>check-ins</strong>.
                </li>
                <li>
                  Las reglas 1, 2, 4 y 5 —las que más disparan— <strong>no miran el score en absoluto</strong>:
                  miran hechos declarados y textos.
                </li>
              </ul>
              <p>
                Una persona puede seguir durmiendo, puntuando y funcionando exactamente como siempre (score
                alto, banda estable) <strong>y a la vez</strong> escribir un mensaje con ideación directa, o
                declarar un plan. El score dice «no ha cambiado su patrón diario»; la alerta dice «ha dicho algo
                que exige atención hoy». Ambas cosas son verdad simultáneamente.
              </p>
              <p>
                Por eso la ficha muestra siempre, en la tarjeta de cabecera,{" "}
                <strong>qué tipo de evidencia disparó el nivel</strong>:
              </p>
              <ul>
                <li>
                  <strong>Hecho declarado</strong> — alguien lo declaró. Es un hecho.
                </li>
                <li>
                  <strong>Texto del paciente</strong> — el Agente 2 lo infirió de una frase concreta.{" "}
                  <strong>Lee la frase</strong>, está justo debajo de la tarjeta.
                </li>
                <li>
                  <strong>Check-ins</strong> — ahí sí manda el score.
                </li>
                <li>
                  <strong>Varias señales</strong> — convergencia.
                </li>
              </ul>
              <p>
                Y añade una frase explícita de reconciliación cuando el score y el nivel parecen contradecirse.
              </p>
            </>
          )}

          {active === "alertas" && (
            <>
              <h2>8. Alertas: ciclo de vida</h2>
              <ol>
                <li>
                  <strong>Creación.</strong> Solo niveles ≥ 3. Se crea si no hay ya una alerta abierta del mismo
                  nivel en las últimas <strong>24 h</strong> y si el nivel supera al de cualquier alerta abierta.
                </li>
                <li>
                  <strong>Refresco.</strong> Si ya existe una abierta del mismo nivel, se actualiza en lugar de
                  duplicarse.
                </li>
                <li>
                  <strong>Notificación.</strong> In-app y, si hay SMTP configurado, por email.
                </li>
                <li>
                  <strong>Gestión.</strong> <em>Reconocer</em> (la has visto, sigue abierta), <em>resolver</em>{" "}
                  (requiere nota) o <em>descartar</em> (requiere motivo; en <strong>nivel 4 es obligatorio</strong>
                  ).
                </li>
                <li>Todo queda en auditoría con tu identidad.</li>
              </ol>
              <h3>Falsos positivos del Agente 2</h3>
              <p>
                Si la alerta viene de «Texto del paciente» y al leer la frase ves que es ironía, una cita o una
                letra de canción:
              </p>
              <ol>
                <li>Descarta la alerta indicando el motivo.</li>
                <li>
                  Registra un hecho de categoría <code>correction</code> describiendo el error.
                </li>
              </ol>
              <p className="manual-key">
                Un hecho <code>correction</code> documenta el falso positivo pero <strong>no cancela</strong> la
                señal: si el paciente vuelve a escribir algo similar en las 12 h siguientes, la regla puede
                volver a disparar.
              </p>
            </>
          )}

          {active === "ficha" && (
            <>
              <h2>9. La ficha del paciente, pestaña a pestaña</h2>
              <div className="table-wrap">
                <table className="table">
                  <thead>
                    <tr>
                      <th>Pestaña</th>
                      <th>Para qué</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(
                      [
                        ["Resumen", "Tarjeta de nivel con su explicación, score estructural desglosado, gráficas de nivel y score, línea de tiempo de alertas y hechos."],
                        ["Métricas", "Las cinco gráficas: nivel de alarma, score estructural, z-scores por variable, check-ins crudos y señales del Agente 2. Cada una con su nota de «cómo se lee»."],
                        ["Contexto psicosocial", "Vivienda, convivencia, apoyo, familia, dinero, ocupación, pérdidas y vínculo con el tratamiento, con la frase literal de la que salen y botones para confirmar o refutar."],
                        ["Evidencia", "Una tarjeta por texto analizado: lo que escribió, lo que leyó el Agente 2, qué nivel salió y si generó alerta. Filtrable."],
                        ["Copiloto clínico", "Conversación con el Agente 3 sobre este paciente."],
                        ["Chat del paciente", "Transcripción completa de su conversación con el Agente 1."],
                        ["Diario", "Entradas íntegras."],
                        ["Check-ins", "Gráfica y tabla de valores crudos."],
                        ["Hechos", "Hechos activos y formulario para registrar uno nuevo."],
                        ["Alertas", "Alertas con su regla, su explicación y la evidencia detrás."],
                        ["Motor de riesgo", "Desglose regla a regla de cada evaluación, con valores observados y umbrales."],
                        ["Plan de seguridad", "El plan que escribió el paciente."],
                        ["Detalle técnico", "Trazas de las llamadas al Agente 2 y plantillas de protocolo. Para auditoría, no para el día a día."],
                      ] as const
                    ).map(([tab, purpose]) => (
                      <tr key={tab}>
                        <td>
                          <strong>{tab}</strong>
                        </td>
                        <td>{purpose}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <p className="manual-key">
                En la gráfica de señales del Agente 2 puedes <strong>pinchar un punto</strong> para saltar
                directamente al texto que lo produjo en la pestaña Evidencia.
              </p>
            </>
          )}

          {active === "copiloto" && (
            <>
              <h2>10. El copiloto clínico (Agente 3)</h2>
              <h3>Qué puede hacer</h3>
              <ul>
                <li>Resumir la situación del paciente a partir de lo que ha contado y escrito.</li>
                <li>Responder preguntas de seguimiento.</li>
                <li>Señalar cambios, patrones y contradicciones entre fuentes.</li>
              </ul>
              <h3>Cómo usarlo</h3>
              <p>
                Desde el menú <Link to="/professional/copilot">Copiloto</Link> eliges paciente, o desde la
                pestaña «Copiloto clínico» de su ficha. La conversación es tuya y de ese paciente: otro
                profesional asignado tiene su propio hilo. Puedes ajustar la{" "}
                <strong>ventana de expediente</strong> (14–180 días).
              </p>
              <h3>Qué NO puede hacer</h3>
              <ul>
                <li>Diagnosticar, proponer medicación o predecir conductas.</li>
                <li>Calcular o cambiar el nivel de alarma.</li>
                <li>Crear hechos, señales, evaluaciones o alertas.</li>
                <li>Escribir nada que el paciente lea.</li>
              </ul>
              <h3>Cómo verificarlo</h3>
              <p className="manual-key">
                Está obligado a citar fuente y fecha en cada afirmación —«(diario, 12/08)», «(chat, 14/08)»—
                precisamente para que puedas contrastarlo. <strong>Si una afirmación no viene con fuente,
                trátala como no verificada.</strong> Es un modelo de lenguaje: puede equivocarse al leer.
              </p>
            </>
          )}

          {active === "errores" && (
            <>
              <h2>11. Errores de interpretación más frecuentes</h2>
              <div className="table-wrap">
                <table className="table">
                  <thead>
                    <tr>
                      <th>Lectura errónea</th>
                      <th>Lectura correcta</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(
                      [
                        ["«Score 0.9 = paciente bien»", "Score 0.9 = paciente igual que siempre. Su «siempre» puede ser malo."],
                        ["«Score bajo = está peor»", "Score bajo = ha cambiado. Mira los z-scores: puede haber mejorado mucho."],
                        ["«Nivel 1 = riesgo bajo»", "Nivel 1 puede ser falta de datos. Si la banda es insufficient_data, el sistema no sabe."],
                        ["«El nivel 2 me lo notifican»", "No. El nivel 2 no crea alerta. Aparece en la ficha y en la lista de pacientes."],
                        ["«La IA decidió el nivel 4»", "El nivel lo decidió una regla fija. La IA solo aportó la lectura de un texto."],
                        ["«Si no hay alerta, no hace falta mirar»", "El historial completo está disponible sin alerta y ése es su uso normal."],
                        ["«El copiloto ha visto algo que yo no»", "El copiloto ve exactamente lo mismo que tú, en las pestañas. Verifícalo."],
                        ["«Índice psicosocial bajo = contexto bueno»", "Puede ser «no ha hablado del tema». Mira cuántos dominios hay activos."],
                        ["«El índice psicosocial me generó la alerta»", "Nunca solo. Siempre converge con otra señal; por su cuenta solo llega a nivel 2."],
                        ["«Si el Agente 4 lo extrajo, es verdad»", "Es una inferencia. La cita literal está ahí para que la compruebes, y puedes refutarla."],
                      ] as const
                    ).map(([wrong, right]) => (
                      <tr key={wrong}>
                        <td className="manual-wrong">{wrong}</td>
                        <td>{right}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}

          {active === "privacidad" && (
            <>
              <h2>12. Privacidad y auditoría</h2>
              <ul>
                <li>
                  Los datos viven en tu propia infraestructura (Supabase/Postgres). El esquema no está expuesto
                  vía PostgREST y las tablas sensibles tienen <code>FORCE ROW LEVEL SECURITY</code> con acceso
                  solo para el rol del backend.
                </li>
                <li>
                  Lo único que sale a un tercero es el <strong>texto enviado a la API de Anthropic</strong> para
                  los agentes 1, 2 y 3. No hay modelos Claude descargables.
                </li>
                <li>Las trazas del Agente 2 no duplican el texto: apuntan al mensaje o entrada original.</li>
                <li>
                  Los mensajes de error del proveedor nunca se guardan en crudo: solo una categoría de una lista
                  cerrada, para que un error no filtre contenido.
                </li>
                <li>
                  Cada acceso tuyo a un historial, a la evidencia, al chat del paciente o al copiloto queda
                  auditado.
                </li>
              </ul>
            </>
          )}

          {active === "glosario" && (
            <>
              <h2>13. Glosario</h2>
              <dl className="plan-dl">
                {(
                  [
                    ["Línea base", "Media y desviación típica de las cuatro variables en los últimos 21 días del propio paciente."],
                    ["z-score", "Cuántas desviaciones típicas se separa la media reciente de la media base. Negativo = por debajo."],
                    ["z compuesto", "Media de los cuatro |z|."],
                    ["Banda de confianza", "stable / transition / unstable / insufficient_data."],
                    ["Señal (inference)", "Algo calculado por el sistema o inferido por un modelo. Puede ser superada por un hecho."],
                    ["Hecho (fact)", "Declaración de una persona. No la sobrescribe ningún modelo."],
                    ["Correlation id", "Identificador que enlaza un mensaje, su análisis, la señal resultante y la evaluación de riesgo del mismo ciclo."],
                    ["Traza de Agente 2", "Registro de una llamada al analista lingüístico: modelo, versión de prompt y esquema, tokens, latencia y resultado."],
                  ] as const
                ).map(([term, definition]) => (
                  <div key={term}>
                    <dt>{term}</dt>
                    <dd>{definition}</dd>
                  </div>
                ))}
              </dl>
              <p className="manual-key">
                El sistema está deliberadamente construido para que <strong>la parte que decide no sea la parte
                inteligente</strong>. El motor que fija el nivel es un conjunto de reglas que puedes leer entero
                en la sección 6. Los modelos aportan lectura de texto y ayuda a la comprensión, y su fallo
                degrada el sistema a «sin señal», nunca a «nivel equivocado». Tu criterio va por encima de todo
                lo anterior.
              </p>
            </>
          )}
        </article>
      </div>
    </div>
  );
}
