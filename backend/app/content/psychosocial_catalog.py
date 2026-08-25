"""
Canonical catalogue of the psychosocial domains PsychApp extracts from what
the patient writes.

Why this is one table instead of prose spread across services: the same
domains have to line up in four places at once.

  1. Agent 4's tool schema (``app/content/prompts.py``) — the enum the model
     is allowed to emit.
  2. The deterministic indices (``app/services/psychosocial.py``) — the
     weights that turn observations into numbers the risk engine compares
     against fixed thresholds.
  3. The deterministic risk engine (``app/services/risk_engine.py``) — which
     domains count as an acute social rupture, which ones are the
     interpersonal-theory constructs, which ones describe relapse context.
  4. The therapist panel (``app/services/clinical_view.py``) — Spanish
     labels, what the domain means clinically, and what to ask about it.

If the four drift apart, an alert can fire on a domain the therapist has no
label for. Keeping them in one structure makes that impossible: the prompt
enum is *derived* from this file, not written alongside it.

Clinical grounding
------------------
The material/relational split follows the social-determinants framing the
project docs use for "contexto de apoyo y social".

``perceived_burden`` and ``thwarted_belonging`` are the two interpersonal
constructs of the Interpersonal Theory of Suicide (Joiner). They are tracked
as *separate* domains rather than folded into "low support" precisely
because it is their convergence — not either one alone — that turns a set of
ordinary-sounding messages into a warning sign. Collapsing them into one
domain would mean the newest observation overwrote the other, and the
convergence could never be observed.

``leave_taking`` collects the classic preparation markers: giving
possessions away, putting affairs in order, goodbye-shaped messages, finding
a home for a pet, sudden calm after a hopeless period. Each is harmless on
its own; that is exactly why a machine has to keep count of them.

Nothing in this module decides an alert level. It only names things and
assigns the weights those names carry.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# ---------------------------------------------------------------- valence ---
VALENCE_RISK = "risk"
VALENCE_PROTECTIVE = "protective"
VALENCE_NEUTRAL = "neutral"

VALENCES = (VALENCE_RISK, VALENCE_PROTECTIVE, VALENCE_NEUTRAL)

# What a neutral, purely descriptive observation is worth on the 0-1 risk
# scale. Not zero: "vivo solo" is not protective, it is the baseline against
# which a later "y llevo días sin hablar con nadie" is read.
NEUTRAL_RISK_VALUE = 0.25

VALENCE_LABELS = {
    VALENCE_RISK: "Adversidad",
    VALENCE_PROTECTIVE: "Protector",
    VALENCE_NEUTRAL: "Descriptivo",
}

# ------------------------------------------------------------------ groups ---
GROUP_MATERIAL = "material"
GROUP_RELATIONAL = "relacional"
GROUP_INTERPERSONAL = "riesgo_interpersonal"
GROUP_SIGNALS = "senales_sutiles"

GROUP_LABELS: dict[str, str] = {
    GROUP_MATERIAL: "Condiciones materiales y estructurales",
    GROUP_RELATIONAL: "Vínculos y apoyo social",
    GROUP_INTERPERSONAL: "Riesgo interpersonal (teoría interpersonal del suicidio)",
    GROUP_SIGNALS: "Señales sutiles y contexto de consumo",
}

GROUP_ORDER = (GROUP_MATERIAL, GROUP_RELATIONAL, GROUP_INTERPERSONAL, GROUP_SIGNALS)


@dataclass(frozen=True)
class Domain:
    """One psychosocial domain, with everything every layer needs from it.

    The four weights place the domain inside the four deterministic indices.
    A weight of 0 means "descriptive only, never scored in this index" — a
    domain can be clinically important to display and still carry no weight,
    which is the case for ``leave_taking`` (gated by its own rule) and
    ``means_access`` (already handled as a confirmed fact upstream).
    """

    key: str
    label: str
    group: str
    meaning: str
    session_question: str
    categories: tuple[str, ...]
    # Legacy blended index (kept so historic assessments stay comparable).
    weight: float = 0.5
    support_weight: float = 0.0
    material_weight: float = 0.0
    interpersonal_weight: float = 0.0
    relapse_weight: float = 0.0
    examples: tuple[str, ...] = field(default_factory=tuple)


DOMAINS: tuple[Domain, ...] = (
    # ------------------------------------------------ material / structural ---
    Domain(
        key="housing",
        label="Vivienda",
        group=GROUP_MATERIAL,
        meaning=(
            "Dónde vive y con qué estabilidad: alquiler, casa de un familiar, recurso "
            "residencial, riesgo de desahucio, sinhogarismo."
        ),
        session_question="¿Dónde está durmiendo estas semanas y cuánto cree que va a poder seguir ahí?",
        categories=(
            "housing_stable",
            "housing_precarious",
            "housing_temporary",
            "housing_homeless",
            "housing_eviction_risk",
            "housing_institution",
        ),
        weight=1.00,
        material_weight=1.0,
        examples=("me han dado un mes para dejar el piso", "estoy en casa de un colega mientras tanto"),
    ),
    Domain(
        key="cohabitation",
        label="Convivencia",
        group=GROUP_MATERIAL,
        meaning=(
            "Con quién comparte casa y cómo le afecta: soledad, convivencia con personas "
            "que consumen, conflicto doméstico, convivencia protectora."
        ),
        session_question="¿Quién hay en casa cuando llega, y cómo le sienta eso?",
        categories=(
            "lives_alone",
            "lives_with_family",
            "lives_with_partner",
            "lives_shared",
            "lives_with_people_who_use",
            "cohabitation_conflict",
        ),
        weight=0.70,
        material_weight=0.6,
        relapse_weight=0.8,
        examples=("vivo con dos que se meten todos los findes", "desde que se fue mi hermana estoy solo"),
    ),
    Domain(
        key="economic",
        label="Situación económica",
        group=GROUP_MATERIAL,
        meaning="Ingresos, deudas, prestaciones, llegar a fin de mes, dependencia económica.",
        session_question="¿Cómo va lo económico este mes? ¿Hay algo que ya no puede pagar?",
        categories=(
            "income_stable",
            "income_precarious",
            "debt",
            "food_insecurity",
            "benefit_loss",
            "financial_dependence",
        ),
        weight=0.80,
        material_weight=1.0,
        examples=("me han cortado la ayuda", "debo tres meses de alquiler"),
    ),
    Domain(
        key="occupation",
        label="Ocupación y estructura diaria",
        group=GROUP_MATERIAL,
        meaning=(
            "Trabajo, estudios, actividad con la que organiza el día. La pérdida de "
            "estructura diurna es un factor de recaída independiente del dinero."
        ),
        session_question="¿Cómo es un día suyo entre semana ahora mismo?",
        categories=("employed", "unemployed", "job_loss", "studying", "sick_leave", "work_stress"),
        weight=0.60,
        material_weight=0.8,
        relapse_weight=0.6,
        examples=("me han echado", "llevo dos semanas sin salir de casa por el día"),
    ),
    Domain(
        key="legal",
        label="Situación legal",
        group=GROUP_MATERIAL,
        meaning=(
            "Causas judiciales, multas, situación documental, custodia: trámites que "
            "condicionan todo lo demás."
        ),
        session_question="¿Hay algún trámite o asunto legal pendiente que le esté pesando?",
        categories=("legal_proceedings", "legal_none"),
        weight=0.50,
        material_weight=0.7,
        examples=("tengo juicio el mes que viene",),
    ),
    Domain(
        key="healthcare_access",
        label="Acceso a tratamiento",
        group=GROUP_MATERIAL,
        meaning=(
            "Si puede llegar a los recursos que necesita: citas, dispensación, listas de "
            "espera, cambios de profesional de referencia."
        ),
        session_question="¿Ha podido mantener las citas y la medicación estas semanas?",
        categories=(
            "treatment_engaged",
            "treatment_dropout",
            "medication_access_problem",
            "appointment_barrier",
        ),
        weight=0.80,
        material_weight=0.8,
        relapse_weight=0.4,
        examples=("me han cambiado de psiquiatra otra vez", "me dan cita para dentro de tres meses"),
    ),
    # ------------------------------------------------------------ relational ---
    Domain(
        key="social_support",
        label="Apoyo social",
        group=GROUP_RELATIONAL,
        meaning=(
            "Personas a las que puede recurrir de verdad. No es cuánta gente conoce, sino "
            "a quién podría llamar de noche."
        ),
        session_question="Si esta noche se pusiera muy mal, ¿a quién podría llamar? ¿Lo haría?",
        categories=(
            "support_strong",
            "support_limited",
            "support_absent",
            "isolation_increasing",
            "new_supportive_relationship",
        ),
        weight=1.00,
        support_weight=1.0,
        relapse_weight=0.6,
        examples=("no tengo a nadie a quien contarle esto", "he vuelto al grupo de los martes"),
    ),
    Domain(
        key="family",
        label="Familia",
        group=GROUP_RELATIONAL,
        meaning="Relación con la familia de origen: apoyo, conflicto, ruptura, control, dependencia.",
        session_question="¿Cómo está la relación con su familia estas semanas?",
        categories=(
            "family_supportive",
            "family_conflict",
            "family_estranged",
            "family_caregiving_burden",
            "family_unaware",
        ),
        weight=0.75,
        support_weight=0.9,
        examples=("mi madre ya no me coge el teléfono", "mi hermano se ha ofrecido a acompañarme"),
    ),
    Domain(
        key="stigma",
        label="Estigma",
        group=GROUP_RELATIONAL,
        meaning=(
            "Rechazo vivido o anticipado por consumo, salud o situación administrativa. "
            "Incluye dejar de pedir ayuda por vergüenza."
        ),
        session_question="¿Hay personas con las que no puede contar esto? ¿Qué teme que pase si lo cuenta?",
        categories=("stigma_experienced", "disclosure_fear"),
        weight=0.55,
        support_weight=0.5,
        examples=("en el trabajo no lo puede saber nadie",),
    ),
    Domain(
        key="loss_event",
        label="Pérdidas y rupturas",
        group=GROUP_RELATIONAL,
        meaning=(
            "Muertes, rupturas, pérdida de custodia, pérdida de un animal: pérdidas "
            "recientes de cualquier tipo, incluidas las que la persona minimiza."
        ),
        session_question="¿Ha perdido a alguien o algo importante en los últimos meses?",
        categories=("bereavement", "breakup", "relationship_loss", "pet_loss", "other_loss"),
        weight=0.90,
        support_weight=0.6,
        examples=("murió mi amigo en marzo", "ya no veo a mi hija"),
    ),
    Domain(
        key="connectedness",
        label="Vínculos y rutina",
        group=GROUP_RELATIONAL,
        meaning=(
            "Contacto real con otras personas, actividades con sentido, pertenencia a un "
            "grupo y planes de futuro. Cuenta la retirada, no solo la ausencia."
        ),
        session_question="¿Cuándo fue la última vez que estuvo con alguien sin que fuera por obligación?",
        categories=("meaningful_activity", "community_belonging", "future_plans", "loss_of_routine"),
        weight=0.85,
        support_weight=1.0,
        interpersonal_weight=0.25,
        examples=("he dejado el grupo", "ya no quedo con los del gimnasio"),
    ),
    # --------------------------------------------- interpersonal risk (IPTS) ---
    Domain(
        key="perceived_burden",
        label="Sentirse una carga",
        group=GROUP_INTERPERSONAL,
        meaning=(
            "Percepción de ser un lastre para los demás («estarían mejor sin mí», «solo doy "
            "problemas»). Constructo de carga percibida de la teoría interpersonal del "
            "suicidio. Se registra aunque se diga de pasada o en tono de broma."
        ),
        session_question="¿Ha pensado que su familia o su gente estarían mejor si usted no estuviera?",
        categories=("burden_severe", "burden_expressed", "burden_absent"),
        weight=0.95,
        interpersonal_weight=0.40,
        examples=("solo les doy disgustos", "les estoy arruinando la vida"),
    ),
    Domain(
        key="thwarted_belonging",
        label="No pertenecer / no ser necesitado",
        group=GROUP_INTERPERSONAL,
        meaning=(
            "Sensación de no encajar, de no ser querido ni necesitado por nadie. Constructo "
            "de pertenencia frustrada. Distinto de estar solo: se puede estar rodeado de "
            "gente y no pertenecer."
        ),
        session_question="¿Siente que hay algún sitio donde encaje o alguien a quien le importe de verdad?",
        categories=("belonging_absent", "belonging_partial", "belonging_present"),
        weight=0.95,
        interpersonal_weight=0.35,
        examples=("sobro en todas partes", "nadie me echaría de menos"),
    ),
    # -------------------------------------------------------- subtle signals ---
    Domain(
        key="leave_taking",
        label="Señales de despedida",
        group=GROUP_SIGNALS,
        meaning=(
            "Marcadores clásicos de preparación, cada uno inocuo por separado: repartir o "
            "regalar pertenencias, dejar asuntos en orden, mensajes de agradecimiento o "
            "cierre, buscar acomodo para sus animales, calma repentina tras un periodo de "
            "desesperanza."
        ),
        session_question=(
            "Ha mencionado que está ordenando cosas o despidiéndose. ¿Qué hay detrás de eso? "
            "¿Ha pensado en hacerse daño?"
        ),
        categories=(
            "giving_possessions_away",
            "affairs_in_order",
            "goodbye_message",
            "pet_rehoming",
            "sudden_calm_after_hopelessness",
        ),
        # Genuinely unweighted, which is what the line below used to claim
        # while carrying 0.95 — nearly the maximum, enough for one
        # leave-taking observation to push the psychosocial index toward a
        # threshold by itself. The intent was always that this domain is one
        # leg of the N4 convergence rule and nothing else, so it contributes
        # to neither the numerator nor the denominator of any index.
        # `has_leave_taking_signal` does not read the weight, so the
        # convergence rule is unaffected.
        weight=0.0,
        examples=(
            "le he dado mi guitarra a mi sobrino",
            "quería darte las gracias por todo, de verdad",
            "he dejado los papeles ordenados por si acaso",
            "de repente estoy tranquilo, ya no me agobia nada",
        ),
    ),
    Domain(
        key="substance_environment",
        label="Entorno de consumo",
        group=GROUP_SIGNALS,
        meaning=(
            "Entorno que sostiene o dispara el consumo: disponibilidad, contactos, apps, "
            "presión de grupo, fechas señaladas, aniversarios."
        ),
        session_question="¿Con quién y en qué situaciones aparece el consumo últimamente?",
        categories=("using_environment_exposure", "environment_protective"),
        weight=0.85,
        relapse_weight=1.0,
        examples=("me han vuelto a escribir del grupo", "este finde hay fiesta y no sé decir que no"),
    ),
    Domain(
        key="means_access",
        label="Acceso a medios lesivos",
        group=GROUP_SIGNALS,
        meaning=(
            "Disponibilidad referida de medios para hacerse daño, y su restricción. No "
            "puntúa en ningún índice: se trata como hecho a confirmar, no como contexto."
        ),
        session_question="¿Tiene en casa algo con lo que podría hacerse daño? ¿Podemos ponerlo lejos?",
        categories=("means_access_reported", "means_restricted"),
        weight=0.95,
        examples=("tengo las pastillas de mi madre en el cajón",),
    ),
)

DOMAIN_KEYS: tuple[str, ...] = tuple(domain.key for domain in DOMAINS)
DOMAIN_BY_KEY: dict[str, Domain] = {domain.key: domain for domain in DOMAINS}

# The prompt enum is derived, never re-typed. This is the invariant the whole
# module exists for.
DOMAIN_CATEGORIES: dict[str, tuple[str, ...]] = {domain.key: domain.categories for domain in DOMAINS}
CATEGORY_KEYS: tuple[str, ...] = tuple(
    category for domain in DOMAINS for category in domain.categories
)
CATEGORY_DOMAIN: dict[str, str] = {
    category: domain.key for domain in DOMAINS for category in domain.categories
}

DOMAIN_LABELS: dict[str, str] = {domain.key: domain.label for domain in DOMAINS}
DOMAIN_WEIGHTS: dict[str, float] = {domain.key: domain.weight for domain in DOMAINS}

CATEGORY_LABELS: dict[str, str] = {
    # housing
    "housing_stable": "Vivienda estable",
    "housing_precarious": "Vivienda precaria",
    "housing_temporary": "Alojamiento temporal",
    "housing_homeless": "Sin hogar",
    "housing_eviction_risk": "Riesgo de perder la vivienda",
    "housing_institution": "Recurso residencial / institución",
    # cohabitation
    "lives_alone": "Vive solo/a",
    "lives_with_family": "Vive con familia",
    "lives_with_partner": "Vive en pareja",
    "lives_shared": "Vivienda compartida",
    "lives_with_people_who_use": "Convive con personas que consumen",
    "cohabitation_conflict": "Conflicto de convivencia",
    # social support
    "support_strong": "Apoyo social sólido",
    "support_limited": "Apoyo social limitado",
    "support_absent": "Sin apoyo social",
    "isolation_increasing": "Aislamiento creciente",
    "new_supportive_relationship": "Nuevo vínculo de apoyo",
    # family
    "family_supportive": "Familia que apoya",
    "family_conflict": "Conflicto familiar",
    "family_estranged": "Ruptura familiar",
    "family_caregiving_burden": "Sobrecarga de cuidados",
    "family_unaware": "Familia no informada",
    # economic
    "income_stable": "Ingresos estables",
    "income_precarious": "Ingresos precarios",
    "debt": "Deudas",
    "food_insecurity": "Inseguridad alimentaria",
    "benefit_loss": "Pérdida de ayuda o prestación",
    "financial_dependence": "Dependencia económica",
    # occupation
    "employed": "Con empleo",
    "unemployed": "Sin empleo",
    "job_loss": "Pérdida de empleo",
    "studying": "Estudiando",
    "sick_leave": "Baja laboral",
    "work_stress": "Estrés laboral",
    # legal
    "legal_proceedings": "Procedimiento legal abierto",
    "legal_none": "Sin asuntos legales",
    # healthcare access
    "treatment_engaged": "Vinculado al tratamiento",
    "treatment_dropout": "Abandono de tratamiento",
    "medication_access_problem": "Problema de acceso a medicación",
    "appointment_barrier": "Barrera para acudir a citas",
    # stigma
    "stigma_experienced": "Estigma vivido",
    "disclosure_fear": "Miedo a revelar su situación",
    # loss
    "bereavement": "Duelo",
    "breakup": "Ruptura de pareja",
    "relationship_loss": "Pérdida de una relación",
    "pet_loss": "Pérdida de un animal de compañía",
    "other_loss": "Otra pérdida",
    # connectedness
    "meaningful_activity": "Actividad con sentido",
    "community_belonging": "Pertenencia a un grupo",
    "future_plans": "Planes de futuro",
    "loss_of_routine": "Pérdida de rutina",
    # interpersonal — perceived burdensomeness
    "burden_severe": "Se vive como una carga grave para los suyos",
    "burden_expressed": "Expresa sentirse una carga",
    "burden_absent": "No se vive como una carga",
    # interpersonal — thwarted belongingness
    "belonging_absent": "No siente pertenecer a ningún sitio",
    "belonging_partial": "Pertenencia frágil o parcial",
    "belonging_present": "Siente que pertenece y le importa a alguien",
    # leave-taking
    "giving_possessions_away": "Reparte o regala pertenencias",
    "affairs_in_order": "Deja asuntos en orden",
    "goodbye_message": "Mensaje de cierre o agradecimiento",
    "pet_rehoming": "Busca acomodo para su animal",
    "sudden_calm_after_hopelessness": "Calma repentina tras desesperanza",
    # means / environment
    "means_access_reported": "Acceso referido a medios lesivos",
    "means_restricted": "Medios restringidos",
    "using_environment_exposure": "Exposición a entorno de consumo",
    "environment_protective": "Entorno protector",
}

# ---------------------------------------------------- domain sets by role ---
# The two IPTS constructs plus withdrawal. Their convergence is the point.
INTERPERSONAL_DOMAINS: frozenset[str] = frozenset(
    {"perceived_burden", "thwarted_belonging", "connectedness"}
)

LEAVE_TAKING_DOMAIN = "leave_taking"

# Categories that constitute an acute adverse change when freshly reported.
# These are the "small" sentences that precede crises.
ACUTE_CHANGE_CATEGORIES: frozenset[str] = frozenset(
    {
        "housing_homeless",
        "housing_eviction_risk",
        "housing_temporary",
        "housing_precarious",
        "lives_with_people_who_use",
        "cohabitation_conflict",
        "support_absent",
        "isolation_increasing",
        "family_conflict",
        "family_estranged",
        "benefit_loss",
        "food_insecurity",
        "debt",
        "job_loss",
        "bereavement",
        "breakup",
        "relationship_loss",
        "pet_loss",
        "other_loss",
        "loss_of_routine",
        "treatment_dropout",
        "medication_access_problem",
        "using_environment_exposure",
        "means_access_reported",
    }
)

# Categories that are protective by definition. Applied in
# `psychosocial._coherent`: a model calling "support_strong" adverse has
# misread the observation, and an adverse reading is the one that moves an
# index, so the observation is dropped rather than trusted. This comment
# described a check that did not exist until it did.
PROTECTIVE_CATEGORIES: frozenset[str] = frozenset(
    {
        "housing_stable",
        "support_strong",
        "new_supportive_relationship",
        "family_supportive",
        "income_stable",
        "employed",
        "studying",
        "legal_none",
        "treatment_engaged",
        "meaningful_activity",
        "community_belonging",
        "future_plans",
        "burden_absent",
        "belonging_present",
        "means_restricted",
        "environment_protective",
    }
)


def domain_label(key: str) -> str:
    domain = DOMAIN_BY_KEY.get(key)
    return domain.label if domain else key


def category_label(key: str) -> str:
    return CATEGORY_LABELS.get(key, key)


def group_label(key: str) -> str:
    return GROUP_LABELS.get(key, key)


def session_question(key: str) -> str | None:
    domain = DOMAIN_BY_KEY.get(key)
    return domain.session_question if domain else None


def risk_value(valence: str, intensity: float) -> float:
    """Position one observation on the shared 0-1 adversity scale.

    Protective readings are 0, adversity is the intensity the model gave,
    and a neutral, purely descriptive reading sits at a fixed low value —
    it is a baseline, not an absence of risk.
    """
    if valence == VALENCE_PROTECTIVE:
        return 0.0
    if valence == VALENCE_NEUTRAL:
        return NEUTRAL_RISK_VALUE
    return max(0.0, min(1.0, float(intensity)))
