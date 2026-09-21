import io
import json
import re
import secrets
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse, Response, StreamingResponse
from fastapi.templating import Jinja2Templates
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from sqlalchemy import func, text
from sqlalchemy.orm import Session, joinedload

from app import models
from app.activity_log import log_activity
from app.asset_version import asset_version
from app.auth import get_current_user, require_role, require_super_admin
from app.config import SHIFT_END_HOUR, SHIFT_START_HOUR
from app.database import get_db
from app.local_time import local_now, operational_today, to_local
from app.security import hash_password
from app.shift import hour_label, shift_is_open
from app.truck_resolution import resolve_truck
from app.ws_manager import manager

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
    "records_reset": "Reinicio de registros",
    "user_reset_password": "Restablecer contraseña",
    "user_password": "Cambio de contraseña propia",
    "records_restore": "Restauración desde respaldo",
}


# Alfabeto sin caracteres que se confunden al teclearlos en una tablet (0/O, 1/I/L).
_TEMP_PASSWORD_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"


def _temp_password(length: int = 8) -> str:
    return "".join(secrets.choice(_TEMP_PASSWORD_ALPHABET) for _ in range(length))


def _shift_label(day: date) -> str:
    """Rango real del turno que corresponde a un día operativo, para que se
    vea a simple vista qué cubre esa fecha (el turno EMPIEZA el día anterior)."""
    prev = day - timedelta(days=1)
    return f"Turno: {prev.strftime('%d/%m')} {hour_label(SHIFT_START_HOUR)} → {day.strftime('%d/%m')} {hour_label(SHIFT_END_HOUR)}"


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

    temp_password = request.session.pop("temp_password", None)

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
            "temp_password": temp_password,
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


@router.post("/admin/users/{user_id}/reset-password")
async def reset_user_password(
    request: Request,
    user_id: int,
    current_user: models.User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
):
    """Genera una contraseña TEMPORAL para el usuario y lo obliga a crear una
    nueva en su próximo ingreso. La temporal se le muestra al admin una sola vez
    (vía sesión, no queda guardada en ningún lado en claro) y NUNCA se escribe
    en la bitácora. Mismo permiso que editar: solo un Super Admin toca cuentas
    Admin/Super Admin."""
    target = db.get(models.User, user_id)
    if not target:
        raise HTTPException(404, "Usuario no encontrado")
    if target.id == current_user.id:
        raise HTTPException(400, "Para cambiar tu propia contraseña usa 'Cambiar contraseña' en la barra superior")
    if target.role in ADMIN_ROLES and current_user.role != "super_admin":
        raise HTTPException(403, "Solo Super Admin puede restablecer cuentas Admin/Super Admin")

    temp = _temp_password()
    target.password_hash = hash_password(temp)
    target.must_change_password = True
    log_activity(db, current_user, "user_reset_password", detail=f"Contraseña restablecida: {target.username} (debe cambiarla al ingresar)")
    db.commit()
    request.session["temp_password"] = {"username": target.username, "full_name": target.full_name, "password": temp}
    return RedirectResponse("/admin", status_code=303)


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
    since = operational_today() - timedelta(days=days - 1)
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

    target_date = date.fromisoformat(fecha) if fecha else operational_today()
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
        "faltan": summary["counts"]["pendiente"] + summary["counts"]["carga_en_piso"],
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
            "shift_start_hour": SHIFT_START_HOUR,
            "shift_end_hour": SHIFT_END_HOUR,
            "shift_label": _shift_label(target_date),
            "shift_window": f"{hour_label(SHIFT_START_HOUR)} a {hour_label(SHIFT_END_HOUR)}",
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

    target_date = date.fromisoformat(fecha) if fecha else operational_today()
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


# --- Reportes (Excel) -----------------------------------------------------------
# Se entregan como .xlsx real (openpyxl) y no como CSV: en Excel con configuración
# regional en español el separador de listas es ";" y no ",", así que un CSV con
# comas se abre con todo el renglón metido en la columna A. Un .xlsx abre igual en
# cualquier equipo, conserva los ceros a la izquierda de los SV ("0123") y permite
# colores, filtros y fechas/horas reales.

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
MAX_REPORT_DAYS = 92
STATUS_FILL = {
    "cargado": "BBF7D0",
    "carga_en_piso": "FEF08A",
    "no_cargado": "FECACA",
    "pendiente": "E5E7EB",
}
_HEADER_FILL = PatternFill("solid", fgColor="1F2937")
_HEADER_FONT = Font(bold=True, color="FFFFFF")
_DATETIME_FMT = "dd/mm/yyyy hh:mm AM/PM"
_NO_CARGADO_REASON = re.compile(r"→ No Cargado \((.*)\)$", re.DOTALL)


