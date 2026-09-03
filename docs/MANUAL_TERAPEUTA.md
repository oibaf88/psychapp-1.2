# Manual del terapeuta — PsychDeep / PsychApp 1.2

> **Documento histórico (motor anterior a v1.4).** Las fórmulas y la tabla de
> reglas siguientes se conservan para interpretar evaluaciones antiguas, no
> describen el motor actual. Para el funcionamiento vigente consulta el manual
> dentro de la aplicación y [Fundamentos y límites clínicos](CLINICAL_RISK_BASIS.md).
> Desde v1.4, el score usa `1 / (1 + media(|z|))`, las reglas distinguen deterioro
> de cambio favorable y una señal vigente no refutada de ideación indirecta
> exige al menos N3 (valoración prioritaria). Ninguno de estos índices es una
> escala clínica validada ni una probabilidad de suicidio o recaída.

Este manual explica **todo** lo que un profesional necesita saber para usar
el panel: de dónde salen los datos, cómo se genera cada alerta, qué es
exactamente el score estructural, qué hace y qué no hace cada agente de IA,
y cómo auditar cualquier decisión del sistema hasta la frase que la produjo.

> **PsychApp no es un dispositivo médico ni un sistema de triaje autónomo.**
> No diagnostica, no predice conductas y no sustituye ningún juicio clínico.
> Es un sistema de acompañamiento y de señalización: recoge lo que el
> paciente registra, calcula desviaciones respecto a su propia normalidad y
> te avisa cuando se cumplen criterios explícitos. La decisión siempre es
> tuya.

---

## 1. En una página

| Pieza | Qué hace | Quién decide |
|---|---|---|
| **Check-ins** | El paciente puntúa a diario ánimo, craving, sueño y autoeficacia. | Paciente |
| **Diario** | Texto libre del paciente. | Paciente |
| **Chat** | Conversación del paciente con el Agente 1. | Paciente |
| **Agente 1** | Responde al paciente. Nunca calcula riesgo. | LLM (Claude) |
| **Agente 2** | Lee cada texto (diario y chat) y devuelve señales estructuradas. | LLM (Claude) |
| **Score estructural** | Compara los últimos 7 días de check-ins con la línea base de 21 días del propio paciente. | Estadística local, sin IA |
| **Motor de riesgo** | Decide el nivel 0–4 aplicando reglas fijas en orden. | **Código determinista, sin IA** |
| **Alertas** | Se crean automáticamente en niveles 3 y 4. | Motor determinista |
| **Agente 3 (copiloto)** | Te resume y responde preguntas sobre un paciente. Solo lectura. | LLM (Claude) |
| **Agente 4** | Extrae determinantes sociales (vivienda, apoyo, dinero, pérdidas…) de lo que el paciente escribe. | LLM (Claude) |
| **Índice psicosocial** | Pondera esos determinantes con pesos fijos. | Aritmética local, sin IA |

Lo importante: **ningún modelo de lenguaje decide el nivel de alarma**. Los
agentes 2 y 4 aportan observaciones sobre el texto; el motor determinista
decide. El Agente 3 no puede escribir nada en el historial clínico.

---

## 2. Roles y permisos (RBAC)

| Rol | Ve pacientes | Historial clínico | Hechos confirmados | Gestiona alertas | Copiloto |
|---|---|---|---|---|---|
| **Terapeuta** | Solo los suyos, con asignación `active` o `paused` | Sí | Ve y registra | Sí | Sí |
| **Supervisor** | Todos | Sí | No | Sí | Sí |
| **Admin clínico** | Roster (nombres y emails) | **No** | No | **No** | **No** |
| **Paciente** | — | Solo lo suyo | Registra los suyos | — | — |

- La asignación la solicita el profesional por email y **la tiene que
  aceptar el paciente** (consentimiento `professional_sharing`).
- Una asignación `pending` no da acceso al historial.
- Todo acceso al historial, a la evidencia, al chat del paciente y al
  copiloto queda registrado en el log de auditoría con tu identidad, el
  paciente y la hora.

---

## 3. De dónde salen los datos

### 3.1 Check-ins (dato declarado)
Cuatro números al día, todos 0–10 salvo el sueño (horas):

