"""Correo diario de cierre: estatus de las cargas del día operativo, a la hora
fijada en DIGEST_HOUR:DIGEST_MINUTE (9:00 AM hora del CD por defecto).

El resumen sale de los mismos helpers que el Dashboard de /admin
(`_daily_summary`, `_spot_rows`), así que el correo y la pantalla nunca
discrepan. Se importan dentro de las funciones porque admin.py importa este
módulo para el botón "Enviar ahora" (importarlos arriba sería circular).
"""

import asyncio
from datetime import datetime, timedelta, timezone
from html import escape

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import emailer, models
from app.config import APP_BASE_URL, DIGEST_CATCHUP_HOURS, DIGEST_HOUR, DIGEST_MINUTE, SHIFT_END_HOUR
from app.database import SessionLocal
from app.local_time import local_now, operational_date
from app.shift import hour_label

CHECK_SECONDS = 60
MAX_ATTEMPTS = 3
RETRY_AFTER = timedelta(minutes=5)

STATUS_LABELS = {"pendiente": "Pendiente", "carga_en_piso": "Carga en Piso", "cargado": "Cargado", "no_cargado": "No Cargado"}
STATUS_COLORS = {"pendiente": "#6b7280", "carga_en_piso": "#d97706", "cargado": "#15803d", "no_cargado": "#b91c1c"}


def digest_time_label() -> str:
    return f"{(DIGEST_HOUR % 12) or 12}:{DIGEST_MINUTE:02d} {'AM' if DIGEST_HOUR < 12 else 'PM'}"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _cell(value, bold=False) -> str:
    style = "padding:6px 10px;border-bottom:1px solid #e5e7eb;font-size:13px;" + ("font-weight:600;" if bold else "")
    return f'<td style="{style}">{escape(str(value)) if value not in (None, "") else "—"}</td>'


def _table(headers: list[str], rows: list[list]) -> str:
    head = "".join(
        f'<th align="left" style="padding:6px 10px;background:#f3f4f6;font-size:12px;color:#374151;">{escape(h)}</th>' for h in headers
    )
    body = "".join("<tr>" + "".join(_cell(c, i == 0) for i, c in enumerate(r)) + "</tr>" for r in rows)
    return f'<table width="100%" cellspacing="0" cellpadding="0" style="border-collapse:collapse;margin:6px 0 18px;">{"<tr>" + head + "</tr>"}{body}</table>'


