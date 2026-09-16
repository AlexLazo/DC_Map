import csv
import io
from datetime import date, timedelta

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session, joinedload

from app import models
from app.activity_log import log_activity
from app.asset_version import asset_version
from app.auth import get_current_user, require_role
from app.truck_resolution import resolve_truck
from app.database import get_db
from app.security import hash_password

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

ROLES = ("super_admin", "admin", "supervisor", "conductor_patio")
ADMIN_ROLES = {"admin", "super_admin"}
ROLE_LABELS = {
    "super_admin": "Super Admin",
    "admin": "Admin",
    "supervisor": "Supervisor",
    "conductor_patio": "Conductor de Patio",
}
ACTION_LABELS = {
    "status_change": "Cambio de estatus",
    "assign": "Asignación de camión",
    "truck_edit": "Edición de camión",
    "spot_adjust": "Mover/redimensionar Spot",
    "spot_create": "Crear Spot",
    "spot_delete": "Eliminar Spot",
    "label_adjust": "Mover/redimensionar etiqueta",
    "label_create": "Crear etiqueta",
    "label_delete": "Eliminar etiqueta",
    "label_rename": "Renombrar etiqueta",
    "user_create": "Alta de usuario",
}


def _require_admin_page(request: Request, db: Session) -> models.User | None:
    user = get_current_user(request, db)
    if not user:
        return None
    if user.role not in ADMIN_ROLES:
        return None
    return user


@router.get("/admin")
async def admin_page(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=302)
    if user.role not in ADMIN_ROLES:
        return RedirectResponse("/", status_code=302)
    users = db.query(models.User).order_by(models.User.username).all()
    trucks = db.query(models.Truck).order_by(models.Truck.placa).all()
    return templates.TemplateResponse(
        request,
        "admin.html",
        {"user": user, "users": users, "trucks": trucks, "roles": ROLES, "role_labels": ROLE_LABELS, "asset_version": asset_version()},
    )


@router.post("/admin/users")
async def create_user(
    username: str = Form(...),
    password: str = Form(...),
    full_name: str = Form(...),
    role: str = Form(...),
    current_user: models.User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
):
    if role not in ROLES:
        raise HTTPException(400, "Rol inválido")
    if role in ADMIN_ROLES and current_user.role != "super_admin":
        raise HTTPException(403, "Solo Super Admin puede crear cuentas Admin/Super Admin")
    if db.query(models.User).filter_by(username=username).first():
        raise HTTPException(400, "El usuario ya existe")
    new_user = models.User(
        username=username,
        password_hash=hash_password(password),
        full_name=full_name,
        role=role,
    )
    db.add(new_user)
    db.flush()
    log_activity(db, current_user, "user_create", detail=f"Usuario creado: {username} ({ROLE_LABELS[role]})")
    db.commit()
    return RedirectResponse("/admin", status_code=302)


@router.post("/admin/users/{user_id}/toggle")
async def toggle_user(
    user_id: int,
    current_user: models.User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
):
    target = db.get(models.User, user_id)
    if not target:
        return RedirectResponse("/admin", status_code=302)
    if target.role in ADMIN_ROLES and current_user.role != "super_admin":
        raise HTTPException(403, "Solo Super Admin puede activar/desactivar cuentas Admin/Super Admin")
    target.active = not target.active
    db.commit()
    return RedirectResponse("/admin", status_code=302)


@router.post("/admin/trucks")
async def create_truck(
    placa: str = Form(...),
    hod_code: str = Form(""),
    sv_code: str = Form(""),
    _: models.User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
):
    db.add(
        models.Truck(
            placa=placa.strip(),
            hod_code=hod_code.strip() or None,
            sv_code=sv_code.strip() or None,
        )
    )
    db.commit()
    return RedirectResponse("/admin", status_code=302)


# --- Dashboard / Resumen Ejecutivo ------------------------------------------

def _daily_summary(db: Session, target_date: date) -> dict:
    spots = db.query(models.Spot).all()
    statuses = {s.spot_id: s for s in db.query(models.DailyStatus).filter(models.DailyStatus.fecha == target_date).all()}
    counts = {"pendiente": 0, "carga_en_piso": 0, "cargado": 0, "no_cargado": 0}
    for spot in spots:
        st = statuses.get(spot.id)
        counts[st.status if st else "pendiente"] += 1
    total = len(spots) or 1
    pct = {k: round(100 * v / total) for k, v in counts.items()}
    return {
        "fecha": target_date,
        "total": len(spots),
        "counts": counts,
        "pct": pct,
        "pct_cargado": pct["cargado"],
    }


