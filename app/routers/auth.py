from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app import models
from app.activity_log import log_activity
from app.asset_version import asset_version
from app.auth import get_current_user
from app.database import get_db
from app.security import hash_password, verify_password

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


LOGIN_INFO = {"restaurado": "Respaldo restaurado. Ingresa de nuevo con tu usuario."}


@router.get("/login")
async def login_page(request: Request, info: str | None = None):
    if request.session.get("user_id"):
        return RedirectResponse("/", status_code=302)
    return templates.TemplateResponse(request, "login.html", {"error": None, "info": LOGIN_INFO.get(info or "")})


@router.post("/login")
async def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    user = db.query(models.User).filter(models.User.username == username).first()
    if not user or not user.active or not verify_password(password, user.password_hash):
        return templates.TemplateResponse(
            request,
            "login.html",
            {"error": "Usuario o contraseña incorrectos", "info": None},
            status_code=401,
        )
    request.session["user_id"] = user.id
    return RedirectResponse("/cambiar-clave" if user.must_change_password else "/", status_code=302)


@router.post("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=302)


# --- Cambiar la contraseña propia ------------------------------------------------
# También es la pantalla obligatoria tras un restablecimiento hecho por un admin
# (o mientras siga la contraseña de fábrica): ver `must_change_password`.

MIN_PASSWORD_LENGTH = 8
WEAK_PASSWORDS = {"admin123", "12345678", "123456789", "password", "contrasena", "contraseña", "qwerty123", "abc12345"}


def _password_error(user: models.User, current: str, new: str, confirm: str) -> str | None:
    if not verify_password(current, user.password_hash):
        return "La contraseña actual no es correcta"
    if len(new) < MIN_PASSWORD_LENGTH:
        return f"La contraseña nueva debe tener al menos {MIN_PASSWORD_LENGTH} caracteres"
    if new != confirm:
        return "La confirmación no coincide con la contraseña nueva"
    if new == current:
        return "La contraseña nueva debe ser distinta a la actual"
    if new.lower() in WEAK_PASSWORDS or new.lower() == user.username.lower():
        return "Esa contraseña es demasiado fácil de adivinar; elige otra"
    return None


def _change_password_page(request: Request, user: models.User, error: str | None = None, status_code: int = 200):
    return templates.TemplateResponse(
        request,
        "cambiar_clave.html",
        {"user": user, "forced": user.must_change_password, "error": error, "min_length": MIN_PASSWORD_LENGTH, "asset_version": asset_version()},
        status_code=status_code,
    )


@router.get("/cambiar-clave")
async def change_password_page(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=302)
    return _change_password_page(request, user)


@router.post("/cambiar-clave")
async def change_password_submit(
    request: Request,
    current: str = Form(...),
    new: str = Form(...),
    confirm: str = Form(...),
    db: Session = Depends(get_db),
):
    user = get_current_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=302)
    error = _password_error(user, current, new, confirm)
    if error:
        return _change_password_page(request, user, error, status_code=400)
    user.password_hash = hash_password(new)
    user.must_change_password = False
    log_activity(db, user, "user_password", detail=f"{user.username} cambió su propia contraseña")
    db.commit()
    return RedirectResponse("/", status_code=302)
