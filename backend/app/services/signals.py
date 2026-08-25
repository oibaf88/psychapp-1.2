"""
Refuting a linguistic inference.

Agent 4's psychosocial observations have had an adjudication path since they
existed: a therapist can confirm or refute one, and the deterministic index
changes accordingly. The linguistic signals never had one. A wrong flag —
the kind that produced an emergency alert for someone announcing they had
decided to change their life — kept firing its rule on every evaluation for
the whole freshness window, and there was no way to say it was wrong.

This is the mirror of `psychosocial.adjudicate`, and it needs no change to
the risk engine: every query the engine makes already filters
`AlfaSignal.is_active`, so deactivating the row removes it from the next
evaluation by construction.

It is also what finally makes a `correction` fact do something. A
`ConfirmedFact` of category `correction` was stored and then ignored by the
engine, which reads only `N4_FACT_CATEGORIES` and `N3_FACT_CATEGORIES`.
Refuting writes one and links it through `AlfaSignal.superseded_by_fact` — a
column that has existed since the first migration and was never populated —
so the correction is the record of *why* the inference stopped counting,
which is exactly what the fact/inference wall is for: a human statement
overriding a model's reading, without either one being deleted.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import Session

from app.models import AlfaSignal, ConfirmedFact

logger = logging.getLogger("psychapp.signals")

# Only inferences may be refuted. A structural_score is arithmetic over
# stored check-ins, not a reading that can be wrong about the person: if the
# numbers are wrong, the check-ins are what needs correcting.
REFUTABLE_SIGNAL_TYPES = frozenset({"linguistic_analysis"})

MAX_REASON_CHARS = 1000


class SignalNotRefutable(ValueError):
    """This signal is not a model inference a human can overrule."""


@dataclass(frozen=True)
class Refutation:
    signal: AlfaSignal
    fact: ConfirmedFact


def refute(
    db: Session,
    signal: AlfaSignal,
    *,
    actor_id,
    actor_role: str,
    reason: str,
) -> Refutation:
    """Mark one linguistic signal as wrong, and record who said so and why.

    The signal row is kept. Deleting it would take the analysis lineage with
    it — the trace, the source text, the alert it produced — and the reason
    this exists at all is that a clinician needs to be able to review a
    decision that was made badly.
    """
    if signal.signal_type not in REFUTABLE_SIGNAL_TYPES:
        raise SignalNotRefutable(
            f"Only a model inference can be refuted, not {signal.signal_type}"
        )

    text = (reason or "").strip()[:MAX_REASON_CHARS]
    if not text:
        raise ValueError("A refutation needs a reason: it is the clinical record of the correction")

    fact = ConfirmedFact(
        user_id=signal.user_id,
        category="correction",
        content=text,
        declared_by="professional" if actor_role != "patient" else "user",
    )
    db.add(fact)
    db.flush()

    signal.is_active = False
    signal.superseded_by_fact = fact.id
    db.commit()
    db.refresh(signal)
    db.refresh(fact)
    logger.info("Linguistic signal refuted by a clinician; it will not be evaluated again")
    return Refutation(signal=signal, fact=fact)


def restore(db: Session, signal: AlfaSignal, *, actor_id) -> AlfaSignal:
    """Undo a refutation made in error.

    The `correction` fact stays: it is a statement someone made, and the
    wall does not let the system retract those. Only the link is cleared,
    so the signal counts again from the next evaluation.
    """
    signal.is_active = True
    signal.superseded_by_fact = None
    db.commit()
    db.refresh(signal)
    return signal