- **Ánimo** — más alto es mejor.
- **Craving** — más alto es peor.
- **Horas de sueño**.
- **Autoeficacia** — confianza percibida en poder manejar la situación.

Son la única fuente del score estructural.

### 3.2 Diario (texto libre)
Entradas escritas por el paciente. Cada entrada se guarda íntegra y se envía
al Agente 2 para su análisis.

### 3.3 Chat (texto libre)
La conversación con el Agente 1. **Cada mensaje del paciente pasa por el
Agente 2 exactamente igual que una entrada de diario.** Esto significa que
una alerta de nivel 4 puede originarse en un único mensaje de chat, y por
eso el chat completo es visible para ti en la pestaña «Chat del paciente».

### 3.4 Hechos confirmados (declaraciones)
Categorías: `medication_taken`, `relapse`, `consumption_crisis`,
`ideation_active`, `planning`, `correction`, `other`.

Un hecho lo declara **una persona** (el paciente o tú), nunca el sistema. Es
el nivel más alto de evidencia y **ningún modelo puede sobrescribirlo**
(«muro de hechos vs. inferencias»).

### 3.5 Contexto psicosocial (inferencias del Agente 4)
Determinantes sociales extraídos de lo que el paciente escribe: vivienda,
convivencia, apoyo social, familia, economía, ocupación, situación legal,
acceso al tratamiento, estigma, pérdidas, vínculos y rutina, entorno de
consumo y acceso a medios lesivos. Ver sección 5b.

---

## 4. Los cuatro agentes

### Agente 1 — conversacional
Habla con el paciente. Recibe el nivel de alarma ya calculado como contexto
de solo lectura y nunca menciona números ni niveles al paciente. En niveles
3 y 4 el servidor **añade siempre** un bloque fijo con 024 y 112 después de
su respuesta: ese bloque no depende del modelo y se envía aunque la llamada
al modelo falle o sea rechazada.

### Agente 2 — analista lingüístico
No habla con nadie. Lee un texto y devuelve un objeto estructurado y
validado:

| Campo | Tipo | Entra en una regla de riesgo |
|---|---|---|
| `rumination_score` | 0–1 | Sí, como señal convergente (> 0.60) y en la regla extrema (> 0.85) |
| `negative_valence` | 0–1 | No |
| `urgency_level` | 0–1 | No |
| `ambivalence` | 0–1 | No |
| `emotional_complexity` | low/medium/high | No |
| `ideation_indirect` | booleano | **No** — es informativo para ti |
| `ideation_direct` | booleano | **Sí → nivel 4** |
| `consumption_crisis` | booleano | **Sí → nivel 3** |
| `short_rationale` | texto | No |

Si el modelo devuelve algo que no cumple el esquema, la salida se descarta
entera y el motor sigue sin señal lingüística para ese texto. Cada llamada
queda registrada (modelo, versión de prompt, tokens, latencia, error) en la
pestaña «Detalle técnico».

### Agente 4 — extractor psicosocial
No habla con nadie, igual que el Agente 2, y lee las mismas dos fuentes
(chat y diario). Devuelve una lista de observaciones estructuradas, cada
una con: dominio, categoría, si es adversa o protectora, intensidad,
confianza, si describe un **cambio** reciente, un resumen y —obligatorio—
la **frase literal** del paciente que la sostiene.

Tres filtros antes de que una observación entre en la base de datos:

1. **Esquema estricto**: si la salida no valida, se descarta entera.
2. **Coherencia dominio/categoría**: una categoría de «economía» declarada
   bajo «vivienda» se descarta en lugar de adivinar.
3. **Cita comprobada**: la frase debe aparecer *literalmente* en el texto
   del paciente. Una cita inventada no llega nunca a tu pantalla.

### Agente 3 — copiloto clínico
Habla **contigo**, nunca con el paciente. Recibe el expediente del paciente
con fechas y fuentes y debe citarlas. Es **estrictamente de solo lectura**:
no puede crear hechos, señales, evaluaciones ni alertas, ni cambiar el nivel
de riesgo, ni escribir nada que el paciente lea.

---

## 5. El score estructural, en detalle

### 5.1 Qué mide
**Similitud con su propia normalidad reciente.** No gravedad, no riesgo, no
bienestar.

