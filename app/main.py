import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app import models
from app.config import SESSION_SECRET
from app.database import Base, SessionLocal, engine
from app.excel_sync import EXCEL_PATH, sync_truck_data
from app.routers import admin as admin_router
from app.routers import auth as auth_router
from app.routers import board as board_router
from app.routers import ws as ws_router
from app.security import hash_password
from app.ws_manager import manager

BASE_DIR = Path(__file__).resolve().parent
EXCEL_POLL_SECONDS = 20


def ensure_default_admin() -> None:
    db = SessionLocal()
    try:
        if not db.query(models.User).first():
            db.add(
                models.User(
                    username="admin",
                    password_hash=hash_password("admin123"),
                    full_name="Administrador",
                    role="super_admin",
                    active=True,
                )
            )
            db.commit()
            print("=" * 60)
            print("Usuario admin creado -> usuario: admin / contraseña: admin123")
            print("Cambia esta contraseña desde /admin apenas ingreses.")
            print("=" * 60)
    finally:
        db.close()


async def _watch_excel_for_changes() -> None:
    """Revisa cada EXCEL_POLL_SECONDS si el Excel cambió (por fecha de
    modificación) y, si cambió, actualiza HOD/Placa/SV/supervisor de los
    Spots que ya existen y crea los nuevos -- ver `sync_truck_data()` en
    `app/excel_sync.py` para las garantías de qué NUNCA toca (posiciones
    movidas a mano, borrados). Si el archivo está bloqueado porque alguien
    lo tiene abierto/guardando en Excel en ese instante, ese ciclo se salta
    y se reintenta en el siguiente."""
    try:
        last_mtime = EXCEL_PATH.stat().st_mtime
    except OSError:
        last_mtime = None

    while True:
        await asyncio.sleep(EXCEL_POLL_SECONDS)
        try:
            mtime = EXCEL_PATH.stat().st_mtime
        except OSError:
            continue
        if last_mtime is not None and mtime == last_mtime:
            continue
        last_mtime = mtime

        db = SessionLocal()
        try:
            stats = sync_truck_data(db)
        except Exception as exc:  # el archivo pudo estar a medio guardar
            print(f"[excel-sync] no se pudo sincronizar (se reintenta en {EXCEL_POLL_SECONDS}s): {exc}")
            continue
        finally:
            db.close()

        if "error" in stats:
            print(f"[excel-sync] {stats['error']}")
            continue
        print(f"[excel-sync] Excel actualizado -> {stats}")
        if stats["created"] or stats["updated"] or stats["moved"]:
            await manager.broadcast({"type": "layout_update"})


def _sync_excel_on_startup() -> None:
    """Corre `sync_truck_data()` una vez al arrancar, además del watcher de
    abajo. Hace falta porque en un host como Railway el Excel llega nuevo en
    CADA deploy (viene del repo de git) pero no vuelve a cambiar mientras ese
    contenedor sigue vivo -- sin esto, el watcher (que solo dispara cuando el
    mtime CAMBIA en caliente) nunca sincronizaría nada en producción."""
    db = SessionLocal()
    try:
        stats = sync_truck_data(db)
    except Exception as exc:
        print(f"[excel-sync] no se pudo sincronizar al arrancar: {exc}")
        return
    finally:
        db.close()
    if "error" in stats:
        print(f"[excel-sync] {stats['error']}")
    else:
        print(f"[excel-sync] Sincronizado al arrancar -> {stats}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(engine)
    ensure_default_admin()
    _sync_excel_on_startup()
    watcher = asyncio.create_task(_watch_excel_for_changes())
    yield
    watcher.cancel()


app = FastAPI(title="DC Map - Control de Carga", lifespan=lifespan)
app.add_middleware(SessionMiddleware, secret_key=SESSION_SECRET)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

app.include_router(auth_router.router)
app.include_router(board_router.router)
app.include_router(admin_router.router)
app.include_router(ws_router.router)
