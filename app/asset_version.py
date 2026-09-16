from pathlib import Path

STATIC_DIR = Path(__file__).resolve().parent / "static"


def asset_version() -> str:
    """El navegador cachea agresivamente board.js/styles.css entre despliegues
    -- una recarga normal (incluso F5) a veces no baja el archivo nuevo. Se
    ata la URL de CADA página que use styles.css/board.js a la fecha de
    modificación real de esos archivos, para forzar descarga cuando cambian,
    sin depender de que alguien recuerde incrementar un número de versión a
    mano. Compartido por board.py y admin.py -- toda plantilla que incluya
    `/static/css/styles.css` debe pasar esto como `asset_version` en su
    contexto, o quedará sirviendo CSS viejo cacheado indefinidamente."""
    try:
        mtimes = [(STATIC_DIR / "js" / "board.js").stat().st_mtime, (STATIC_DIR / "css" / "styles.css").stat().st_mtime]
        return str(int(max(mtimes)))
    except OSError:
        return "0"
