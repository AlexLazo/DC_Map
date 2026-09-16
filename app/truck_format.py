"""Normaliza el código SV de un camión a 4 dígitos con cero(s) a la
izquierda (ej. "745" -> "0745"). El Excel a veces guarda la celda como
número (pierde cualquier cero a la izquierda) y a veces como texto (lo
preserva), así que sin esto el mismo tipo de código aparece de dos formas
distintas según de qué celda salió. Los que ya tienen 4+ dígitos (6015,
6014...) se dejan tal cual -- rellenarlos los volvería de 5 dígitos, que ya
no es el mismo código. Los que no son puramente numéricos (ej. "F023")
también se dejan tal cual -- no hay forma segura de rellenar eso."""


def format_sv_code(value) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.isdigit() and len(text) < 4:
        return text.zfill(4)
    return text
