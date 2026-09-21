import os

from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./dc_map.db")
SESSION_SECRET = os.getenv("SESSION_SECRET", "change-me-in-production-please")

# Zona horaria del CD y horario del turno. Railway corre en UTC (El Salvador es
# UTC-6), así que NO se puede confiar en la zona del servidor: se fija aquí.
#
# El turno de noche (Conductor de Patio) va de SHIFT_START_HOUR (10 PM) a
# SHIFT_END_HOUR (10 AM) y cruza la medianoche. Todo lo que se carga en ese
# turno pertenece a la carga del día en que TERMINA (lo cargado de 10 PM del 18
# a 10 AM del 19 es la carga del 19). Fuera de ese horario el tablero solo se
# puede consultar (ver app/shift.py).
APP_TIMEZONE = os.getenv("APP_TIMEZONE", "America/El_Salvador")
SHIFT_START_HOUR = int(os.getenv("SHIFT_START_HOUR", "22"))
SHIFT_END_HOUR = int(os.getenv("SHIFT_END_HOUR", "10"))
if not (0 <= SHIFT_END_HOUR < SHIFT_START_HOUR <= 23):
    raise ValueError("SHIFT_START_HOUR debe ser mayor que SHIFT_END_HOUR (el turno cruza la medianoche)")
