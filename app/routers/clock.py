from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.config import APP_TIMEZONE, SHIFT_END_HOUR, SHIFT_START_HOUR
from app.database import get_db
from app.local_time import operational_today

router = APIRouter()


@router.get("/api/time")
async def server_time(request: Request, db: Session = Depends(get_db)):
    """Hora del SERVIDOR (no la del dispositivo) para el reloj de la esquina:
    su propósito es que cualquiera pueda comparar contra su reloj y confirmar
    que la hora con la que la app sella los eventos es la correcta."""
    if not get_current_user(request, db):
        raise HTTPException(401, "No autenticado")
    return {
        "utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "tz": APP_TIMEZONE,
        "operational_date": operational_today().isoformat(),
        "shift_start_hour": SHIFT_START_HOUR,
        "shift_end_hour": SHIFT_END_HOUR,
    }