### 5.2 Cómo se calcula

1. **Línea base**: se toman todos los check-ins de los últimos **21 días**.
   Si hay menos de **5**, no hay línea base y la banda es
   `insufficient_data`.
2. Para cada una de las cuatro variables se calcula media y desviación
   típica poblacional. El craving se invierte antes (`craving_inv = 10 −
   craving`) para que en las cuatro «más alto sea mejor».
3. **Ventana reciente**: media de los check-ins de los últimos **7 días**.
4. **z-score por variable**:
   `z = (media_reciente − media_base) / desviación_base`
   (si la desviación base es 0, se fija `z = 0`).
5. **z compuesto**: media de los **valores absolutos** de los cuatro z.
6. **Score**: `score = máx(0, mín(1, 1 − z_compuesto / 3))`

### 5.3 Bandas

| Banda | Score | Lectura |
|---|---|---|
| `stable` | ≥ 0.60 | Los últimos 7 días se parecen a su línea base. |
| `transition` | 0.35 – 0.60 | Desviación moderada. |
| `unstable` | < 0.35 | Se alejan claramente de su línea base. |
| `insufficient_data` | — | Menos de 5 check-ins en 21 días, o ninguno en los últimos 7. |

### 5.4 Ejemplo numérico

Base (21 días): ánimo 6.0 (σ 1.0), craving_inv 5.0 (σ 1.5), sueño 7.0
(σ 1.0), autoeficacia 6.0 (σ 1.0).
Últimos 7 días: ánimo 4.0, craving_inv 3.5, sueño 5.5, autoeficacia 5.0.

```
z_ánimo        = (4.0 − 6.0) / 1.0 = −2.00
z_craving_inv  = (3.5 − 5.0) / 1.5 = −1.00
z_sueño        = (5.5 − 7.0) / 1.0 = −1.50
z_autoeficacia = (5.0 − 6.0) / 1.0 = −1.00

z_compuesto = (2.00 + 1.00 + 1.50 + 1.00) / 4 = 1.375
score       = 1 − 1.375 / 3 = 0.54  →  banda "transition"
```

Los cuatro z son negativos: la desviación es **adversa**. Eso es lo que te
dice la tabla «Score estructural, explicado» y la gráfica de z-scores.

### 5.5 La trampa importante: el score es ciego a la dirección

El compuesto usa **valores absolutos**. Un paciente que mejora mucho y de
golpe (duerme 3 horas más, el craving se desploma) también baja su score y
puede aparecer como `unstable`. Por eso el panel muestra siempre, junto al
score:

- la **desviación adversa media** (media de |z| de lo que ha empeorado), y
- la **desviación favorable media** (media de |z| de lo que ha mejorado).

Si la favorable domina, el panel lo dice explícitamente. **Nunca leas el
score solo: lee el desglose por variable.**

### 5.6 Qué NO es el score

- No compara al paciente con otros pacientes ni con ninguna norma
  poblacional. Solo consigo mismo.
- No incluye nada del diario ni del chat.
- No incluye hechos confirmados.
- Un score alto significa «sin cambios respecto a su normalidad», **nunca**
  «sin riesgo». Un paciente con ideación estable y crónica puede tener 0.95.

---

## 5b. El índice psicosocial, en detalle

### 5b.1 Por qué existe

El deterioro emocional suele ser lo **último** que cambia. Lo primero que
cambia es la situación: alguien se muda «una temporada» a casa de un colega,
deja de quedar con la gente del gimnasio, le retiran una ayuda, se muere una
abuela, vuelve a un piso donde se consume. Cada una de esas frases parece
conversación intrascendente, y hasta esta versión el sistema las tiraba: el
Agente 2 puntúa rumiación e ideación, y el score estructural solo lee cuatro
números diarios. Nada podía ver que la vida de una persona se estaba
estrechando.

### 5b.2 Qué mide

**Adversidad del contexto de vida.** Al contrario que el score estructural,
aquí **más alto es peor**.

### 5b.3 Cómo se calcula

1. De cada dominio cuenta **solo la observación más reciente**, y solo si
   tiene menos de **90 días**. Una situación que mejoró deja de puntuar por
   su estado antiguo.
