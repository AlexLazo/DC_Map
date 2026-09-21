"""Hora local del CD -- zona fija (APP_TIMEZONE), NUNCA la del servidor.

Dos problemas reales que esto resuelve (ver Ronda 20 en la memoria):

1. `DailyStatus.updated_at`/`ActivityLog.timestamp` usan `func.now()`, que
   guarda UTC sin marca de zona. Mostrarlos tal cual (o convertirlos con la
   zona del SERVIDOR) muestra la hora equivocada en cualquier host que no
   esté en la zona del CD: Railway corre en UTC, y un reporte de las 5 PM en
   El Salvador (UTC-6) aparecía como las 11 PM.
2. `date.today()` en el servidor también es la fecha UTC, así que el "día"
   del tablero cambiaba a las 6 PM hora local. Aquí el día operativo es el del
   TURNO (10 PM -> 10 AM): todo lo que se carga en ese turno cuenta para el
   día en que TERMINA, y el día cambia a las 10 PM (cuando empieza el turno
   siguiente), no a medianoche ni a las 10 AM.
"""

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app.config import APP_TIMEZONE, SHIFT_START_HOUR

LOCAL_TZ = ZoneInfo(APP_TIMEZONE)


def to_local(dt: datetime) -> datetime:
    """UTC naive (como sale de la DB) -> hora local naive del CD."""
    return dt.replace(tzinfo=timezone.utc).astimezone(LOCAL_TZ).replace(tzinfo=None)


def local_now() -> datetime:
    return datetime.now(LOCAL_TZ).replace(tzinfo=None)


def operational_date(local_dt: datetime) -> date:
    """Día operativo de un momento local = el día en que TERMINA el turno al
    que pertenece. Ej.: las 11 PM del 18, la 1 AM del 19 y las 9:59 AM del 19
    son todos "día 19" (la carga de 10 PM del 18 a 10 AM del 19 es la carga
    del 19). De 10 AM a 10 PM (fuera de turno) sigue siendo el día del turno
    que acaba de terminar -- así el tablero muestra sus resultados hasta que
    a las 10 PM arranca el día siguiente en blanco.

    Se logra sumando las horas que faltan para medianoche desde el inicio del
    turno: a las 10 PM (22:00) + 2 h = 00:00 del día siguiente."""
    return (local_dt + timedelta(hours=24 - SHIFT_START_HOUR)).date()


def operational_today() -> date:
    return operational_date(local_now())
