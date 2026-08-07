from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Consent, SafetyPlan, User
from app.schemas import LoginRequest, Token, UserCreate, UserOut
from app.security import create_access_token, get_current_user, hash_password, verify_password
from app.services import audit

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

VALID_ROLES = {"patient", "therapist", "supervisor", "admin_clinical"}


@router.post("/register", response_model=Token, status_code=status.HTTP_201_CREATED)
def register(payload: UserCreate, db: Session = Depends(get_db)):
    if payload.role not in VALID_ROLES:
        raise HTTPException(status_code=400, detail=f"role must be one of {sorted(VALID_ROLES)}")

    existing = db.query(User).filter(User.email == payload.email).first()
    if existing:
        raise HTTPException(status_code=400, detail="Email already registered")

    user = User(
        email=payload.email,
        hashed_password=hash_password(payload.password),
        display_name=payload.display_name,
        role=payload.role,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    # Baseline consent for data processing is required to use the app at all
    # (doc 1: "consentimiento granular, informado, trazable y revocable").
    db.add(Consent(user_id=user.id, consent_type="data_processing", granted=True))
    if user.role == "patient":
        db.add(SafetyPlan(user_id=user.id))
    db.commit()

    audit.log(db, actor_id=user.id, actor_role=user.role, action="register", entity_type="user", entity_id=user.id)

    token = create_access_token(user.id, user.role)
    return Token(access_token=token, user=UserOut.model_validate(user))


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)):
    return UserOut.model_validate(user)


@router.post("/login", response_model=Token)
def login(payload: LoginRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == payload.email).first()
    if not user or not verify_password(payload.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="Account disabled")

    audit.log(db, actor_id=user.id, actor_role=user.role, action="login", entity_type="user", entity_id=user.id)

    token = create_access_token(user.id, user.role)
    return Token(access_token=token, user=UserOut.model_validate(user))