2. Las observaciones **refutadas** por ti se excluyen por completo.
3. Cada observación aporta `peso_del_dominio × confianza_efectiva ×
   intensidad`, donde la confianza efectiva es **1.0 si tú la confirmaste** y
   la confianza del modelo si sigue como inferida.
4. `índice = clamp(media_ponderada(adversos) − 0.35 × media_ponderada(protectores), 0, 1)`

Los factores protectores **restan hasta un 35 %** de la adversidad, nunca la
cancelan: alguien con buen apoyo puede seguir perdiendo su vivienda.

### 5b.4 Pesos por dominio

| Dominio | Peso |
|---|---|
| Vivienda | 1.00 |
| Apoyo social | 1.00 |
| Acceso a medios lesivos | 0.95 |
| Pérdidas y rupturas | 0.90 |
| Vínculos y rutina | 0.85 |
| Entorno de consumo | 0.85 |
| Acceso a tratamiento | 0.80 |
| Situación económica | 0.80 |
| Familia | 0.75 |
| Convivencia | 0.70 |
| Ocupación | 0.60 |
| Estigma | 0.55 |
| Situación legal | 0.50 |

Estos pesos son un **criterio de diseño explícito**, no un instrumento
psicométrico validado. Están aquí para que puedas discutirlos, no para que
los aceptes.

### 5b.5 Bandas

| Banda | Índice |
|---|---|
| alta | ≥ 0.60 |
| moderada | 0.35 – 0.60 |
| baja | < 0.35 |
| sin datos | no hay observaciones activas |

### 5b.6 Cambios agudos: las señales «inocuas»

Una observación cuenta como **cambio agudo** si cumple las cuatro cosas:

- el modelo la marcó como `is_change`,
- es adversa,
- su categoría está en la lista de cambios agudos (mudanza, pérdida de
  vivienda, aislamiento creciente, ruptura, duelo, pérdida de empleo o de
  ayuda, deudas, abandono de tratamiento, vuelta a entorno de consumo,
  pérdida de rutina, acceso a medios…),
- tiene menos de **14 días** y confianza efectiva ≥ 0.5.

Esto es lo que dispara la regla 8, y es el mecanismo por el que un «nada,
que me he ido unos días a casa de un colega» puede acabar en tu pantalla.

### 5b.7 El límite de seguridad, explícito

**El índice psicosocial por sí solo nunca genera una alerta profesional.**
Como máximo llega a nivel 2 (regla 11). Para alcanzar nivel 3 tiene que
converger con una señal independiente: inestabilidad estructural, sueño
empeorando o rumiación alta. Esto es deliberado: una extracción demasiado
entusiasta no puede sacarte de una sesión.

### 5b.8 Tu papel: confirmar y refutar

Cada observación empieza como `inferida`. En la pestaña «Contexto
psicosocial» puedes:

- **Confirmar**: pasa a contar al 100 % de su intensidad, ignorando la
  confianza del modelo. Úsalo cuando lo hayas verificado en sesión.
- **Refutar**: deja de contar por completo. Úsalo cuando el modelo haya
  malinterpretado la frase.
- **Deshacer**: vuelve a `inferida`.

Cada acción queda auditada y **reevalúa el motor de riesgo al instante**, así
que refutar puede bajar el nivel del paciente en el momento.

### 5b.9 Qué NO es el índice

- No es un diagnóstico ni una predicción.
- No es un instrumento validado ni comparable entre pacientes.
- Un índice bajo puede significar «buen contexto» **o** «no ha hablado de
  ello». El panel te dice cuántos dominios hay activos precisamente por eso.
- No sustituye a preguntar. Es un recordatorio de qué preguntar.

---

## 6. El motor de riesgo: cómo se genera cada nivel

Es código Python determinista. No hay IA en él. Se evalúan **catorce reglas
en orden fijo** y gana **la primera que se cumple**.

