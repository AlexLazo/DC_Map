"""Horario operativo del tablero: solo se puede operar (marcar estatus, asignar
o corregir camiones) durante el turno de noche, de SHIFT_START_HOUR (10 PM) a
SHIFT_END_HOUR (10 AM). Fuera de ese horario el tablero es de solo consulta.

La regla se aplica AQUÍ, en el servidor -- ocultar botones en la pantalla es
solo cortesía; una tablet con la pantalla vieja o una llamada directa a la API
no se pueden saltar el bloqueo. Admin y Super Admin quedan exentos: necesitan
poder corregir un dato después del turno (ej. un conductor olvidó marcar un
camión) y probar el sistema de día; sus cambios igual quedan en la Bitácora.
"""

from fastapi import HTTPException

from app.config import SHIFT_END_HOUR, SHIFT_START_HOUR
from app.local_time import local_now

EXEMPT_ROLES = {"admin", "super_admin"}


def hour_label(hour: int) -> str:
    return f"{(hour % 12) or 12}:00 {'AM' if hour < 12 else 'PM'}"


def shift_is_open(local_dt=None) -> bool:
    hour = (local_dt or local_now()).hour
    return hour >= SHIFT_START_HOUR or hour < SHIFT_END_HOUR


def can_operate(role: str, local_dt=None) -> bool:
    return shift_is_open(local_dt) or role in EXEMPT_ROLES


def shift_info(role: str) -> dict:
    return {
        "open": shift_is_open(),
        "can_operate": can_operate(role),
        "start_hour": SHIFT_START_HOUR,
        "end_hour": SHIFT_END_HOUR,
        "start_label": hour_label(SHIFT_START_HOUR),
        "end_label": hour_label(SHIFT_END_HOUR),
    }


def require_operating_hours(user, at=None) -> None:
    """`at` = hora LOCAL en que realmente ocurrió la acción. Normalmente es
    "ahora", pero un cambio hecho sin señal y enviado después debe juzgarse con
    la hora en que se marcó, no con la de la reconexión."""
    if not can_operate(user.role, at):
        raise HTTPException(
            403,
            f"Fuera de horario operativo. El tablero solo acepta cambios de {hour_label(SHIFT_START_HOUR)} "
            f"a {hour_label(SHIFT_END_HOUR)}; se habilita a las {hour_label(SHIFT_START_HOUR)}.",
        )
