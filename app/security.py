import base64
import hashlib
import hmac
import os

_ITERATIONS = 200_000

# Contraseña con la que se crea la cuenta inicial: conocida por definición, así que
# el sistema obliga a cambiarla (ver flag_default_passwords en main.py).
DEFAULT_PASSWORD = "admin123"


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _ITERATIONS)
    return f"{base64.b64encode(salt).decode()}${base64.b64encode(dk).decode()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        salt_b64, dk_b64 = stored.split("$")
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(dk_b64)
    except (ValueError, TypeError):
        return False
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _ITERATIONS)
    return hmac.compare_digest(dk, expected)
