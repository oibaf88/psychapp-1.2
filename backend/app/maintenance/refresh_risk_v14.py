"""Append one v1.4 evaluation per active patient, preserving all history.

Run with the configured application database:
    python -m app.maintenance.refresh_risk_v14          # rollback-only preview
    python -m app.maintenance.refresh_risk_v14 --apply  # explicit maintenance

Never invokes an LLM, sends email, or creates notification deliveries. Existing
clinical alerts are not closed. Newly warranted alerts remain in the dashboard.
Per-patient atomic transactions and audit markers make an apply run resumable.
Output contains aggregate counts only, never patient identifiers or text.
"""
import argparse
import hashlib
import json
import logging
import os

from sqlalchemy import exists, or_, text
from sqlalchemy.orm import Session

from app.database import engine
from app.models import AlfaSignal, AuditLog, CheckIn, RiskAssessment, User
from app.schemas import DailyStatisticsOut
from app.services.daily_statistics import load_daily_statistics
from app.services.risk_engine import MODEL_VERSION, run_and_persist


ACTION = "maintenance.risk_v14_structural_v2"


def run_configured_startup_refresh() -> None:
    """Explicit operator switch, off by default and unavailable to HTTP callers.

    Set RISK_V14_MAINTENANCE to preview or apply for a controlled deploy, then
    restore off. Apply first performs a complete rollback-only preview.
    """
    mode = os.environ.get("RISK_V14_MAINTENANCE", "off").strip().lower()
    if mode == "off":
        return
    if mode not in {"preview", "apply"}:
        raise ValueError("RISK_V14_MAINTENANCE must be off, preview or apply")
    logger = logging.getLogger("psychapp.maintenance")
    logger.info("Risk v1.4 maintenance preview: %s", json.dumps(refresh_all()))
    if mode == "apply":
        logger.info("Risk v1.4 maintenance result: %s", json.dumps(refresh_all(apply=True)))


def refresh_patient(db: Session, patient_id) -> dict:
    """Caller owns the outer transaction; internal service commits are isolated."""
    if db.bind.dialect.name == "postgresql":
        lock_id = int.from_bytes(hashlib.sha256(f"{ACTION}:{patient_id}".encode()).digest()[:8], "big") & ((1 << 63) - 1)
        db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock_id})
    already_done = db.query(AuditLog.id).filter(
        AuditLog.action == ACTION, AuditLog.entity_id == str(patient_id),
    ).first()
    if already_done:
        return {"skipped": True}
    statistics = load_daily_statistics(db, patient_id, window_days=90)
    DailyStatisticsOut.model_validate(statistics)
    assessment = run_and_persist(db, patient_id, notify=False)
    values = assessment.input_signals or {}
    db.add(AuditLog(
        actor_role="system", action=ACTION, entity_type="user", entity_id=str(patient_id),
        extra={"model_version": MODEL_VERSION, "assessment_id": str(assessment.id),
               "notifications_enabled": False, "history_preserved": True,
               "structural_calculation_version": values.get("structural_calculation_version")},
    ))
    db.flush()
    return {"skipped": False, "level": assessment.alert_level,
            "score": values.get("structural_score"),
            "statistics_days": len(statistics["daily"]),
            "statistics_variables": len(statistics["variables"])}


def refresh_all(*, apply: bool = False) -> dict:
    with Session(engine) as db:
        patients = [row.id for row in db.query(User.id).filter(
            User.role == "patient", User.is_active.is_(True),
            or_(exists().where(CheckIn.user_id == User.id),
                exists().where(RiskAssessment.user_id == User.id),
                exists().where(AlfaSignal.user_id == User.id)),
        ).all()]
    result = {"mode": "apply" if apply else "preview_rolled_back", "eligible": len(patients),
              "refreshed": 0, "skipped": 0, "scores_available": 0, "zero_scores": 0,
              "levels": {}, "statistics_days": 0, "statistics_variables": 0,
              "external_notifications": 0, "history_preserved": True}
    for patient_id in patients:
        with engine.connect() as connection:
            outer = connection.begin()
            try:
                with Session(bind=connection, join_transaction_mode="rollback_only") as db:
                    outcome = refresh_patient(db, patient_id)
                    if outcome["skipped"]:
                        result["skipped"] += 1
                    else:
                        result["refreshed"] += 1
                        level = str(outcome["level"])
                        result["levels"][level] = result["levels"].get(level, 0) + 1
                        score = outcome["score"]
                        result["scores_available"] += int(score is not None)
                        result["zero_scores"] += int(score == 0)
                        result["statistics_days"] += outcome["statistics_days"]
                        result["statistics_variables"] = max(result["statistics_variables"], outcome["statistics_variables"])
                    if apply:
                        outer.commit()
                    else:
                        outer.rollback()
            except BaseException:
                if outer.is_active:
                    outer.rollback()
                raise
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Commit the append-only correction")
    args = parser.parse_args()
    print(json.dumps(refresh_all(apply=args.apply), ensure_ascii=False))