| # | Regla | Nivel | Condición |
|---|---|---|---|
| 1 | `N4_declaracion_ideacion_o_plan` | 4 | Hecho confirmado `ideation_active` o `planning` en las últimas **48 h** |
| 2 | `N4_senal_linguistica_ideacion_directa` | 4 | Señal del Agente 2 con `ideation_direct = true` y **menos de 12 h** de antigüedad |
| 3 | `N4_convergencia_critica_extrema` | 4 | score < 0.20 **y** rumiación > 0.85 **y** sueño empeorando |
| 4 | `N3_declaracion_crisis_consumo` | 3 | Hecho confirmado `consumption_crisis` en 48 h |
| 4b | `N3_declaracion_recaida` | 3 | Hecho confirmado `relapse` en 48 h: revisión profesional, no probabilidad ni emergencia 112 |
| 5 | `N3_senal_linguistica_crisis_consumo` | 3 | Señal del Agente 2 con `consumption_crisis = true` (< 12 h) |
| 6 | `N3_unstable_persistente_con_convergencia` | 3 | Banda `unstable` **y** ≥ 3 días naturales distintos inestables **y** (sueño empeorando **o** rumiación > 0.60) |
| 7 | `N3_unstable_persistente` | 3 | Banda `unstable` **y** ≥ 5 días naturales distintos inestables |
| 8 | `N3_desestabilizacion_psicosocial_aguda` | 3 | Cambio psicosocial adverso en **14 días** **y** (sueño empeorando **o** rumiación > 0.60 **o** banda no estable) |
| 9 | `N3_convergencia_psicosocial_estructural` | 3 | Índice psicosocial ≥ **0.60** **y** banda `unstable` |
| 10 | `N2_desviacion_moderada` | 2 | Banda `transition`, o primer día en `unstable` |
| 11 | `N2_vulnerabilidad_psicosocial` | 2 | Índice psicosocial ≥ **0.50** sin nada más |
| 12 | `N0_estable` | 0 | Banda `stable` |
| 13 | `N1_datos_insuficientes_o_sin_criterios` | 1 | Banda `insufficient_data` |
| 14 | `N1_sin_criterios_superiores` | 1 | Regla de cierre |

### 6.1 Ventanas temporales, y por qué importan

- **Hechos críticos: 48 h.** Una declaración de ideación mantiene el nivel 4
  durante dos días y luego deja de contar por sí sola. Si sigue siendo
  válida, regístrala de nuevo.
- **Señales del Agente 2: 12 h.** Un texto de anteayer no puede mantener al
  paciente en nivel 4 indefinidamente.
- **Persistencia estructural: días naturales distintos.** Cinco check-ins el
  mismo martes cuentan como **un** día, no como cinco. Esto evita que un
  paciente muy activo dispare la regla en una tarde.
- **Contexto psicosocial: 90 días activos, 14 días para «cambio agudo».** Una
  situación de vida persiste mucho más que un marcador lingüístico, pero un
  *cambio* solo es agudo durante dos semanas.

### 6.2 Qué significa cada nivel

| Nivel | Nombre | Qué pasa |
|---|---|---|
| 0 | Autogestión | Nada. Seguimiento habitual. |
| 1 | Autogestión / sin datos | **No es «bajo riesgo»**: puede ser «no lo sé». |
| 2 | Prevención | La app refuerza autorregulación con el paciente. **No crea alerta.** |
| 3 | Alarma profesional | Crea alerta + notificación. Revisión humana en cuanto sea posible. |
| 4 | Emergencia | Crea alerta + notificación. El paciente ve el bloque fijo con 024 y 112. |

### 6.3 Cuándo se calcula

En cada mensaje de chat, en cada entrada de diario, al registrar un hecho, y
cuando pulsas «Reevaluar riesgo». Cada cálculo guarda una evaluación
inmutable con todos sus datos de entrada, de modo que una evaluación
antigua siempre se explica con los datos con los que se decidió.

---

## 7. Por qué «score 0.91 estable» + «alerta nivel 4» NO es un error

Es el caso que más confunde, y es coherente por diseño:

- El **score estructural** solo mira **check-ins**.
- Las reglas 1, 2, 4 y 5 —las que más disparan— **no miran el score en
  absoluto**: miran hechos declarados y textos.

Una persona puede seguir durmiendo, puntuando y funcionando exactamente
como siempre (score alto, banda estable) **y a la vez** escribir un mensaje
con ideación directa, o declarar un plan. El score dice «no ha cambiado su
patrón diario»; la alerta dice «ha dicho algo que exige atención hoy».
Ambas cosas son verdad simultáneamente.

