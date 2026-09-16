"""Convierte un datetime que salió de la DB (SQLite `func.now()` siempre
devuelve UTC, sin marca de zona horaria) a la hora local del servidor.

Sin esto, cualquier `DailyStatus.updated_at`/`ActivityLog.timestamp` mostrado
tal cual aparece desfasado por el offset UTC del servidor -- un camión
cargado a la 1AM local se leía como si hubiera sido a otra hora. Ver Ronda 16
en la memoria del proyecto: esto ya afectaba silenciosamente la columna
"Hora" de la Bitácora antes de que se notara."""

from datetime import datetime, timezone


def to_local(dt: datetime) -> datetime:
    return dt.replace(tzinfo=timezone.utc).astimezone().replace(tzinfo=None)
