"""
Canonical catalogue of the psychosocial domains PsychApp extracts from what
the patient writes.

Why this file exists, and why it is data instead of prose scattered across
services: the same eighteen domains have to line up in four places at once.

  1. Agent 4's tool schema (``app/content/prompts.py``) — the enum the model
     is allowed to emit.
  2. The deterministic index (``app/services/psychosocial.py``) — the weights
     that turn observations into numbers the risk engine can compare against
     fixed thresholds.
  3. The deterministic risk engine (``app/services/risk_engine.py``) — which
     domains count as an acute social rupture, which ones are the
     interpersonal-theory constructs, which ones describe relapse context.
  4. The therapist panel (``app/services/clinical_view.py``) — Spanish labels,
     what the domain means clinically, and what to ask about it in session.

If the four ever drift apart, an alert can fire on a domain the therapist has
no label for. Keeping them in one table makes that impossible.

Clinical grounding, briefly:

  * The material/relational split follows the social-determinants framing the
    project docs use for "contexto de apoyo y social".
  * ``carga_percibida`` (perceived burdensomeness) and ``pertenencia_frustrada``
    (thwarted belongingness) are the two interpersonal constructs from the
    Interpersonal Theory of Suicide (Joiner). They are tracked separately from
    generic "low support" because their convergence is what turns an
    apparently innocuous message into a warning sign.
  * ``senales_despedida`` collects the classic leave-taking markers — giving
    things away, putting affairs in order, sudden calm after a hopeless
    period, goodbye-shaped messages. On their own each reads as harmless;
    that is exactly why a machine has to keep count of them.

Nothing here decides an alert level. This module only names things.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# Ordinal risk scale used by every domain. The model picks one of these; the
# deterministic layer converts it to the number below. Keeping the numeric
# mapping out of the model's hands is deliberate: the model is good at reading
# a sentence, not at calibrating a 0-1 scale across sessions.
STATE_PROTECTIVE = "protector"
STATE_NEUTRAL = "neutro"
STATE_MILD = "riesgo_leve"
STATE_MODERATE = "riesgo_moderado"
STATE_SEVERE = "riesgo_alto"

STATES = (STATE_PROTECTIVE, STATE_NEUTRAL, STATE_MILD, STATE_MODERATE, STATE_SEVERE)

STATE_RISK_VALUE: dict[str, float] = {
    STATE_PROTECTIVE: 0.0,
    STATE_NEUTRAL: 0.25,
    STATE_MILD: 0.50,
    STATE_MODERATE: 0.75,
    STATE_SEVERE: 1.00,
}

STATE_LABELS: dict[str, str] = {
    STATE_PROTECTIVE: "Protector",
    STATE_NEUTRAL: "Neutro",
    STATE_MILD: "Riesgo leve",
    STATE_MODERATE: "Riesgo moderado",
    STATE_SEVERE: "Riesgo alto",
}

# Ordinal position, so rules can say "at least riesgo_moderado" without
# hard-coding a list of strings.
STATE_ORDER: dict[str, int] = {state: index for index, state in enumerate(STATES)}

DIRECTION_IMPROVING = "mejora"
DIRECTION_STABLE = "estable"
DIRECTION_WORSENING = "empeora"
DIRECTION_UNKNOWN = "desconocido"

DIRECTIONS = (DIRECTION_IMPROVING, DIRECTION_STABLE, DIRECTION_WORSENING, DIRECTION_UNKNOWN)

DIRECTION_LABELS: dict[str, str] = {
    DIRECTION_IMPROVING: "mejorando",
    DIRECTION_STABLE: "estable",
    DIRECTION_WORSENING: "empeorando",
    DIRECTION_UNKNOWN: "sin datos de evolución",
}

ONSET_RECENT = "reciente"
ONSET_CHRONIC = "cronico"
ONSET_UNKNOWN = "desconocido"

ONSETS = (ONSET_RECENT, ONSET_CHRONIC, ONSET_UNKNOWN)

ONSET_LABELS: dict[str, str] = {
    ONSET_RECENT: "situación reciente",
    ONSET_CHRONIC: "situación sostenida en el tiempo",
    ONSET_UNKNOWN: "antigüedad desconocida",
}

# Groups drive the deterministic indices.
GROUP_MATERIAL = "material"
GROUP_RELATIONAL = "relacional"
GROUP_INTERPERSONAL = "riesgo_interpersonal"
GROUP_SIGNALS = "senales_sutiles"

GROUP_LABELS: dict[str, str] = {
    GROUP_MATERIAL: "Condiciones materiales y estructurales",
    GROUP_RELATIONAL: "Vínculos y apoyo social",
    GROUP_INTERPERSONAL: "Riesgo interpersonal (teoría interpersonal del suicidio)",
    GROUP_SIGNALS: "Señales sutiles de despedida y contexto de consumo",
}


@dataclass(frozen=True)
class Domain:
    key: str
    label: str
    group: str
    meaning: str
    session_question: str
    # Weight inside its index. 0 means "descriptive only, never scored".
    support_weight: float = 0.0
    material_weight: float = 0.0
    interpersonal_weight: float = 0.0
    relapse_weight: float = 0.0
    examples: tuple[str, ...] = field(default_factory=tuple)


DOMAINS: tuple[Domain, ...] = (
    # ------------------------------------------------ material / structural ---
    Domain(
        key="vivienda",
        label="Vivienda",
        group=GROUP_MATERIAL,
        meaning=(
            "Dónde vive y con qué estabilidad: alquiler, okupación, casa de un familiar, "
            "recurso residencial, riesgo de desahucio, sinhogarismo."
        ),
        session_question="¿Dónde está durmiendo estas semanas y cuánto cree que va a poder seguir ahí?",
        material_weight=1.0,
        examples=("me han dado un mes para dejar el piso", "estoy en casa de un colega mientras tanto"),
    ),
    Domain(
        key="convivencia",
        label="Convivencia",
        group=GROUP_MATERIAL,
        meaning=(
            "Con quién comparte casa y cómo le afecta: soledad, convivencia con personas que "
            "consumen, conflicto doméstico, convivencia protectora."
        ),
        session_question="¿Quién hay en casa cuando llega, y cómo le sienta eso?",
        material_weight=0.6,
        relapse_weight=0.8,
        examples=("vivo con dos que se meten todos los findes", "desde que se fue mi hermana estoy solo"),
    ),
    Domain(
        key="economia",
        label="Recursos económicos",
        group=GROUP_MATERIAL,
        meaning="Ingresos, deudas, prestaciones, capacidad de llegar a fin de mes, dependencia económica.",
        session_question="¿Cómo va lo económico este mes? ¿Hay algo que ya no puede pagar?",
        material_weight=1.0,
        examples=("me han cortado la ayuda", "debo tres meses de alquiler"),
    ),
    Domain(
        key="empleo_ocupacion",
        label="Empleo y estructura diaria",
        group=GROUP_MATERIAL,
        meaning=(
            "Trabajo, estudios, actividad con la que organiza el día. La pérdida de estructura "
            "diurna es un factor de recaída independiente del dinero."
        ),
        session_question="¿Cómo es un día suyo entre semana ahora mismo?",
        material_weight=0.8,
        relapse_weight=0.6,
        examples=("me han echado", "llevo dos semanas sin salir de casa por el día"),
    ),
    Domain(
        key="necesidades_basicas",
        label="Necesidades básicas",
        group=GROUP_MATERIAL,
        meaning="Alimentación, higiene, ropa, poder pagar la medicación o el transporte a las citas.",
        session_question="¿Está comiendo con regularidad? ¿Puede pagar la medicación y los desplazamientos?",
        material_weight=1.0,
        examples=("hay días que no como", "no tengo para el metro hasta la consulta"),
    ),
    Domain(
        key="legal_administrativo",
        label="Situación legal y administrativa",
        group=GROUP_MATERIAL,
        meaning=(
            "Causas judiciales, multas, situación documental o de extranjería, custodia, "
            "trámites que condicionan todo lo demás."
        ),
        session_question="¿Hay algún trámite o asunto legal pendiente que le esté pesando?",
        material_weight=0.7,
        examples=("tengo juicio el mes que viene", "se me ha caducado la tarjeta y no puedo trabajar"),
    ),
    Domain(
        key="acceso_recursos",
        label="Acceso a recursos y continuidad de tratamiento",
        group=GROUP_MATERIAL,
        meaning=(
            "Si puede llegar a los recursos que necesita: cita con salud mental, dispensación, "
            "servicios sociales, listas de espera, cambios de referente."
        ),
        session_question="¿Ha podido mantener las citas y la medicación estas semanas?",
        material_weight=0.8,
        relapse_weight=0.4,
        examples=("me han cambiado de psiquiatra otra vez", "me dan cita para dentro de tres meses"),
    ),
    # ------------------------------------------------------------ relational ---
    Domain(
        key="apoyo_social",
        label="Red de apoyo",
        group=GROUP_RELATIONAL,
        meaning=(
            "Personas a las que puede recurrir de verdad: amistades, iguales, asociación, "
            "grupo de ayuda mutua. No es cuánta gente conoce, sino a quién puede llamar de noche."
        ),
        session_question="Si esta noche se pusiera muy mal, ¿a quién podría llamar? ¿Lo haría?",
        support_weight=1.0,
        relapse_weight=0.6,
        examples=("no tengo a nadie a quien contarle esto", "he vuelto al grupo de los martes"),
    ),
    Domain(
        key="familia",
        label="Familia",
        group=GROUP_RELATIONAL,
        meaning="Relación con la familia de origen: apoyo, conflicto, ruptura, control, dependencia.",
        session_question="¿Cómo está la relación con su familia estas semanas?",
        support_weight=0.9,
        examples=("mi madre ya no me coge el teléfono", "mi hermano se ha ofrecido a acompañarme"),
    ),
    Domain(
        key="pareja",
        label="Pareja o vínculo íntimo",
        group=GROUP_RELATIONAL,
        meaning="Relación de pareja o vínculo afectivo principal: apoyo, conflicto, ruptura, violencia.",
        session_question="¿Cómo está lo de la pareja? ¿Le suma o le desgasta ahora mismo?",
        support_weight=0.8,
        examples=("lo hemos dejado", "él es el único que sabe lo del consumo"),
    ),
    Domain(
        key="aislamiento",
        label="Aislamiento y soledad",
        group=GROUP_RELATIONAL,
        meaning=(
            "Contacto real con otras personas y sensación subjetiva de soledad. Puede haber "
            "aislamiento con red disponible: cuenta la retirada, no solo la ausencia."
        ),
        session_question="¿Cuándo fue la última vez que estuvo con alguien sin que fuera por obligación?",
        support_weight=1.0,
        interpersonal_weight=0.25,
        examples=("llevo días sin hablar con nadie", "he dejado de contestar a todo el mundo"),
    ),
    Domain(
        key="duelo_perdida",
        label="Pérdidas y duelos",
        group=GROUP_RELATIONAL,
        meaning=(
            "Muertes, rupturas, despidos, pérdida de custodia, pérdida de un animal, pérdidas "
            "recientes de cualquier tipo, incluidas las que la persona minimiza."
        ),
        session_question="¿Ha perdido a alguien o algo importante en los últimos meses?",
        support_weight=0.6,
        examples=("murió mi amigo en marzo", "ya no veo a mi hija"),
    ),
    Domain(
        key="estigma_discriminacion",
        label="Estigma y discriminación",
        group=GROUP_RELATIONAL,
        meaning=(
            "Rechazo vivido por consumo, por VIH, por orientación o identidad, por situación "
            "administrativa. Incluye el estigma anticipado (dejar de pedir ayuda por vergüenza)."
        ),
        session_question="¿Hay sitios o personas con las que no puede contar esto? ¿Qué teme que pase si lo cuenta?",
        support_weight=0.5,
        examples=("en el trabajo no lo puede saber nadie", "en urgencias me trataron como a un yonqui"),
    ),
    Domain(
        key="cuidados_responsabilidades",
        label="Cuidados y responsabilidades",
        group=GROUP_RELATIONAL,
        meaning=(
            "Personas o animales a su cargo. Es ambivalente: sostiene (razón para vivir) y "
            "sobrecarga (agotamiento, culpa)."
        ),
        session_question="¿Quién depende de usted ahora mismo? ¿Cómo lo lleva?",
        support_weight=0.4,
        examples=("cuido de mi madre las 24 horas", "tengo al perro, por eso me levanto"),
    ),
    # --------------------------------------------- interpersonal risk (IPTS) ---
    Domain(
        key="carga_percibida",
        label="Sentirse una carga",
        group=GROUP_INTERPERSONAL,
        meaning=(
            "Percepción de ser un lastre para los demás («estarían mejor sin mí», «solo doy "
            "problemas»). Constructo de carga percibida de la teoría interpersonal del suicidio. "
            "Se registra aunque la persona lo diga de pasada o en tono de broma."
        ),
        session_question="¿Ha pensado que su familia o su gente estarían mejor si usted no estuviera?",
        interpersonal_weight=0.40,
        examples=("solo les doy disgustos", "les estoy arruinando la vida"),
    ),
    Domain(
        key="pertenencia_frustrada",
        label="No pertenecer / no ser necesitado",
        group=GROUP_INTERPERSONAL,
        meaning=(
            "Sensación de no encajar, de no ser querido ni necesitado por nadie. Constructo de "
            "pertenencia frustrada de la teoría interpersonal del suicidio. Distinto de estar "
            "solo: se puede sentir rodeado de gente y no pertenecer."
        ),
        session_question="¿Siente que hay algún sitio donde encaje o alguien a quien le importe de verdad?",
        interpersonal_weight=0.35,
        examples=("sobro en todas partes", "nadie me echaría de menos"),
    ),
    # -------------------------------------------------------- subtle signals ---
    Domain(
        key="senales_despedida",
        label="Señales de despedida",
        group=GROUP_SIGNALS,
        meaning=(
            "Marcadores clásicos de preparación, cada uno inocuo por separado: repartir o regalar "
            "pertenencias, dejar asuntos en orden, mensajes de agradecimiento o cierre, buscar "
            "acomodo para sus animales, calma repentina tras un periodo de desesperanza."
        ),
        session_question=(
            "Ha mencionado que está ordenando cosas / despidiéndose. ¿Qué hay detrás de eso? "
            "¿Ha pensado en hacerse daño?"
        ),
        interpersonal_weight=0.0,
        examples=(
            "le he dado mi guitarra a mi sobrino",
            "quería darte las gracias por todo, de verdad",
            "he dejado los papeles ordenados por si acaso",
            "de repente estoy tranquilo, ya no me agobia nada",
        ),
    ),
    Domain(
        key="contexto_consumo",
        label="Contexto social del consumo",
        group=GROUP_SIGNALS,
        meaning=(
            "Entorno que sostiene o dispara el consumo: sesiones de chemsex, disponibilidad, "
            "contactos, apps, presión de grupo, fechas señaladas, aniversarios."
        ),
        session_question="¿Con quién y en qué situaciones aparece el consumo últimamente?",
        relapse_weight=1.0,
        examples=("me han vuelto a escribir del grupo", "este finde hay fiesta y no sé decir que no"),
    ),
)

DOMAIN_KEYS: tuple[str, ...] = tuple(domain.key for domain in DOMAINS)
DOMAIN_BY_KEY: dict[str, Domain] = {domain.key: domain for domain in DOMAINS}

# Domain sets the deterministic risk engine reasons about by name.
# A rupture in any of these within days is what "acute social disconnection"
# means operationally.
ACUTE_RUPTURE_DOMAINS: frozenset[str] = frozenset(
    {
        "vivienda",
        "economia",
        "necesidades_basicas",
        "apoyo_social",
        "familia",
        "pareja",
        "aislamiento",
        "duelo_perdida",
        "empleo_ocupacion",
    }
)

INTERPERSONAL_DOMAINS: frozenset[str] = frozenset({"carga_percibida", "pertenencia_frustrada", "aislamiento"})
LEAVE_TAKING_DOMAIN = "senales_despedida"


def domain_label(key: str) -> str:
    domain = DOMAIN_BY_KEY.get(key)
    return domain.label if domain else key


def state_at_least(state: str | None, minimum: str) -> bool:
    """True when ``state`` is at or above ``minimum`` on the ordinal scale."""
    if state not in STATE_ORDER or minimum not in STATE_ORDER:
        return False
    return STATE_ORDER[state] >= STATE_ORDER[minimum]


def risk_value(state: str | None) -> float | None:
    return STATE_RISK_VALUE.get(state) if state else None