Por eso la ficha muestra siempre, en la tarjeta de cabecera, **qué tipo de
evidencia disparó el nivel**:

- `Hecho declarado` — alguien lo declaró. Es un hecho.
- `Texto del paciente` — el Agente 2 lo infirió de una frase concreta. **Lee
  la frase**, está justo debajo.
- `Check-ins` — ahí sí manda el score.
- `Varias señales` — convergencia.

Y una frase explícita de reconciliación cuando el score y el nivel parecen
contradecirse.

---

## 8. Alertas: ciclo de vida

1. **Creación.** Solo niveles ≥ 3. Se crea si no hay ya una alerta abierta
   del mismo nivel en las últimas **24 h** (antiduplicado) y si el nivel es
   superior al de cualquier alerta abierta.
2. **Refresco.** Si ya existe una abierta del mismo nivel, se actualiza en
   lugar de crear una nueva.
3. **Notificación.** Se encola notificación in-app y, si hay SMTP
   configurado, por email.
4. **Gestión.** Tres acciones:
   - **Reconocer** (`acknowledged`): la has visto, sigue abierta.
   - **Resolver** (`resolved`): requiere nota de resolución.
   - **Descartar** (`dismissed`): requiere motivo. En **nivel 4 el motivo es
     obligatorio** y no puede estar en blanco.
5. Todo queda en auditoría con tu identidad.

### 8.1 Falsos positivos del Agente 2

Si la alerta viene de `Texto del paciente` y al leer la frase original ves
que es ironía, una cita, una letra de canción o un error de lectura:

1. Descarta la alerta indicando el motivo.
2. Registra un hecho de categoría **`correction`** describiendo el error.

El hecho queda en el historial y documenta el falso positivo. Ten en cuenta
que un hecho `correction` **no cancela** la señal: si el paciente vuelve a
escribir algo similar en las 12 h siguientes, la regla puede volver a
disparar.

---

## 9. La ficha del paciente, pestaña a pestaña

| Pestaña | Para qué |
|---|---|
| **Resumen** | Tarjeta de nivel con su explicación, score estructural explicado y desglosado, gráficas de nivel y de score, línea de tiempo de alertas y hechos. |
| **Métricas** | Las cinco gráficas: nivel de alarma, score estructural, z-scores por variable, check-ins crudos y señales del Agente 2. Cada gráfica lleva su propia nota de «cómo se lee». |
| **Evidencia** | Una tarjeta por texto analizado: lo que escribió, lo que leyó el Agente 2, qué nivel salió y si generó alerta. Filtrable por chat / diario / con bandera. |
| **Copiloto clínico** | Conversación con el Agente 3 sobre este paciente. |
| **Chat del paciente** | Transcripción completa de su conversación con el Agente 1. |
| **Diario** | Entradas íntegras. |
| **Check-ins** | Gráfica y tabla de valores crudos. |
| **Hechos** | Hechos activos y formulario para registrar uno nuevo. |
| **Alertas** | Alertas con su regla, su explicación y la evidencia detrás. |
| **Motor de riesgo** | Desglose regla a regla de cada evaluación, con los valores observados y los umbrales. |
| **Plan de seguridad** | El plan que escribió el paciente. |
| **Detalle técnico** | Trazas de las llamadas al Agente 2 (modelo, prompt, tokens, latencia, errores) y plantillas de protocolo. Para auditoría, no para el día a día. |

En la gráfica de señales del Agente 2 puedes **pinchar un punto** para
saltar directamente al texto que lo produjo en la pestaña Evidencia.

---

## 10. El copiloto clínico (Agente 3)

### Qué puede hacer
- Resumir la situación del paciente a partir de lo que ha contado y escrito.
- Responder preguntas de seguimiento.
- Señalar cambios, patrones y contradicciones entre fuentes.

### Cómo usarlo
Desde el menú **Copiloto** eliges paciente, o desde la pestaña «Copiloto
clínico» de su ficha. La conversación es tuya y de ese paciente: otro
profesional asignado tiene su propio hilo.

Puedes ajustar la **ventana de expediente** (14–180 días). Una ventana más
corta da respuestas más centradas en lo reciente; una más larga permite
preguntar por evolución.