@router.get("/admin/reportes")
async def admin_reportes(request: Request, desde: str | None = None, hasta: str | None = None, db: Session = Depends(get_db)):
    user = _require_admin_page(request, db)
    if not user:
        return RedirectResponse("/login", status_code=302)
    hoy = operational_today()
    desde_val = desde or (hoy - timedelta(days=6)).isoformat()
    hasta_val = hasta or hoy.isoformat()
    return templates.TemplateResponse(
        request,
        "admin_reportes.html",
        {
            "user": user,
            "desde": desde_val,
            "hasta": hasta_val,
            "hoy": hoy.isoformat(),
            "start_label": hour_label(SHIFT_START_HOUR),
            "end_label": hour_label(SHIFT_END_HOUR),
            "max_days": MAX_REPORT_DAYS,
            "asset_version": asset_version(),
        },
    )


def _report_range(desde: str, hasta: str) -> tuple[date, date]:
    try:
        d0, d1 = date.fromisoformat(desde), date.fromisoformat(hasta)
    except ValueError:
        raise HTTPException(400, "Fechas inválidas")
    if d0 > d1:
        raise HTTPException(400, "'Desde' no puede ser posterior a 'Hasta'")
    if (d1 - d0).days >= MAX_REPORT_DAYS:
        raise HTTPException(400, f"El rango máximo es de {MAX_REPORT_DAYS} días")
    return d0, d1