def build_digest(db: Session, fecha) -> tuple[str, str, str]:
    """(asunto, html, texto) del estado de `fecha` (día operativo) AHORA."""
    from app.routers.admin import _daily_summary, _shift_label, _spot_rows

    summary = _daily_summary(db, fecha)
    prev = _daily_summary(db, fecha - timedelta(days=1))
    rows = _spot_rows(db, fecha)
    counts, pct, total = summary["counts"], summary["pct"], summary["total"]
    delta = summary["pct_cargado"] - prev["pct_cargado"]
    delta_txt = f"{delta:+d} pts vs. ayer ({prev['pct_cargado']}%)"
    corte = digest_time_label()
    fecha_txt = fecha.strftime("%d/%m/%Y")

    by_sup: dict[str, dict] = {}
    no_cargados, faltan = [], []
    for r in rows:
        name = r["supervisor_name"] or "Sin asignar"
        b = by_sup.setdefault(name, {"total": 0, "cargado": 0})
        b["total"] += 1
        b["cargado"] += r["status"] == "cargado"
        placa = r["truck"].placa if r["truck"] else None
        if r["status"] == "no_cargado":
            no_cargados.append([r["code"], placa, r["comentario"], r["updated_by"]])
        elif r["status"] in ("pendiente", "carga_en_piso"):
            faltan.append([r["code"], placa, name, STATUS_LABELS[r["status"]]])
    sup_rows = [[n, f"{b['cargado']} / {b['total']}", f"{round(100 * b['cargado'] / b['total'])}%"] for n, b in sorted(by_sup.items())]

    subject = f"DC Map · Cierre de carga {fecha_txt}: {summary['pct_cargado']}% cargado ({counts['cargado']}/{total})"

    kpis = "".join(
        f'<td align="center" style="padding:10px;border:1px solid #e5e7eb;">'
        f'<div style="font-size:26px;font-weight:700;color:{STATUS_COLORS[k]};">{counts[k]}</div>'
        f'<div style="font-size:12px;color:#374151;">{STATUS_LABELS[k]} · {pct[k]}%</div></td>'
        for k in ("cargado", "carga_en_piso", "pendiente", "no_cargado")
    )
    link = (
        f'<p style="margin:18px 0 0;"><a href="{escape(APP_BASE_URL)}/admin/dashboard" style="color:#1d4ed8;">Abrir el Dashboard</a></p>'
        if APP_BASE_URL
        else ""
    )
    html = (
        '<div style="font-family:Arial,Helvetica,sans-serif;max-width:680px;margin:auto;color:#111827;">'
        f'<h2 style="margin:0 0 4px;">Cierre de carga · CD Soyapango</h2>'
        f'<p style="margin:0 0 14px;color:#4b5563;font-size:13px;">{fecha_txt} · {escape(_shift_label(fecha))} · corte a las {corte}</p>'
        f'<p style="font-size:30px;font-weight:700;margin:0;">{summary["pct_cargado"]}% cargado '
        f'<span style="font-size:14px;font-weight:400;color:#4b5563;">({counts["cargado"]} de {total} Spots · {escape(delta_txt)})</span></p>'
        f'<table width="100%" cellspacing="6" cellpadding="0" style="margin:14px 0;"><tr>{kpis}</tr></table>'
        "<h3 style='margin:16px 0 0;'>Avance por supervisor</h3>"
        + _table(["Supervisor", "Cargados", "Avance"], sup_rows)
        + f"<h3 style='margin:16px 0 0;'>No cargados ({len(no_cargados)})</h3>"
        + (_table(["Spot", "Placa", "Comentario", "Marcado por"], no_cargados) if no_cargados else "<p style='font-size:13px;'>Ninguno.</p>")
        + f"<h3 style='margin:16px 0 0;'>Aún por cargar ({len(faltan)})</h3>"
        + (_table(["Spot", "Placa", "Supervisor", "Estatus"], faltan) if faltan else "<p style='font-size:13px;'>Todo cargado o con incidencia registrada.</p>")
        + f'<p style="font-size:12px;color:#6b7280;">El turno cierra a las {hour_label(SHIFT_END_HOUR)}; las cifras son al momento del envío.</p>'
        + link
        + "</div>"
    )

    text_lines = [
        f"Cierre de carga · CD Soyapango · {fecha_txt} (corte {corte})",
        f"{summary['pct_cargado']}% cargado: {counts['cargado']} de {total} Spots ({delta_txt})",
        f"Cargado {counts['cargado']} · Carga en Piso {counts['carga_en_piso']} · Pendiente {counts['pendiente']} · No Cargado {counts['no_cargado']}",
        "",
        "Avance por supervisor:",
        *[f"  {n}: {c} ({p})" for n, c, p in sup_rows],
        "",
        f"No cargados ({len(no_cargados)}):",
        *[f"  {c} · {p or '—'} · {com or 'sin comentario'}" for c, p, com, _ in no_cargados],
        "",
        f"Aún por cargar ({len(faltan)}):",
        *[f"  {c} · {p or '—'} · {s} · {e}" for c, p, s, e in faltan],
    ]
    return subject, html, "\n".join(text_lines)


def active_recipients(db: Session) -> list[str]:
    return [r.email for r in db.query(models.EmailRecipient).filter_by(active=True).order_by(models.EmailRecipient.email)]


def send_digest(db: Session, fecha=None) -> int:
    """Envía el resumen a los destinatarios activos y devuelve cuántos son.
    Lanza `emailer.MailError` si no hay proveedor/destinatarios o el proveedor falla."""
    fecha = fecha or operational_date(local_now())
    to = active_recipients(db)
    subject, html, text = build_digest(db, fecha)
    emailer.send_email(to, subject, html, text)
    return len(to)


def _scheduled_tick() -> None:
    now = local_now()
    due = now.replace(hour=DIGEST_HOUR, minute=DIGEST_MINUTE, second=0, microsecond=0)
    if not (due <= now <= due + timedelta(hours=DIGEST_CATCHUP_HOURS)):
        return
    fecha = operational_date(now)

    db = SessionLocal()
    try:
        if not emailer.provider() or not active_recipients(db):
            return
        log = db.query(models.EmailDigestLog).filter_by(fecha=fecha).first()
        if log is None:
            log = models.EmailDigestLog(fecha=fecha, attempts=1, last_attempt_at=_utcnow())
            db.add(log)
            try:
                db.commit()  # reclama el día: el UNIQUE de `fecha` evita un doble envío
            except IntegrityError:
                db.rollback()
                return
        elif log.ok or log.attempts >= MAX_ATTEMPTS or _utcnow() - log.last_attempt_at < RETRY_AFTER:
            return
        else:
            log.attempts += 1
            log.last_attempt_at = _utcnow()
            db.commit()

        try:
            n = send_digest(db, fecha)
            log.ok, log.detail = True, f"Enviado a {n} destinatario(s)"
            print(f"[correo-diario] {log.detail} ({fecha})")
        except Exception as exc:
            log.ok, log.detail = False, str(exc)[:500]
            print(f"[correo-diario] intento {log.attempts}/{MAX_ATTEMPTS} falló: {exc}")
        db.commit()
    finally:
        db.close()


async def run_digest_scheduler() -> None:
    while True:
        await asyncio.sleep(CHECK_SECONDS)
        try:
            await asyncio.to_thread(_scheduled_tick)
        except Exception as exc:  # que un fallo no mate el ciclo
            print(f"[correo-diario] error inesperado: {exc}")
