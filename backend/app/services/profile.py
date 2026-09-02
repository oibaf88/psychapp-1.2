"""
What is known about one patient, and what counts as normal *for them*.

Why this exists
---------------
The analytic layer judged every patient against the same constants.
`rumination_score > 0.60` was the same threshold for someone who writes in
long anxious spirals and for someone who answers in four words. A single
message was read with no idea who wrote it, what they had said last week,
or how they usually sound. That is how "he decidido cambiar de vida" became
a crisis: with no history, a model cannot tell a turning point from a
euphemism for closure, and with no personal baseline, an ordinary score for
that person looks like a spike.

Two things live here, and they answer different questions:

**The linguistic baseline** answers "is this unusual for them?" It is the
mean and standard deviation of this patient's own scores over their own
history — the same idea `baseline.py` already applies to the four check-in
variables, extended to the axis where it was missing. It is recomputed from
the stored signals rather than accumulated incrementally: a running total
cannot be re-derived after a signal is refuted, and refuting signals is
exactly what Fase 4 adds.

**The portrait and the open threads** answer "who is this?" They are prose
and a short agenda, maintained by the analyser and correctable by a
clinician.

Where this sits
---------------
On the inference side of the fact/inference wall, without exception.
Nothing here is a ConfirmedFact. Nothing here decides an alert level: the
deterministic engine reads the baseline only to ask whether a reading is
unusual for this person, and a patient with no profile is evaluated exactly
as they were before this module existed. That fallback is not a
convenience, it is the safety property — a new patient must not become
un-assessable because the system has not met them yet.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.models import AlfaSignal, PatientProfile
from app.services.baseline import _mean_std

logger = logging.getLogger("psychapp.profile")

# The linguistic axes worth normalising per person. Booleans are excluded on
# purpose: "did they express direct ideation" is not more or less true
# relative to a personal average, and treating it that way would be a way of
# getting used to someone's ideation.
LINGUISTIC_VARIABLES = (
    "rumination_score",
    "negative_valence",
    "urgency_level",
    "ambivalence",
)

# Below this a personal baseline says more about the sample than the person,
# and the absolute constants stay in charge. Deliberately higher than the 5
# check-ins `baseline.py` needs: a check-in is a number on a fixed scale,
# where a linguistic score is a model's reading of one text.
MIN_SIGNALS_FOR_LINGUISTIC_BASELINE = 12

# How far back the baseline looks. Long enough to describe a person, short
# enough that a year-old way of writing does not define them today.
LINGUISTIC_BASELINE_WINDOW_DAYS = 120

# Recompute at most this often. The baseline moves slowly; recomputing it on
# every message would be a query per message for a value that barely changes.
LINGUISTIC_BASELINE_MAX_AGE_HOURS = 12

# A standard deviation below this is noise, not spread — dividing by it
# turns a rounding difference into a three-sigma event.
MIN_STD_FOR_DEVIATION = 0.02

MAX_PORTRAIT_CHARS = 1500
MAX_OPEN_THREADS = 8
MAX_THREAD_TOPIC_CHARS = 120
MAX_THREAD_NOTE_CHARS = 300


@dataclass(frozen=True)
class Deviation:
    """How far one reading sits from this person's own normal."""

    value: float
    z: float | None
    mean: float | None
    std: float | None
    n: int
    # True when there is no usable baseline, so the caller must fall back to
    # the absolute constant rather than treat 0.0 as "perfectly normal".
    insufficient_data: bool


