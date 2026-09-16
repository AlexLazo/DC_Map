import csv
import io
import json
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from app import models
from app.activity_log import log_activity
from app.asset_version import asset_version
from app.auth import get_current_user, require_role
from app.database import get_db
from app.local_time import to_local
from app.security import hash_password
from app.truck_resolution import resolve_truck

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")
templates.env.filters["local_time"] = to_local

ROLES = ("super_admin", "admin", "supervisor", "conductor_patio")
ADMIN_ROLES = {"admin", "super_admin"}
ROLE_LABELS = {
    "super_admin": "Super Admin",
    "admin": "Admin",
    "supervisor": "Supervisor",
    "conductor_patio": "Conductor de Patio",
}
STATUS_LABELS = {"pendiente": "Pendiente", "carga_en_piso": "Carga en Piso", "cargado": "Cargado", "no_cargado": "No Cargado"}
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
    "user_edit": "Edición de usuario",
    "user_toggle": "Activar/desactivar usuario",
    "user_delete": "Eliminar usuario",
}


def _require_admin_page(request: Request, db: Session) -> models.User | None:
    user = get_current_user(request, db)
    if not user:
        return None
    if user.role not in ADMIN_ROLES:
        return None
    return user


@router.get("/admin")
async def admin_page(request: Request, edit: int | None = None, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=302)
    if user.role not in ADMIN_ROLES:
        return RedirectResponse("/", status_code=302)
    users = db.query(models.User).order_by(models.User.username).all()
    trucks = db.query(models.Truck).order_by(models.Truck.placa).all()

    edit_user = None
    if edit:
        candidate = db.get(models.User, edit)
        if candidate and not (candidate.role in ADMIN_ROLES and user.role != "super_admin"):
            edit_user = candidate

    return templates.TemplateResponse(
        request,
        "admin.html",
        {
            "user": user,
            "users": users,
            "trucks": trucks,
            "roles": ROLES,
            "role_labels": ROLE_LABELS,
            "edit_user": edit_user,
            "asset_version": asset_version(),
        },
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


@router.post("/admin/users/{user_id}/edit")
async def edit_user(
    user_id: int,
    full_name: str = Form(...),
    role: str = Form(...),
    password: str = Form(""),
    current_user: models.User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
):
    target = db.get(models.User, user_id)
    if not target:
        raise HTTPException(404, "Usuario no encontrado")
    if role not in ROLES:
        raise HTTPException(400, "Rol inválido")
    # Dos checks distintos a propósito: uno protege una cuenta admin-tier YA
    # existente (no tocarla sin ser super_admin), el otro evita que alguien
    # ESCALE a un usuario normal a admin-tier por esta vía -- create_user y
    # toggle_user cada uno solo necesita uno de los dos, editar necesita
    # ambos a la vez.
    if target.role in ADMIN_ROLES and current_user.role != "super_admin":
        raise HTTPException(403, "Solo Super Admin puede editar cuentas Admin/Super Admin")
    if role in ADMIN_ROLES and current_user.role != "super_admin":
        raise HTTPException(403, "Solo Super Admin puede asignar rol Admin/Super Admin")

    old_name, old_role = target.full_name, target.role
    target.full_name = full_name.strip()
    target.role = role
    changed_password = bool(password.strip())
    if changed_password:
        target.password_hash = hash_password(password.strip())

    detail = f"Usuario editado: {target.username}"
    if old_name != target.full_name:
        detail += f" · nombre: {old_name} → {target.full_name}"
    if old_role != role:
        detail += f" · rol: {ROLE_LABELS[old_role]} → {ROLE_LABELS[role]}"
    if changed_password:
        detail += " · contraseña cambiada"
    log_activity(db, current_user, "user_edit", detail=detail)
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
    log_activity(db, current_user, "user_toggle", detail=f"Usuario {'activado' if target.active else 'desactivado'}: {target.username}")
    db.commit()
    return RedirectResponse("/admin", status_code=302)


@router.post("/admin/users/{user_id}/delete")
async def delete_user(
    user_id: int,
    current_user: models.User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
):
    """Borra la cuenta de verdad (a diferencia de desactivar). Los registros
    viejos de Bitácora que referenciaban a este usuario no se tocan -- se
    quedan sin nombre asociado (la FK es nullable), no se pierde el dato en
    sí, solo el "quién"."""
    target = db.get(models.User, user_id)
    if not target:
        return RedirectResponse("/admin", status_code=302)
    if target.role in ADMIN_ROLES and current_user.role != "super_admin":
        raise HTTPException(403, "Solo Super Admin puede borrar cuentas Admin/Super Admin")
    if target.id == current_user.id:
        raise HTTPException(400, "No puedes borrar tu propia cuenta")

    username = target.username
    log_activity(db, current_user, "user_delete", detail=f"Usuario eliminado: {username} ({ROLE_LABELS[target.role]})")
    db.delete(target)
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


def _cargado_times(db: Session, target_date: date) -> dict[int, datetime]:
    """spot_id -> hora UTC exacta (usar el filtro `local_time` al mostrarla)
    en la que se marcó "Cargado" ese día. Se toma de `ActivityLog` (nunca se
    sobreescribe) y NO de `DailyStatus.updated_at`, porque reasignar el
    camión de un Spot ya cargado (`POST /api/spots/{id}/assign`) también
    dispara el `onupdate` de esa columna sin que el estatus cambie -- eso
    correría silenciosamente "la hora en que se cargó". El formato de
    `detail` (`f"{code}: {OldLabel} → {NewLabel}"`, ver `update_status` en
    board.py) hace que terminar en "→ Cargado" sea inequívoco: una transición
    a "No Cargado" termina en "...→ No Cargado", nunca en "→ Cargado"."""
    rows = (
        db.query(models.ActivityLog.spot_id, func.max(models.ActivityLog.timestamp))
        .filter(
            models.ActivityLog.fecha == target_date,
            models.ActivityLog.action == "status_change",
            models.ActivityLog.detail.like("%→ Cargado"),
        )
        .group_by(models.ActivityLog.spot_id)
        .all()
    )
    return dict(rows)


def _spot_rows(db: Session, target_date: date) -> list[dict]:
    """Una fila resuelta por Spot para `target_date`: código, camión (ya
    resuelto por `resolve_truck`, respeta la reasignación diaria), estatus,
    comentario, quién lo marcó y la hora exacta si quedó "Cargado". Fuente
    compartida por el Dashboard (problemas/desglose por supervisor) y la
    tabla "Camiones del día" de la Bitácora -- antes cada uno re-derivaba su
    propia versión parcial de lo mismo."""
    spots = db.query(models.Spot).options(joinedload(models.Spot.truck)).order_by(models.Spot.code).all()
    statuses = {
        s.spot_id: s
        for s in db.query(models.DailyStatus)
        .options(joinedload(models.DailyStatus.updated_by), joinedload(models.DailyStatus.truck))
        .filter(models.DailyStatus.fecha == target_date)
        .all()
    }
    cargado_times = _cargado_times(db, target_date)

    rows = []
    for spot in spots:
        st = statuses.get(spot.id)
        status = st.status if st else "pendiente"
        rows.append(
            {
                "spot": spot,
                "code": spot.code,
                "supervisor_name": spot.supervisor_name,
                "truck": resolve_truck(spot, st),
                "status": status,
                "comentario": st.comentario if st else None,
                "updated_by": st.updated_by.full_name if st and st.updated_by else None,
                "hora_cargado": cargado_times.get(spot.id) if status == "cargado" else None,
            }
        )
    return rows


def _hourly_loading_profile(db: Session, days: int = 7) -> list[int]:
    """Cuenta, por hora del día local (0-23), cuántos camiones se marcaron
    "Cargado" en los últimos `days` días -- misma fuente/filtro que
    `_cargado_times`, agregada en un rango en vez de por Spot."""
    since = date.today() - timedelta(days=days - 1)
    rows = (
        db.query(models.ActivityLog.timestamp)
        .filter(
            models.ActivityLog.fecha >= since,
            models.ActivityLog.action == "status_change",
            models.ActivityLog.detail.like("%→ Cargado"),
        )
        .all()
    )
    counts = [0] * 24
    for (ts,) in rows:
        counts[to_local(ts).hour] += 1
    return counts


@router.get("/admin/dashboard")
async def admin_dashboard(request: Request, fecha: str | None = None, db: Session = Depends(get_db)):
    user = _require_admin_page(request, db)
    if not user:
        return RedirectResponse("/login", status_code=302)

    target_date = date.fromisoformat(fecha) if fecha else date.today()
    summary = _daily_summary(db, target_date)
    rows = _spot_rows(db, target_date)

    problemas = []
    by_supervisor: dict[str, dict] = {}
    for row in rows:
        name = row["supervisor_name"] or "Sin asignar"
        bucket = by_supervisor.setdefault(name, {"total": 0, "cargado": 0})
        bucket["total"] += 1
        if row["status"] == "cargado":
            bucket["cargado"] += 1
        if row["status"] == "no_cargado":
            problemas.append(
                {
                    "code": row["code"],
                    "placa": row["truck"].placa if row["truck"] else None,
                    "comentario": row["comentario"],
                    "updated_by": row["updated_by"],
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

    # --- Resumen Ejecutivo: comparación vs. ayer + hora pico de carga -------
    prev_summary = _daily_summary(db, target_date - timedelta(days=1))
    hourly_profile = _hourly_loading_profile(db)
    peak_hour = hourly_profile.index(max(hourly_profile)) if any(hourly_profile) else None
    resumen = {
        "delta_pct": summary["pct_cargado"] - prev_summary["pct_cargado"],
        "peak_hour": peak_hour,
        "incidencias": len(problemas),
    }

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
            "resumen": resumen,
            "hourly_profile": hourly_profile,
            "hourly_max": max(hourly_profile) or 1,
            "status_labels": STATUS_LABELS,
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
    truck_rows = _spot_rows(db, target_date)

    return templates.TemplateResponse(
        request,
        "admin_bitacora.html",
        {
            "user": user,
            "fecha": target_date.isoformat(),
            "entries": entries,
            "all_users": all_users,
            "truck_rows": truck_rows,
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
                to_local(e.timestamp).strftime("%H:%M:%S"),
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


@router.get("/admin/reportes/camiones.csv")
async def reporte_camiones_csv(fecha: str, request: Request, db: Session = Depends(get_db)):
    """Un renglón por Spot (no solo los problemáticos) para una fecha
    puntual: estatus final, camión, hora exacta de carga y motivo si no se
    cargó -- lo que Bitácora/Dashboard ya muestran en pantalla, para
    exportar. A diferencia de bitacora.csv/resumen.csv (rango de fechas),
    esta es de una sola fecha, igual que su tabla en admin_bitacora.html."""
    user = _require_admin_page(request, db)
    if not user:
        raise HTTPException(401, "No autenticado")
    target_date = date.fromisoformat(fecha)
    rows = _spot_rows(db, target_date)

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["Spot", "Supervisor", "Placa", "HOD", "SV", "Estatus", "Hora de carga", "Comentario", "Marcado por"])
    for r in rows:
        truck = r["truck"]
        writer.writerow(
            [
                r["code"],
                r["supervisor_name"] or "-",
                (truck and truck.placa) or "-",
                (truck and truck.hod_code) or "-",
                (truck and truck.sv_code) or "-",
                STATUS_LABELS[r["status"]],
                to_local(r["hora_cargado"]).strftime("%H:%M:%S") if r["hora_cargado"] else "-",
                r["comentario"] or "-",
                r["updated_by"] or "-",
            ]
        )
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=camiones_{fecha}.csv"},
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


# --- Configuración / Respaldo -----------------------------------------------

def _serialize_backup(db: Session) -> dict:
    """Vuelca TODAS las tablas a un dict serializable a JSON -- pensado para
    restaurar de verdad (incluye el hash de contraseña de cada usuario), no
    para consumo público. Se usa desde /admin/config/backup.json, detrás del
    mismo gate que el resto del panel admin."""

    def dt(v):
        return v.isoformat() if v else None

    return {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "users": [
            {
                "id": u.id, "username": u.username, "password_hash": u.password_hash,
                "full_name": u.full_name, "role": u.role, "active": u.active, "created_at": dt(u.created_at),
            }
            for u in db.query(models.User).all()
        ],
        "trucks": [
            {"id": t.id, "hod_code": t.hod_code, "placa": t.placa, "sv_code": t.sv_code, "source_cell": t.source_cell, "active": t.active}
            for t in db.query(models.Truck).all()
        ],
        "spots": [
            {
                "id": s.id, "code": s.code, "supervisor_name": s.supervisor_name, "color_hex": s.color_hex,
                "grid_row": s.grid_row, "grid_col": s.grid_col, "row_span": s.row_span, "col_span": s.col_span,
                "truck_id": s.truck_id,
            }
            for s in db.query(models.Spot).all()
        ],
        "labels": [
            {"id": l.id, "text": l.text, "grid_row": l.grid_row, "grid_col": l.grid_col, "row_span": l.row_span, "col_span": l.col_span}
            for l in db.query(models.MapLabel).all()
        ],
        "daily_status": [
            {
                "id": d.id, "spot_id": d.spot_id, "fecha": d.fecha.isoformat(), "status": d.status,
                "comentario": d.comentario, "updated_by_id": d.updated_by_id, "updated_at": dt(d.updated_at),
                "truck_id": d.truck_id, "truck_overridden": d.truck_overridden,
            }
            for d in db.query(models.DailyStatus).all()
        ],
        "activity_log": [
            {
                "id": a.id, "fecha": a.fecha.isoformat(), "timestamp": dt(a.timestamp), "user_id": a.user_id,
                "action": a.action, "spot_id": a.spot_id, "detail": a.detail,
            }
            for a in db.query(models.ActivityLog).all()
        ],
    }


@router.get("/admin/config")
async def admin_config(request: Request, db: Session = Depends(get_db)):
    user = _require_admin_page(request, db)
    if not user:
        return RedirectResponse("/login", status_code=302)
    counts = {
        "users": db.query(models.User).count(),
        "trucks": db.query(models.Truck).count(),
        "spots": db.query(models.Spot).count(),
        "labels": db.query(models.MapLabel).count(),
        "daily_status": db.query(models.DailyStatus).count(),
        "activity_log": db.query(models.ActivityLog).count(),
    }
    return templates.TemplateResponse(
        request, "admin_config.html", {"user": user, "counts": counts, "asset_version": asset_version()}
    )


@router.get("/admin/config/backup.json")
async def admin_config_backup(request: Request, db: Session = Depends(get_db)):
    user = _require_admin_page(request, db)
    if not user:
        raise HTTPException(401, "No autenticado")
    body = json.dumps(_serialize_backup(db), ensure_ascii=False, indent=2)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return StreamingResponse(
        iter([body]),
        media_type="application/json",
        headers={"Content-Disposition": f"attachment; filename=dc_map_backup_{stamp}.json"},
    )