### Qué NO puede hacer
- Diagnosticar, proponer medicación o predecir conductas.
- Calcular o cambiar el nivel de alarma.
- Crear hechos, señales, evaluaciones o alertas.
- Escribir nada que el paciente lea.

### Cómo verificarlo
Está obligado a citar fuente y fecha en cada afirmación clínica —«(diario,
12/08)», «(chat, 14/08)»— precisamente para que puedas contrastarlo en las
pestañas correspondientes. **Si una afirmación no viene con fuente, trátala
como no verificada.** Es un modelo de lenguaje: puede equivocarse al leer.

---

## 11. Errores de interpretación más frecuentes

| Lectura errónea | Lectura correcta |
|---|---|
| «Score 0.9 = paciente bien» | Score 0.9 = paciente **igual que siempre**. Su «siempre» puede ser malo. |
| «Score bajo = está peor» | Score bajo = **ha cambiado**. Mira los z-scores: puede haber mejorado mucho. |
| «Nivel 1 = riesgo bajo» | Nivel 1 puede ser **falta de datos**. Mira la banda: si es `insufficient_data`, el sistema no sabe. |
| «El nivel 2 me lo notifican» | No. El nivel 2 **no crea alerta**. Aparece en la ficha y en la lista de pacientes. |
| «La IA decidió el nivel 4» | El nivel lo decidió una regla fija. La IA solo aportó la lectura de un texto. |
| «Si no hay alerta, no hace falta mirar» | El historial completo está disponible sin alerta y ése es su uso normal. |
| «El copiloto ha visto algo que yo no» | El copiloto ve exactamente lo mismo que tú, en las pestañas. Verifícalo. |
| «Índice psicosocial bajo = contexto bueno» | Puede ser «no ha hablado del tema». Mira cuántos dominios hay activos. |
| «El índice psicosocial me ha generado la alerta» | Nunca solo. Siempre converge con otra señal; solo puede llegar a nivel 2 por su cuenta. |
| «Si el Agente 4 lo extrajo, es verdad» | Es una inferencia. La cita literal está ahí para que la compruebes, y puedes refutarla. |

---

## 12. Privacidad y auditoría

- Los datos viven en tu propia infraestructura (Supabase/Postgres). El
  esquema no está expuesto vía PostgREST y las tablas sensibles tienen
  `FORCE ROW LEVEL SECURITY` con acceso solo para el rol del backend.
- Lo único que sale a un tercero es el **texto que se envía a la API de
  Anthropic** para los agentes 1, 2 y 3. No hay modelos Claude descargables.
- Las trazas del Agente 2 **no duplican** el texto: apuntan al mensaje o
  entrada original.
- Los mensajes de error del proveedor nunca se guardan en crudo: solo una
  categoría de una lista cerrada, para que un error no filtre contenido.
- Cada acceso tuyo a un historial, a la evidencia, al chat del paciente o al
  copiloto queda auditado.

---

## 13. Glosario

- **Línea base**: media y desviación típica de las cuatro variables en los
  últimos 21 días del propio paciente.
- **z-score**: cuántas desviaciones típicas se separa la media reciente de
  la media base. Negativo = por debajo.
- **z compuesto**: media de los cuatro |z|.
- **Banda de confianza**: `stable` / `transition` / `unstable` /
  `insufficient_data`.
- **Señal (inference)**: cualquier cosa calculada por el sistema o inferida
  por un modelo. Puede ser superada por un hecho.
- **Hecho (fact)**: declaración de una persona. No la sobrescribe ningún
  modelo.
- **Correlation id**: identificador que enlaza un mensaje, su análisis, la
  señal resultante y la evaluación de riesgo del mismo ciclo.
- **Traza de Agent 2**: registro de una llamada al analista lingüístico
  (modelo, versión de prompt y esquema, tokens, latencia, resultado).

---

## 14. Recordatorio final

El sistema está deliberadamente construido para que **la parte que decide no
sea la parte inteligente**. El motor que fija el nivel es un conjunto de
reglas que puedes leer entero en esta página. Los modelos de lenguaje
aportan lectura de texto y ayuda a la comprensión, y su fallo degrada el
sistema a «sin señal», nunca a «nivel equivocado».

Tu criterio va por encima de todo lo anterior.