@router.get("/admin/dashboard")
async def admin_dashboard(request: Request, fecha: str | None = None, db: Session = Depends(get_db)):
    user = _require_admin_page(request, db)
    if not user:
        return RedirectResponse("/login", status_code=302)

    target_date = date.fromisoformat(fecha) if fecha else date.today()
    summary = _daily_summary(db, target_date)

    spots = db.query(models.Spot).options(joinedload(models.Spot.truck)).all()
    statuses = {
        s.spot_id: s
        for s in db.query(models.DailyStatus)
        .options(joinedload(models.DailyStatus.updated_by), joinedload(models.DailyStatus.truck))
        .filter(models.DailyStatus.fecha == target_date)
        .all()
    }

    problemas = []
    by_supervisor: dict[str, dict] = {}
    for spot in spots:
        name = spot.supervisor_name or "Sin asignar"
        bucket = by_supervisor.setdefault(name, {"total": 0, "cargado": 0})
        bucket["total"] += 1
        st = statuses.get(spot.id)
        status = st.status if st else "pendiente"
        if status == "cargado":
            bucket["cargado"] += 1
        if status == "no_cargado":
            truck = resolve_truck(spot, st)
            problemas.append(
                {
                    "code": spot.code,
                    "placa": truck.placa if truck else None,
                    "comentario": st.comentario if st else None,
                    "updated_by": st.updated_by.full_name if st and st.updated_by else None,
                }
            )
    supervisor_rows = sorted(
        (
            {"name": name, "total": b["total"], "cargado": b["cargado"], "pct": round(100 * b["cargado"] / b["total"]) if b["total"] else 0}
            for name, b in by_supervisor.items()
        ),
        key=lambda r: r["name"],
    )

    trend = []
    for i in range(6, -1, -1):
        d = target_date - timedelta(days=i)
        trend.append(_daily_summary(db, d))

    return templates.TemplateResponse(
        request,
        "admin_dashboard.html",
        {
            "user": user,
            "fecha": target_date.isoformat(),
            "summary": summary,
            "problemas": problemas,
            "supervisor_rows": supervisor_rows,
            "trend": trend,
            "status_labels": {"pendiente": "Pendiente", "carga_en_piso": "Carga en Piso", "cargado": "Cargado", "no_cargado": "No Cargado"},
            "asset_version": asset_version(),
        },
    )


# --- Bitácora ----------------------------------------------------------------

@router.get("/admin/bitacora")
async def admin_bitacora(
    request: Request,
    fecha: str | None = None,
    usuario_id: int | None = None,
    accion: str | None = None,
    db: Session = Depends(get_db),
):
    user = _require_admin_page(request, db)
    if not user:
        return RedirectResponse("/login", status_code=302)

    target_date = date.fromisoformat(fecha) if fecha else date.today()
    query = (
        db.query(models.ActivityLog)
        .options(joinedload(models.ActivityLog.user), joinedload(models.ActivityLog.spot))
        .filter(models.ActivityLog.fecha == target_date)
    )
    if usuario_id:
        query = query.filter(models.ActivityLog.user_id == usuario_id)
    if accion:
        query = query.filter(models.ActivityLog.action == accion)
    entries = query.order_by(models.ActivityLog.timestamp.desc()).limit(500).all()

    all_users = db.query(models.User).order_by(models.User.full_name).all()

    return templates.TemplateResponse(
        request,
        "admin_bitacora.html",
        {
            "user": user,
            "fecha": target_date.isoformat(),
            "entries": entries,
            "all_users": all_users,
            "actions": ACTION_LABELS,
            "selected_usuario_id": usuario_id,
            "selected_accion": accion,
            "asset_version": asset_version(),
        },
    )


# --- Reportes ------------------------------------------------------------

@router.get("/admin/reportes")
async def admin_reportes(request: Request, desde: str | None = None, hasta: str | None = None, db: Session = Depends(get_db)):
    user = _require_admin_page(request, db)
    if not user:
        return RedirectResponse("/login", status_code=302)
    hoy = date.today()
    desde_val = desde or (hoy - timedelta(days=6)).isoformat()
    hasta_val = hasta or hoy.isoformat()
    return templates.TemplateResponse(
        request, "admin_reportes.html", {"user": user, "desde": desde_val, "hasta": hasta_val, "asset_version": asset_version()}
    )


@router.get("/admin/reportes/bitacora.csv")
async def reporte_bitacora_csv(desde: str, hasta: str, request: Request, db: Session = Depends(get_db)):
    user = _require_admin_page(request, db)
    if not user:
        raise HTTPException(401, "No autenticado")
    d0, d1 = date.fromisoformat(desde), date.fromisoformat(hasta)
    entries = (
        db.query(models.ActivityLog)
        .options(joinedload(models.ActivityLog.user), joinedload(models.ActivityLog.spot))
        .filter(models.ActivityLog.fecha.between(d0, d1))
        .order_by(models.ActivityLog.timestamp)
        .all()
    )

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["Fecha", "Hora", "Usuario", "Rol", "Acción", "Spot", "Detalle"])
    for e in entries:
        writer.writerow(
            [
                e.fecha.isoformat(),
                e.timestamp.strftime("%H:%M:%S"),
                e.user.full_name if e.user else "-",
                ROLE_LABELS.get(e.user.role, e.user.role) if e.user else "-",
                ACTION_LABELS.get(e.action, e.action),
                e.spot.code if e.spot else "-",
                e.detail,
            ]
        )
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=bitacora_{desde}_a_{hasta}.csv"},
    )


@router.get("/admin/reportes/resumen.csv")
async def reporte_resumen_csv(desde: str, hasta: str, request: Request, db: Session = Depends(get_db)):
    user = _require_admin_page(request, db)
    if not user:
        raise HTTPException(401, "No autenticado")
    d0, d1 = date.fromisoformat(desde), date.fromisoformat(hasta)

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["Fecha", "Total Spots", "Pendiente", "Carga en Piso", "Cargado", "No Cargado", "% Cargado"])
    d = d0
    while d <= d1:
        s = _daily_summary(db, d)
        writer.writerow(
            [s["fecha"].isoformat(), s["total"], s["counts"]["pendiente"], s["counts"]["carga_en_piso"], s["counts"]["cargado"], s["counts"]["no_cargado"], s["pct_cargado"]]
        )
        d += timedelta(days=1)
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=resumen_{desde}_a_{hasta}.csv"},
    )
