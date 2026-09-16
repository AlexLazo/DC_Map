from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app import models
from app.database import get_db


def get_current_user(request: Request, db: Session = Depends(get_db)) -> models.User | None:
    user_id = request.session.get("user_id")
    if not user_id:
        return None
    user = db.get(models.User, user_id)
    if not user or not user.active:
        return None
    return user


def require_login(request: Request, db: Session = Depends(get_db)) -> models.User:
    user = get_current_user(request, db)
    if not user:
        raise HTTPException(status_code=401, detail="No autenticado")
    return user


ADMIN_ROLES = {"admin", "super_admin"}


def require_role(*roles: str):
    """`admin` y `super_admin` siempre pasan, sin importar qué roles se
    pidan -- son los dos niveles de control total. Para restringir algo a
    SOLO super_admin (gestionar cuentas admin), usar `require_super_admin`."""

    def dependency(user: models.User = Depends(require_login)) -> models.User:
        if user.role in ADMIN_ROLES:
            return user
        if user.role not in roles:
            raise HTTPException(status_code=403, detail="No autorizado")
        return user

    return dependency


def require_super_admin(user: models.User = Depends(require_login)) -> models.User:
    if user.role != "super_admin":
        raise HTTPException(status_code=403, detail="Solo Super Admin puede hacer esto")
    return user