def _xlsx_response(wb: Workbook, filename: str) -> Response:
    buf = io.BytesIO()
    wb.save(buf)
    return Response(
        content=buf.getvalue(),
        media_type=XLSX_MIME,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _init_sheet(ws, headers: list[str], widths: list[int]) -> None:
    ws.append(headers)
    for col, width in enumerate(widths, start=1):
        cell = ws.cell(row=1, column=col)
        cell.fill = _HEADER_FILL
        cell.font = _HEADER_FONT
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        ws.column_dimensions[get_column_letter(col)].width = width
    ws.row_dimensions[1].height = 30
    ws.freeze_panes = "A2"


def _finish_sheet(ws, n_cols: int) -> None:
    ws.auto_filter.ref = f"A1:{get_column_letter(n_cols)}{max(ws.max_row, 1)}"


def _range_rows(db: Session, d0: date, d1: date) -> list[dict]:
    """Una fila por (día operativo, Spot) para el rango. Solo entran los días
    con actividad registrada (algún estatus/asignación) y el día operativo
    actual -- así un domingo sin operación no aparece como 127 "Pendientes"
    falsos. Si el rango es de un solo día, ese día entra siempre (la persona
    lo pidió explícitamente). Todo en 3 consultas para todo el rango, no una
    por día."""
    spots = db.query(models.Spot).options(joinedload(models.Spot.truck)).all()
    statuses = (
        db.query(models.DailyStatus)
        .options(joinedload(models.DailyStatus.updated_by), joinedload(models.DailyStatus.truck))
        .filter(models.DailyStatus.fecha.between(d0, d1))
        .all()
    )
    by_key = {(s.spot_id, s.fecha): s for s in statuses}

    if d0 == d1:
        days = [d0]
    else:
        active = {s.fecha for s in statuses}
        hoy = operational_today()
        if d0 <= hoy <= d1:
            active.add(hoy)
        days = sorted(active)

    events = (
        db.query(models.ActivityLog)
        .options(joinedload(models.ActivityLog.user))
        .filter(
            models.ActivityLog.fecha.between(d0, d1),
            models.ActivityLog.action == "status_change",
            models.ActivityLog.spot_id.isnot(None),
        )
        .order_by(models.ActivityLog.timestamp)
        .all()
    )
    history: dict[tuple[int, date], list] = defaultdict(list)
    for e in events:
        history[(e.spot_id, e.fecha)].append(e)

    spots.sort(key=lambda s: (s.supervisor_name or "", s.code))
    rows = []
    for fecha in days:
        for spot in spots:
            st = by_key.get((spot.id, fecha))
            status = st.status if st else "pendiente"
            evs = history.get((spot.id, fecha), [])

            hora_en_piso = next((e.timestamp for e in evs if e.detail.endswith("→ Carga en Piso")), None)
            cargados = [e.timestamp for e in evs if e.detail.endswith("→ Cargado")]
            hora_cargado = max(cargados) if (cargados and status == "cargado") else None
            reasons = [m.group(1) for m in (_NO_CARGADO_REASON.search(e.detail) for e in evs) if m]

            if status == "no_cargado":
                motivo = (st.comentario if st and st.comentario else None) or "Sin motivo registrado"
            elif status == "cargado":
                motivo = ("Cargado tras retraso: " + " / ".join(reasons)) if reasons else "Cargado"
            elif status == "carga_en_piso":
                motivo = "Quedó en carga en piso (no se marcó como cargado)"
            else:
                motivo = "Reiniciado a pendiente (ver historial)" if evs else "Sin movimiento registrado"

            lines = []
            for e in evs:
                transition = e.detail.split(": ", 1)[-1]
                who = e.user.full_name if e.user else "-"
                lines.append(f"{to_local(e.timestamp).strftime('%I:%M %p')} · {transition} · {who}")

            rows.append(
                {
                    "fecha": fecha,
                    "code": spot.code,
                    "supervisor_name": spot.supervisor_name,
                    "truck": resolve_truck(spot, st),
                    "status": status,
                    "hora_en_piso": hora_en_piso,
                    "hora_cargado": hora_cargado,
                    "motivo": motivo,
                    "updated_by": st.updated_by.full_name if st and st.updated_by else None,
                    "historial": "\n".join(lines),
                }
            )
    return rows


@router.get("/admin/reportes/camiones.xlsx")
async def reporte_camiones_xlsx(desde: str, hasta: str, request: Request, db: Session = Depends(get_db)):
    """Un renglón por camión (Spot) por día operativo: estatus, hora exacta de
    carga, motivo si no se pudo cargar y el historial completo del día -- lo que
    hace falta para ver qué pasó con cada camión cada día. Segunda hoja con el
    resumen de cada día."""
    user = _require_admin_page(request, db)
    if not user:
        raise HTTPException(401, "No autenticado")
    d0, d1 = _report_range(desde, hasta)
    rows = _range_rows(db, d0, d1)

    wb = Workbook()
    ws = wb.active
    ws.title = "Camiones por día"
    headers = [
        "Día operativo", "Spot", "Supervisor", "Placa", "HOD", "SV", "Estatus",
        "Entró a carga en piso", "Hora de carga (Cargado)", "Motivo / comentario", "Marcado por", "Historial del día",
    ]
    _init_sheet(ws, headers, [13, 16, 15, 12, 10, 8, 15, 22, 22, 44, 20, 60])
    for r in rows:
        truck = r["truck"]
        ws.append(
            [
                r["fecha"],
                r["code"],
                r["supervisor_name"] or "-",
                (truck and truck.placa) or "-",
                (truck and truck.hod_code) or "-",
                (truck and truck.sv_code) or "-",
                STATUS_LABELS[r["status"]],
                to_local(r["hora_en_piso"]) if r["hora_en_piso"] else None,
                to_local(r["hora_cargado"]) if r["hora_cargado"] else None,
                r["motivo"],
                r["updated_by"] or "-",
                r["historial"] or "-",
            ]
        )
        row_idx = ws.max_row
        ws.cell(row=row_idx, column=1).number_format = "dd/mm/yyyy"
        ws.cell(row=row_idx, column=6).number_format = "@"  # SV: texto, conserva el 0 inicial
        ws.cell(row=row_idx, column=7).fill = PatternFill("solid", fgColor=STATUS_FILL[r["status"]])
        ws.cell(row=row_idx, column=8).number_format = _DATETIME_FMT
        ws.cell(row=row_idx, column=9).number_format = _DATETIME_FMT
        for col in (10, 12):
            ws.cell(row=row_idx, column=col).alignment = Alignment(wrap_text=True, vertical="top")
        for col in (1, 2, 3, 4, 5, 6, 7, 8, 9, 11):
            ws.cell(row=row_idx, column=col).alignment = Alignment(vertical="top")
    _finish_sheet(ws, len(headers))

    ws2 = wb.create_sheet("Resumen por día")
    headers2 = ["Día operativo", "Total Spots", "Pendiente", "Carga en Piso", "Cargado", "No Cargado", "% Cargado"]
    _init_sheet(ws2, headers2, [15, 12, 12, 14, 12, 13, 12])
    per_day: dict[date, dict[str, int]] = {}
    for r in rows:
        per_day.setdefault(r["fecha"], {k: 0 for k in STATUS_LABELS})[r["status"]] += 1
    for fecha in sorted(per_day):
        c = per_day[fecha]
        total = sum(c.values())
        ws2.append([fecha, total, c["pendiente"], c["carga_en_piso"], c["cargado"], c["no_cargado"], (c["cargado"] / total) if total else 0])
        ws2.cell(row=ws2.max_row, column=1).number_format = "dd/mm/yyyy"
        ws2.cell(row=ws2.max_row, column=7).number_format = "0%"
    _finish_sheet(ws2, len(headers2))

    return _xlsx_response(wb, f"camiones_por_dia_{desde}_a_{hasta}.xlsx")


@router.get("/admin/reportes/bitacora.xlsx")
async def reporte_bitacora_xlsx(desde: str, hasta: str, request: Request, db: Session = Depends(get_db)):
    """Cada evento registrado (cambios de estatus, asignaciones, ediciones,
    movimientos de layout, altas/bajas de usuarios) entre las dos fechas, con
    fecha y hora exactas en hora local."""
    user = _require_admin_page(request, db)
    if not user:
        raise HTTPException(401, "No autenticado")
    d0, d1 = _report_range(desde, hasta)
    entries = (
        db.query(models.ActivityLog)
        .options(joinedload(models.ActivityLog.user), joinedload(models.ActivityLog.spot))
        .filter(models.ActivityLog.fecha.between(d0, d1))
        .order_by(models.ActivityLog.timestamp)
        .all()
    )

    wb = Workbook()
    ws = wb.active
    ws.title = "Bitácora"
    headers = ["Día operativo", "Fecha y hora", "Usuario", "Rol", "Acción", "Spot", "Detalle"]
    _init_sheet(ws, headers, [13, 22, 22, 18, 26, 16, 70])
    for e in entries:
        ws.append(
            [
                e.fecha,
                to_local(e.timestamp),
                e.user.full_name if e.user else "-",
                ROLE_LABELS.get(e.user.role, e.user.role) if e.user else "-",
                ACTION_LABELS.get(e.action, e.action),
                e.spot.code if e.spot else "-",
                e.detail,
            ]
        )
        ws.cell(row=ws.max_row, column=1).number_format = "dd/mm/yyyy"
        ws.cell(row=ws.max_row, column=2).number_format = _DATETIME_FMT
        ws.cell(row=ws.max_row, column=7).alignment = Alignment(wrap_text=True, vertical="top")
    _finish_sheet(ws, len(headers))

    return _xlsx_response(wb, f"bitacora_{desde}_a_{hasta}.xlsx")


# --- Configuración / Respaldo -----------------------------------------------

def _serialize_backup(db: Session) -> dict:
    """Vuelca TODAS las tablas a un dict serializable a JSON -- pensado para
    restaurar de verdad (incluye el hash de contraseña de cada usuario), no
    para consumo público. Se usa desde /admin/config/backup.json, detrás del
    mismo gate que el resto del panel admin."""

    def dt(v):
        return v.isoformat() if v else None

    return {
        "format": "dc_map_backup",
        "version": 1,
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "users": [
            {
                "id": u.id, "username": u.username, "password_hash": u.password_hash,
                "full_name": u.full_name, "role": u.role, "active": u.active, "created_at": dt(u.created_at),
                "must_change_password": u.must_change_password,
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
async def admin_config(
    request: Request,
    reset_ds: int | None = None,
    reset_al: int | None = None,
    restored_ds: int | None = None,
    restored_al: int | None = None,
    restored_skipped: int | None = None,
    db: Session = Depends(get_db),
):
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
    spans = [
        db.query(func.min(model.fecha), func.max(model.fecha)).one()
        for model in (models.DailyStatus, models.ActivityLog)
    ]
    mins = [a for a, _ in spans if a]
    maxs = [b for _, b in spans if b]
    return templates.TemplateResponse(
        request,
        "admin_config.html",
        {
            "user": user,
            "counts": counts,
            "fecha_min": min(mins).isoformat() if mins else "",
            "fecha_max": max(maxs).isoformat() if maxs else "",
            "reset_done": (reset_ds, reset_al) if reset_ds is not None and reset_al is not None else None,
            "confirm_word": RESET_CONFIRM_WORD,
            "restore_word": RESTORE_CONFIRM_WORD,
            "restored": (restored_ds, restored_al, restored_skipped or 0) if restored_ds is not None and restored_al is not None else None,
            "asset_version": asset_version(),
        },
    )


@router.get("/admin/config/backup.json")
async def admin_config_backup(request: Request, db: Session = Depends(get_db)):
    user = _require_admin_page(request, db)
    if not user:
        raise HTTPException(401, "No autenticado")
    body = json.dumps(_serialize_backup(db), ensure_ascii=False, indent=2)
    stamp = local_now().strftime("%Y%m%d_%H%M%S")
    return StreamingResponse(
        iter([body]),
        media_type="application/json",
        headers={"Content-Disposition": f"attachment; filename=dc_map_backup_{stamp}.json"},
    )


# --- Reiniciar registros (solo Super Admin) ------------------------------------
# Para empezar limpio después de pruebas controladas: borra el HISTORIAL
# OPERATIVO (estatus diarios + bitácora), completo o de un rango de días
# operativos. NUNCA toca usuarios, camiones, Spots ni etiquetas (el layout).
# Cuatro seguros: solo Super Admin (validado aquí, no solo en la pantalla), vista
# previa de lo que se va a borrar, palabra de confirmación escrita, y la
# pantalla descarga un respaldo completo justo antes de mandar el borrado.

RESET_CONFIRM_WORD = "REINICIAR"


def _reset_queries(db: Session, scope: str, desde: str, hasta: str):
    """(consulta DailyStatus, consulta ActivityLog, etiqueta legible, d0, d1)
    del alcance pedido. d0/d1 son None cuando el alcance es todo el historial."""
    ds_q = db.query(models.DailyStatus)
    al_q = db.query(models.ActivityLog)
    if scope == "all":
        return ds_q, al_q, "todo el historial", None, None
    if scope == "range":
        try:
            d0, d1 = date.fromisoformat(desde), date.fromisoformat(hasta)
        except ValueError:
            raise HTTPException(400, "Elige las dos fechas del rango")
        if d0 > d1:
            raise HTTPException(400, "'Desde' no puede ser posterior a 'Hasta'")
        return (
            ds_q.filter(models.DailyStatus.fecha.between(d0, d1)),
            al_q.filter(models.ActivityLog.fecha.between(d0, d1)),
            f"{d0.isoformat()} a {d1.isoformat()}",
            d0,
            d1,
        )
    raise HTTPException(400, "Alcance inválido")


@router.get("/admin/config/reset-preview")
async def admin_config_reset_preview(
    scope: str = "all",
    desde: str = "",
    hasta: str = "",
    _: models.User = Depends(require_super_admin),
    db: Session = Depends(get_db),
):
    ds_q, al_q, label, d0, d1 = _reset_queries(db, scope, desde, hasta)
    hoy = operational_today()
    includes_today = (
        ds_q.filter(models.DailyStatus.fecha == hoy).count() > 0
        if d0 is None
        else d0 <= hoy <= d1
    )
    return {
        "daily_status": ds_q.count(),
        "activity_log": al_q.count(),
        "label": label,
        # Con turno en curso, borrar el día de hoy borra lo que los conductores
        # ya marcaron esta noche -- la pantalla lo avisa en rojo.
        "warn_live": bool(includes_today and shift_is_open()),
    }


@router.post("/admin/config/reset")
async def admin_config_reset(
    scope: str = Form(...),
    desde: str = Form(""),
    hasta: str = Form(""),
    confirm: str = Form(""),
    current_user: models.User = Depends(require_super_admin),
    db: Session = Depends(get_db),
):
    if confirm.strip().upper() != RESET_CONFIRM_WORD:
        raise HTTPException(400, f"Escribe {RESET_CONFIRM_WORD} para confirmar")
    ds_q, al_q, label, _, _ = _reset_queries(db, scope, desde, hasta)
    n_ds = ds_q.delete(synchronize_session=False)
    n_al = al_q.delete(synchronize_session=False)
    # Se registra DESPUÉS de borrar, así el reinicio mismo queda como el primer
    # evento de la bitácora nueva (quién, cuándo y qué alcance).
    log_activity(
        db, current_user, "records_reset",
        detail=f"Registros reiniciados ({label}): {n_ds} estatus diarios y {n_al} eventos de bitácora borrados",
    )
    db.commit()
    # Los tableros/dashboards abiertos se recargan solos (mismo aviso que un cambio de layout).
    await manager.broadcast({"type": "layout_update"})
    return RedirectResponse(f"/admin/config?reset_ds={n_ds}&reset_al={n_al}", status_code=303)


# --- Restaurar desde respaldo (solo Super Admin) -----------------------------------
# Lee un archivo generado por "Descargar respaldo completo". Dos modos:
#   history: reemplaza SOLO los estatus diarios y la bitácora (el caso típico:
#            deshacer un reinicio de registros). No toca usuarios ni layout.
#   full:    reemplaza TODO (usuarios, camiones, Spots, etiquetas, historial):
#            recuperación total, p. ej. si se perdió la base. El layout vive solo
#            en la base (nunca vuelve al Excel), por eso vale la pena poder
#            recuperarlo. Cierra todas las sesiones (los ids de usuario pueden
#            cambiar) y exige que el respaldo traiga al menos un Super Admin activo.
# Primero se valida y se arma el plan completo SIN tocar la base; la escritura es
# una sola transacción (todo o nada).

RESTORE_CONFIRM_WORD = "RESTAURAR"
MAX_BACKUP_BYTES = 60 * 1024 * 1024


def _v_int(v):
    if isinstance(v, bool) or not isinstance(v, int):
        raise ValueError("debe ser un entero")
    return v


def _v_str(v):
    if not isinstance(v, str):
        raise ValueError("debe ser texto")
    return v


def _v_bool(v):
    if not isinstance(v, bool):
        raise ValueError("debe ser verdadero/falso")
    return v


def _v_date(v):
    try:
        return date.fromisoformat(_v_str(v)[:10])
    except ValueError:
        raise ValueError("fecha inválida")


def _v_datetime(v):
    try:
        dt = datetime.fromisoformat(_v_str(v).replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("fecha/hora inválida")
    return dt.astimezone(timezone.utc).replace(tzinfo=None) if dt.tzinfo else dt


def _v_role(v):
    if _v_str(v) not in ROLES:
        raise ValueError("rol desconocido")
    return v


def _v_status(v):
    if _v_str(v) not in STATUS_LABELS:
        raise ValueError("estatus desconocido")
    return v


def _opt(fn):
    return lambda v: None if v is None else fn(v)


_MISSING = object()
# tabla -> {campo: (validador, obligatorio, valor por defecto si falta)}
_BACKUP_SPEC = {
    "users": {
        "id": (_v_int, True, None), "username": (_v_str, True, None), "password_hash": (_v_str, True, None),
        "full_name": (_v_str, True, None), "role": (_v_role, True, None), "active": (_v_bool, False, True),
        "created_at": (_opt(_v_datetime), False, None), "must_change_password": (_v_bool, False, False),
    },
    "trucks": {
        "id": (_v_int, True, None), "hod_code": (_opt(_v_str), False, None), "placa": (_opt(_v_str), False, None),
        "sv_code": (_opt(_v_str), False, None), "source_cell": (_opt(_v_str), False, None), "active": (_v_bool, False, True),
    },
    "spots": {
        "id": (_v_int, True, None), "code": (_v_str, True, None), "supervisor_name": (_opt(_v_str), False, None),
        "color_hex": (_opt(_v_str), False, None), "grid_row": (_v_int, True, None), "grid_col": (_v_int, True, None),
        "row_span": (_v_int, False, 1), "col_span": (_v_int, False, 1), "truck_id": (_opt(_v_int), False, None),
    },
    "labels": {
        "id": (_v_int, True, None), "text": (_v_str, True, None), "grid_row": (_v_int, True, None),
        "grid_col": (_v_int, True, None), "row_span": (_v_int, False, 1), "col_span": (_v_int, False, 1),
    },
    "daily_status": {
        "id": (_v_int, True, None), "spot_id": (_v_int, True, None), "fecha": (_v_date, True, None),
        "status": (_v_status, True, None), "comentario": (_opt(_v_str), False, None),
        "updated_by_id": (_opt(_v_int), False, None), "updated_at": (_opt(_v_datetime), False, None),
        "truck_id": (_opt(_v_int), False, None), "truck_overridden": (_v_bool, False, False),
    },
    "activity_log": {
        "id": (_v_int, True, None), "fecha": (_v_date, True, None), "timestamp": (_opt(_v_datetime), False, None),
        "user_id": (_opt(_v_int), False, None), "action": (_v_str, True, None),
        "spot_id": (_opt(_v_int), False, None), "detail": (_opt(_v_str), False, ""),
    },
}
# tabla del respaldo -> modelo
_BACKUP_MODELS = {
    "users": models.User, "trucks": models.Truck, "spots": models.Spot, "labels": models.MapLabel,
    "daily_status": models.DailyStatus, "activity_log": models.ActivityLog,
}
_BACKUP_TABLE_NAMES = {
    "users": "users", "trucks": "trucks", "spots": "spots", "labels": "map_labels",
    "daily_status": "daily_status", "activity_log": "activity_log",
}


def _parse_backup(raw: bytes) -> dict:
    """Valida el archivo y lo convierte a filas tipadas. ValueError con un mensaje
    claro si algo no cuadra -- ANTES de tocar la base."""
    try:
        data = json.loads(raw.decode("utf-8-sig"))
    except (ValueError, UnicodeDecodeError):
        raise ValueError("El archivo no es un JSON válido")
    if not isinstance(data, dict):
        raise ValueError("El archivo no parece un respaldo de DC Map")
    if "format" in data and data["format"] != "dc_map_backup":
        raise ValueError("El archivo no es un respaldo de DC Map")
    if isinstance(data.get("version"), int) and data["version"] > 1:
        raise ValueError("Este respaldo es de una versión más nueva de la app; actualiza la app antes de restaurarlo")
    missing = [t for t in _BACKUP_SPEC if not isinstance(data.get(t), list)]
    if missing:
        raise ValueError("No parece un respaldo completo de DC Map (faltan: " + ", ".join(missing) + ")")

    tables = {}
    for table, spec in _BACKUP_SPEC.items():
        rows, seen_ids = [], set()
        for i, raw_row in enumerate(data[table]):
            if not isinstance(raw_row, dict):
                raise ValueError(f"{table}[{i}]: cada registro debe ser un objeto")
            row = {}
            for key, (fn, required, default) in spec.items():
                value = raw_row.get(key, _MISSING)
                if value is _MISSING:
                    if required:
                        raise ValueError(f"{table}[{i}]: falta el campo '{key}'")
                    row[key] = default
                    continue
                try:
                    row[key] = fn(value)
                except ValueError as exc:
                    raise ValueError(f"{table}[{i}]: campo '{key}' inválido ({exc})")
            if row["id"] in seen_ids:
                raise ValueError(f"{table}: id repetido ({row['id']})")
            seen_ids.add(row["id"])
            # created_at / updated_at / timestamp son NOT NULL con valor por defecto en la
            # base: un None explícito en la inserción masiva la rompería.
            for stamp in ("created_at", "updated_at", "timestamp"):
                if stamp in row and row[stamp] is None:
                    row[stamp] = datetime.now(timezone.utc).replace(tzinfo=None)
            rows.append(row)
        tables[table] = rows
    day_keys = [(r["spot_id"], r["fecha"]) for r in tables["daily_status"]]
    if len(day_keys) != len(set(day_keys)):
        raise ValueError("daily_status: hay más de un registro para el mismo Spot y día")
    return {"generated_at": data.get("generated_at") if isinstance(data.get("generated_at"), str) else None, "tables": tables}


def _plan_restore(parsed: dict, mode: str, db: Session) -> dict:
    """Arma qué filas se insertarían en `mode` y qué se omite o desvincula porque
    apunta a algo que no existe. No escribe nada."""
    t = parsed["tables"]
    if mode == "full":
        user_ids = {r["id"] for r in t["users"]}
        truck_ids = {r["id"] for r in t["trucks"]}
        spot_ids = {r["id"] for r in t["spots"]}
    elif mode == "history":
        user_ids = {i for (i,) in db.query(models.User.id)}
        truck_ids = {i for (i,) in db.query(models.Truck.id)}
        spot_ids = {i for (i,) in db.query(models.Spot.id)}
    else:
        raise ValueError("Modo de restauración inválido")

    notes = {"spots_truck_unlinked": 0, "daily_skipped": 0, "daily_user_unlinked": 0, "daily_truck_reset": 0, "log_user_unlinked": 0, "log_spot_unlinked": 0}
    plan = {}
    if mode == "full":
        plan["users"] = [dict(r) for r in t["users"]]
        plan["trucks"] = [dict(r) for r in t["trucks"]]
        spots = []
        for r in t["spots"]:
            r = dict(r)
            if r["truck_id"] is not None and r["truck_id"] not in truck_ids:
                r["truck_id"] = None
                notes["spots_truck_unlinked"] += 1
            spots.append(r)
        plan["spots"] = spots
        plan["labels"] = [dict(r) for r in t["labels"]]

    daily = []
    for r in t["daily_status"]:
        r = dict(r)
        if r["spot_id"] not in spot_ids:
            notes["daily_skipped"] += 1
            continue
        if r["updated_by_id"] is not None and r["updated_by_id"] not in user_ids:
            r["updated_by_id"] = None
            notes["daily_user_unlinked"] += 1
        if r["truck_id"] is not None and r["truck_id"] not in truck_ids:
            # el camión de la reasignación de ese día ya no existe: se vuelve al oficial del Spot
            r["truck_id"], r["truck_overridden"] = None, False
            notes["daily_truck_reset"] += 1
        daily.append(r)
    plan["daily_status"] = daily

    log = []
    for r in t["activity_log"]:
        r = dict(r)
        if r["user_id"] is not None and r["user_id"] not in user_ids:
            r["user_id"] = None
            notes["log_user_unlinked"] += 1
        if r["spot_id"] is not None and r["spot_id"] not in spot_ids:
            r["spot_id"] = None
            notes["log_spot_unlinked"] += 1
        log.append(r)
    plan["activity_log"] = log
    return {"tables": plan, "notes": notes}


def _full_mode_error(parsed: dict) -> str | None:
    users = parsed["tables"]["users"]
    if not any(u["role"] == "super_admin" and u["active"] for u in users):
        return "El respaldo no trae ningún Super Admin activo; restaurarlo dejaría el sistema sin nadie que pueda administrarlo"
    names = [u["username"] for u in users]
    if len(names) != len(set(names)):
        return "El respaldo tiene nombres de usuario repetidos"
    codes = [s["code"] for s in parsed["tables"]["spots"]]
    if len(codes) != len(set(codes)):
        return "El respaldo tiene códigos de Spot repetidos"
    return None


def _date_span(rows: list[dict]) -> tuple[str, str]:
    fechas = [r["fecha"] for r in rows]
    return (min(fechas).isoformat(), max(fechas).isoformat()) if fechas else ("", "")


async def _read_backup_upload(file: UploadFile) -> dict:
    raw = await file.read(MAX_BACKUP_BYTES + 1)
    if len(raw) > MAX_BACKUP_BYTES:
        raise HTTPException(413, "El archivo es demasiado grande (máximo 60 MB)")
    try:
        return _parse_backup(raw)
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.post("/admin/config/restore-preview")
async def admin_config_restore_preview(
    file: UploadFile = File(...),
    _: models.User = Depends(require_super_admin),
    db: Session = Depends(get_db),
):
    parsed = await _read_backup_upload(file)
    t = parsed["tables"]
    span = _date_span(t["daily_status"] + t["activity_log"])
    history = _plan_restore(parsed, "history", db)
    full_error = _full_mode_error(parsed)
    return {
        "generated_at": parsed["generated_at"],
        "counts": {k: len(v) for k, v in t.items()},
        "fecha_min": span[0],
        "fecha_max": span[1],
        "history": {
            "daily_status": len(history["tables"]["daily_status"]),
            "activity_log": len(history["tables"]["activity_log"]),
            "notes": history["notes"],
        },
        "full_error": full_error,
        "current": {
            "daily_status": db.query(models.DailyStatus).count(),
            "activity_log": db.query(models.ActivityLog).count(),
        },
    }


def _reset_sequences(db: Session, tables: list[str]) -> None:
    """Postgres no avanza solo su contador de ids al insertar ids explícitos: sin
    esto, el siguiente registro nuevo chocaría con uno restaurado."""
    if db.get_bind().dialect.name != "postgresql":
        return
    for name in tables:
        db.execute(text(f"SELECT setval(pg_get_serial_sequence('{name}', 'id'), COALESCE((SELECT MAX(id) FROM {name}), 0) + 1, false)"))


@router.post("/admin/config/restore")
async def admin_config_restore(
    request: Request,
    file: UploadFile = File(...),
    mode: str = Form(...),
    confirm: str = Form(""),
    current_user: models.User = Depends(require_super_admin),
    db: Session = Depends(get_db),
):
    if confirm.strip().upper() != RESTORE_CONFIRM_WORD:
        raise HTTPException(400, f"Escribe {RESTORE_CONFIRM_WORD} para confirmar")
    parsed = await _read_backup_upload(file)
    if mode == "full":
        error = _full_mode_error(parsed)
        if error:
            raise HTTPException(400, error)
    try:
        plan = _plan_restore(parsed, mode, db)
    except ValueError as exc:
        raise HTTPException(400, str(exc))

    actor_username, actor_name = current_user.username, current_user.full_name
    tables = plan["tables"]
    insert_order = (["users", "trucks", "spots", "labels"] if mode == "full" else []) + ["daily_status", "activity_log"]
    delete_order = ["activity_log", "daily_status"] + (["labels", "spots", "trucks", "users"] if mode == "full" else [])
    try:
        for table in delete_order:
            db.query(_BACKUP_MODELS[table]).delete(synchronize_session=False)
        db.flush()
        for table in insert_order:
            if tables[table]:
                db.bulk_insert_mappings(_BACKUP_MODELS[table], tables[table])
        db.flush()
        _reset_sequences(db, [_BACKUP_TABLE_NAMES[t] for t in insert_order])
        db.expire_all()  # los objetos que la sesión tenía en memoria (p. ej. el usuario actual) ya no valen

        skipped = plan["notes"]["daily_skipped"]
        actor = db.query(models.User).filter_by(username=actor_username).first()
        origin = f"respaldo del {parsed['generated_at']}" if parsed["generated_at"] else "un respaldo"
        detail = (
            f"{'Restauración COMPLETA' if mode == 'full' else 'Historial restaurado'} desde {origin} por {actor_name} ({actor_username}): "
            f"{len(tables['daily_status'])} estatus diarios y {len(tables['activity_log'])} eventos"
            + (f"; {skipped} estatus omitidos (su Spot ya no existe)" if skipped else "")
        )
        log_activity(db, actor, "records_restore", detail=detail)
        db.commit()
    except Exception as exc:
        db.rollback()
        print(f"[restore] falló y se revirtió todo: {exc!r}")
        raise HTTPException(500, "No se pudo restaurar el respaldo; no se hizo ningún cambio")

    await manager.broadcast({"type": "layout_update"})
    if mode == "full":
        request.session.clear()  # los ids de usuario pueden haber cambiado
        return RedirectResponse("/login?info=restaurado", status_code=303)
    return RedirectResponse(
        f"/admin/config?restored_ds={len(tables['daily_status'])}&restored_al={len(tables['activity_log'])}&restored_skipped={skipped}",
        status_code=303,
    )
