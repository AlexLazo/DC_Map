from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app import models
from app.database import get_db


# Rutas que siguen funcionando mientras la cuenta tiene el cambio de contraseña
# pendiente (si no, sería imposible salir de ese estado).
PASSWORD_CHANGE_ALLOWED = ("/cambiar-clave", "/logout", "/api/time", "/static/", "/sw.js", "/manifest.webmanifest")


def get_current_user(request: Request, db: Session = Depends(get_db)) -> models.User | None:
    user_id = request.session.get("user_id")
    if not user_id:
        return None
    user = db.get(models.User, user_id)
    if not user or not user.active:
        return None
    path = request.url.path
    if user.must_change_password and not path.startswith(PASSWORD_CHANGE_ALLOWED):
        # Se hace cumplir en el servidor (no solo en la pantalla): mientras el
        # cambio esté pendiente nada más responde. Páginas -> a la pantalla de
        # cambio; llamadas de API/POST -> 403 con mensaje.
        if request.method == "GET" and not path.startswith("/api/"):
            raise HTTPException(status_code=303, headers={"Location": "/cambiar-clave"})
        raise HTTPException(status_code=403, detail="Debes cambiar tu contraseña antes de continuar")
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