def get_or_create(db: Session, user_id) -> PatientProfile:
    profile = db.query(PatientProfile).filter(PatientProfile.user_id == user_id).first()
    if profile is not None:
        return profile
    profile = PatientProfile(
        id=uuid.uuid4(),
        user_id=user_id,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db.add(profile)
    db.commit()
    db.refresh(profile)
    return profile


def get(db: Session, user_id) -> PatientProfile | None:
    """Read without creating. The engine uses this: evaluating a patient
    must never be the thing that writes their first profile row."""
    return db.query(PatientProfile).filter(PatientProfile.user_id == user_id).first()


# --------------------------------------------------------------- baseline ---
def compute_linguistic_stats(db: Session, user_id) -> tuple[dict, int]:
    """Mean and standard deviation of this patient's own linguistic scores.

    Only active signals count. A signal a therapist has marked as wrong must
    not go on defining what is normal for the person it was wrong about.
    """
    since = datetime.utcnow() - timedelta(days=LINGUISTIC_BASELINE_WINDOW_DAYS)
    signals = (
        db.query(AlfaSignal)
        .filter(
            AlfaSignal.user_id == user_id,
            AlfaSignal.signal_type == "linguistic_analysis",
            AlfaSignal.is_active == True,  # noqa: E712
            AlfaSignal.timestamp >= since,
        )
        .all()
    )

    collected: dict[str, list[float]] = {var: [] for var in LINGUISTIC_VARIABLES}
    for signal in signals:
        value = signal.value if isinstance(signal.value, dict) else {}
        for var in LINGUISTIC_VARIABLES:
            reading = value.get(var)
            if isinstance(reading, (int, float)) and not isinstance(reading, bool):
                collected[var].append(float(reading))

    # One n for the profile, taken from the axis with the fewest readings:
    # claiming a baseline that only some axes actually have would let the
    # engine compare against a mean derived from three data points.
    counts = [len(v) for v in collected.values()]
    n = min(counts) if counts else 0

    stats: dict[str, dict[str, float]] = {}
    for var, values in collected.items():
        if not values:
            continue
        mean, std = _mean_std(values)
        stats[var] = {"mean": round(mean, 4), "std": round(std, 4), "n": len(values)}
    return stats, n


def refresh_linguistic_baseline(db: Session, user_id, *, force: bool = False) -> PatientProfile:
    """Recompute the personal baseline if it is stale.

    Unlike the check-in baseline this was modelled on, there is no "active"
    row that gets created once and then never revisited. It is recomputed
    from the current signals every time it ages out, so a person whose way
    of writing changes is eventually described by how they write now.
    """
    profile = get_or_create(db, user_id)
    if not force and profile.linguistic_baseline_updated_at is not None:
        age = datetime.utcnow() - profile.linguistic_baseline_updated_at
        if age < timedelta(hours=LINGUISTIC_BASELINE_MAX_AGE_HOURS):
            return profile

    stats, n = compute_linguistic_stats(db, user_id)
    profile.linguistic_baseline = stats or None
    profile.linguistic_baseline_n = n
    profile.linguistic_baseline_updated_at = datetime.utcnow()
    profile.updated_at = datetime.utcnow()
    db.add(profile)
    db.commit()
    db.refresh(profile)
    return profile


def deviation(profile: PatientProfile | None, variable: str, value) -> Deviation:
    """Where one reading sits relative to this person's own normal.

    ``insufficient_data`` is the important field. It is set whenever there
    is no usable baseline — no profile, too few signals, an axis that was
    never scored, or a standard deviation too small to divide by — and the
    caller must then fall back to the absolute threshold. A z of 0.0 means
    "exactly average for them", which is a very different statement from
    "we do not know them yet", and conflating the two would silently
    disarm every relative rule for new patients.
    """
    numeric = float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None
    if numeric is None:
        return Deviation(value=0.0, z=None, mean=None, std=None, n=0, insufficient_data=True)

    if profile is None or profile.linguistic_baseline_n < MIN_SIGNALS_FOR_LINGUISTIC_BASELINE:
        return Deviation(value=numeric, z=None, mean=None, std=None, n=0, insufficient_data=True)

    stats = (profile.linguistic_baseline or {}).get(variable)
    if not isinstance(stats, dict):
        return Deviation(value=numeric, z=None, mean=None, std=None, n=0, insufficient_data=True)

    mean = stats.get("mean")
    std = stats.get("std")
    n = int(stats.get("n") or 0)
    if not isinstance(mean, (int, float)) or not isinstance(std, (int, float)):
        return Deviation(value=numeric, z=None, mean=None, std=None, n=n, insufficient_data=True)
    if std < MIN_STD_FOR_DEVIATION:
        # Someone who always scores the same has no spread to measure
        # against. Saying so is honest; dividing by 0.001 is not.
        return Deviation(value=numeric, z=None, mean=float(mean), std=float(std), n=n, insufficient_data=True)

    return Deviation(
        value=numeric,
        z=round((numeric - float(mean)) / float(std), 3),
        mean=float(mean),
        std=float(std),
        n=n,
        insufficient_data=False,
    )


# ------------------------------------------------------- portrait & agenda ---
def _clean_threads(raw) -> list[dict]:
    if not isinstance(raw, list):
        return []
    cleaned: list[dict] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        topic = str(item.get("topic") or "").strip()[:MAX_THREAD_TOPIC_CHARS]
        if not topic or topic.lower() in seen:
            continue
        seen.add(topic.lower())
        cleaned.append(
            {
                "topic": topic,
                "note": str(item.get("note") or "").strip()[:MAX_THREAD_NOTE_CHARS],
                "opened_at": str(item.get("opened_at") or datetime.utcnow().date().isoformat())[:32],
                "source": str(item.get("source") or "analyzer")[:32],
            }
        )
    return cleaned[:MAX_OPEN_THREADS]


def apply_analyzer_update(db: Session, user_id, block) -> PatientProfile | None:
    """Fold the analyser's view of the person back into the profile.

    Returns None when there was nothing usable, which is the common case:
    most messages do not change who someone is, and the prompt asks the
    model to leave the portrait alone unless it learned something.

    Never raises. A malformed update is worth losing; the analysis it
    arrived with is not.
    """
    if not isinstance(block, dict):
        return None
    portrait = block.get("portrait")
    threads = _clean_threads(block.get("open_threads"))
    portrait_text = str(portrait).strip()[:MAX_PORTRAIT_CHARS] if isinstance(portrait, str) else ""

    if not portrait_text and not threads:
        return None

    try:
        profile = get_or_create(db, user_id)
        now = datetime.utcnow()
        if portrait_text and portrait_text != (profile.portrait or ""):
            profile.previous_portrait = profile.portrait
            profile.portrait = portrait_text
            profile.portrait_version = (profile.portrait_version or 0) + 1
            profile.portrait_updated_at = now
            # The model rewrote it, so it is no longer the clinician's text.
            profile.portrait_edited_by = None
        if threads:
            profile.open_threads = threads
        profile.updated_at = now
        db.add(profile)
        db.commit()
        db.refresh(profile)
        return profile
    except Exception:  # noqa: BLE001
        db.rollback()
        logger.warning("Profile update discarded; the analysis it came with is kept")
        return None


def set_portrait_by_clinician(db: Session, user_id, *, portrait: str, actor_id) -> PatientProfile:
    """A therapist correcting the portrait.

    Kept separate from the analyser path so `portrait_edited_by` records
    that a person wrote this. The analyser is then told it may add to a
    hand-edited portrait but never contradict it — the same asymmetry as
    the fact/inference wall, applied to prose.
    """
    profile = get_or_create(db, user_id)
    text = (portrait or "").strip()[:MAX_PORTRAIT_CHARS]
    if text != (profile.portrait or ""):
        profile.previous_portrait = profile.portrait
        profile.portrait = text or None
        profile.portrait_version = (profile.portrait_version or 0) + 1
        profile.portrait_updated_at = datetime.utcnow()
    profile.portrait_edited_by = actor_id
    profile.updated_at = datetime.utcnow()
    db.add(profile)
    db.commit()
    db.refresh(profile)
    return profile


def set_open_threads(db: Session, user_id, threads: list) -> PatientProfile:
    """A therapist setting what to explore next.

    Marked `source: clinician` so the analyser's own additions stay
    distinguishable from what a professional asked for.
    """
    profile = get_or_create(db, user_id)
    profile.open_threads = _clean_threads(
        [{**t, "source": "clinician"} if isinstance(t, dict) else t for t in (threads or [])]
    )
    profile.updated_at = datetime.utcnow()
    db.add(profile)
    db.commit()
    db.refresh(profile)
    return profile


def as_dict(profile: PatientProfile | None) -> dict:
    """What the therapist's panel shows. Nothing here is a decision."""
    if profile is None:
        return {
            "portrait": None,
            "previous_portrait": None,
            "portrait_version": 0,
            "portrait_updated_at": None,
            "portrait_edited_by_clinician": False,
            "open_threads": [],
            "linguistic_baseline": None,
            "linguistic_baseline_n": 0,
            "baseline_is_usable": False,
            "minimum_signals_for_baseline": MIN_SIGNALS_FOR_LINGUISTIC_BASELINE,
        }
    n = profile.linguistic_baseline_n or 0
    return {
        "portrait": profile.portrait,
        "previous_portrait": profile.previous_portrait,
        "portrait_version": profile.portrait_version or 0,
        "portrait_updated_at": profile.portrait_updated_at.isoformat()
        if profile.portrait_updated_at
        else None,
        "portrait_edited_by_clinician": profile.portrait_edited_by is not None,
        "open_threads": profile.open_threads or [],
        "linguistic_baseline": profile.linguistic_baseline,
        "linguistic_baseline_n": n,
        # Whether the engine is actually comparing against this person yet,
        # which is not the same as whether numbers exist to show.
        "baseline_is_usable": n >= MIN_SIGNALS_FOR_LINGUISTIC_BASELINE,
        "minimum_signals_for_baseline": MIN_SIGNALS_FOR_LINGUISTIC_BASELINE,
    }




# ------------------------------------------------------------ for the LLM ---
def analyzer_context_block(profile: PatientProfile | None) -> str:
    """What the analyser is told about the person before it reads the text.

    Empty string when there is nothing to say. The prompt then contains no
    profile section at all, rather than a section announcing its own
    emptiness — which reads to a model as a fact about the patient.
    """
    if profile is None:
        return ""

    parts: list[str] = []
    if profile.portrait:
        origin = "corregido por el profesional" if profile.portrait_edited_by else "acumulado por el sistema"
        parts.append(f"### Quién es esta persona ({origin})\n{profile.portrait}")

    baseline = profile.linguistic_baseline or {}
    if baseline and profile.linguistic_baseline_n >= MIN_SIGNALS_FOR_LINGUISTIC_BASELINE:
        rows = "\n".join(
            f"- {var}: media habitual {stats['mean']:.2f} (desviación {stats['std']:.2f})"
            for var, stats in baseline.items()
            if isinstance(stats, dict) and "mean" in stats and "std" in stats
        )
        if rows:
            parts.append(
                "### Cómo puntúa habitualmente esta persona\n"
                f"Sobre {profile.linguistic_baseline_n} textos anteriores suyos:\n{rows}\n"
                "Compara el texto de hoy con ESTOS valores, no con una idea general de "
                "lo que es alto o bajo. Un 0,7 de rumiación en quien suele estar en 0,7 "
                "no es una señal; en quien suele estar en 0,2, sí."
            )

    if profile.open_threads:
        rows = "\n".join(
            f"- {t.get('topic')}" + (f": {t.get('note')}" if t.get("note") else "")
            for t in profile.open_threads
            if isinstance(t, dict) and t.get("topic")
        )
        if rows:
            parts.append(f"### Temas abiertos de sesiones anteriores\n{rows}")

    if not parts:
        return ""
    return (
        "\n\n═══ LO QUE YA SE SABE DE ESTA PERSONA ═══\n"
        "Contexto de solo lectura. No lo repitas ni lo cites; úsalo para juzgar "
        "el texto de hoy relativo a quien lo escribe.\n\n" + "\n\n".join(parts) + "\n═══ FIN DEL CONTEXTO ═══\n"
    )
