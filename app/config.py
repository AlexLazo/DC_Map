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

# --- Correo diario de cierre ---------------------------------------------------
# Hora LOCAL (APP_TIMEZONE) a la que sale el resumen. A las 9:00 AM el turno de
# noche aún no termina (cierra a las SHIFT_END_HOUR), así que el correo es el
# estado "al corte de las 9". Si la app estaba apagada a esa hora, se envía al
# arrancar siempre que estemos dentro de DIGEST_CATCHUP_HOURS horas del corte.
DIGEST_HOUR = int(os.getenv("DIGEST_HOUR", "9"))
DIGEST_MINUTE = int(os.getenv("DIGEST_MINUTE", "0"))
DIGEST_CATCHUP_HOURS = int(os.getenv("DIGEST_CATCHUP_HOURS", "3"))

# Proveedor de correo. Se usa Resend (HTTPS) si hay RESEND_API_KEY; si no, SMTP si
# hay SMTP_HOST. Railway bloquea SMTP en varios planes, por eso Resend es lo normal.
MAIL_FROM = os.getenv("MAIL_FROM", "")
RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")
SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
# URL pública de la app, para el botón "Abrir tablero" del correo (opcional).
APP_BASE_URL = os.getenv("APP_BASE_URL", "")
