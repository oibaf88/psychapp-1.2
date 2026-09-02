from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from datetime import datetime, timedelta
import uuid

from fastapi import Body

from app.config import get_settings
from app.models import Consent, SafetyPlan, User, PasswordResetToken
from app.schemas import LoginRequest, Token, UserCreate, UserOut, PasswordResetRequest, PasswordResetConfirm, GoogleLoginRequest
from app.security import create_access_token, get_current_user, hash_password, verify_password
from app.services import audit

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

settings = get_settings()

PUBLIC_SIGNUP_ROLE = "patient"


@router.post("/register", response_model=Token, status_code=status.HTTP_201_CREATED)
def register(payload: UserCreate, db: Session = Depends(get_db)):
    existing = db.query(User).filter(User.email == payload.email).first()
    if existing:
        raise HTTPException(status_code=400, detail="Email already registered")

    user = User(
        email=payload.email,
        hashed_password=hash_password(payload.password),
        display_name=payload.display_name,
        role=PUBLIC_SIGNUP_ROLE,
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


@router.post("/password-reset-request")
def password_reset_request(payload: PasswordResetRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == payload.email).first()
    if not user:
        # Don't reveal if user exists or not
        return {"message": "If the email exists, a reset link has been sent."}

    # Generate token
    token = str(uuid.uuid4())
    expires_at = datetime.utcnow() + timedelta(hours=1)

    reset_token = PasswordResetToken(
        user_id=user.id,
        token=token,
        expires_at=expires_at
    )
    db.add(reset_token)
    db.commit()

    # In a real app, send an email here with the token.
    # For now, we will just log it in the audit log or pretend it's sent.
    audit.log(db, actor_id=user.id, actor_role=user.role, action="password_reset_requested", entity_type="user", entity_id=user.id)

    response = {"message": "If the email exists, a reset link has been sent."}
    # Returning the token to the caller lets anyone who knows an email
    # address take over that account, so it is confined to local/dev where
    # there is no mail transport to pick the token up from.
    if not settings.is_production:
        response["dev_token"] = token
    return response


@router.post("/password-reset-confirm")
def password_reset_confirm(payload: PasswordResetConfirm, db: Session = Depends(get_db)):
    reset_token = db.query(PasswordResetToken).filter(
        PasswordResetToken.token == payload.token,
        PasswordResetToken.is_used == False,
        PasswordResetToken.expires_at > datetime.utcnow()
    ).first()

    if not reset_token:
        raise HTTPException(status_code=400, detail="Invalid or expired reset token")

    user = db.query(User).filter(User.id == reset_token.user_id).first()
    if not user:
        raise HTTPException(status_code=400, detail="User not found")

    user.hashed_password = hash_password(payload.new_password)
    reset_token.is_used = True
    db.commit()

    audit.log(db, actor_id=user.id, actor_role=user.role, action="password_reset_completed", entity_type="user", entity_id=user.id)

    return {"message": "Password has been reset successfully."}

@router.post("/google-login", response_model=Token)
def google_login(payload: GoogleLoginRequest, db: Session = Depends(get_db)):
    # In a real app you'd verify the token with Google using something like google-auth
    # from google.oauth2 import id_token
    # from google.auth.transport import requests
    # try:
    #     idinfo = id_token.verify_oauth2_token(payload.id_token, requests.Request(), GOOGLE_CLIENT_ID)
    # except ValueError:
    #     raise HTTPException(status_code=401, detail="Invalid token")
    # email = idinfo['email']
    # display_name = idinfo.get('name', email)

    # This handler does NOT verify the token with Google — it treats the
    # client-supplied id_token as the user's email, so enabling it lets
    # anyone obtain a session for any account. It stays disabled until
    # real verification is implemented.
    if settings.is_production or not settings.allow_mock_google_login:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail=(
                "Google login is not available: the handler does not verify tokens "
                "with Google yet. Set ALLOW_MOCK_GOOGLE_LOGIN=true only on a trusted "
                "local machine."
            ),
        )

    if not payload.id_token:
        raise HTTPException(status_code=401, detail="Missing token")

    # We will simulate decoding by just accepting the token as the email (FOR DEMO ONLY)
    email = payload.id_token
    display_name = payload.id_token.split('@')[0] if '@' in payload.id_token else payload.id_token

    user = db.query(User).filter(User.email == email).first()

    if not user:
        user = User(
            email=email,
            # Generate a random password since they login with google
            hashed_password=hash_password(str(uuid.uuid4())),
            display_name=display_name,
            role=PUBLIC_SIGNUP_ROLE,
        )
        db.add(user)
        db.commit()
        db.refresh(user)

        db.add(Consent(user_id=user.id, consent_type="data_processing", granted=True))
        if user.role == "patient":
            db.add(SafetyPlan(user_id=user.id))
        db.commit()
        audit.log(db, actor_id=user.id, actor_role=user.role, action="register_google", entity_type="user", entity_id=user.id)
    else:
        if not user.is_active:
            raise HTTPException(status_code=403, detail="Account disabled")

    audit.log(db, actor_id=user.id, actor_role=user.role, action="login_google", entity_type="user", entity_id=user.id)

    token = create_access_token(user.id, user.role)
    return Token(access_token=token, user=UserOut.model_validate(user))
