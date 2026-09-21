from datetime import datetime

from sqlalchemy.orm import Session

from app import models
from app.local_time import operational_date, operational_today, to_local


def log_activity(
    db: Session,
    user: models.User | None,
    action: str,
    spot_id: int | None = None,
    detail: str = "",
    when: datetime | None = None,
) -> None:
    """Escribe un renglón de bitácora. No hace commit -- se guarda junto con
    el commit del endpoint que ya está mutando algo, para no duplicar
    round-trips a la base de datos ni dejarlo a medias si algo más falla.

    `when` (UTC naive) permite sellar el evento con la hora en que REALMENTE
    ocurrió (un cambio marcado sin señal y enviado después); si se omite, se usa
    la hora del servidor al insertar."""
    extra = {}
    if when is not None:
        extra["timestamp"] = when
        fecha = operational_date(to_local(when))
    else:
        fecha = operational_today()
    db.add(
        models.ActivityLog(
            fecha=fecha,
            user_id=user.id if user else None,
            action=action,
            spot_id=spot_id,
            detail=detail,
            **extra,
        )
    )
