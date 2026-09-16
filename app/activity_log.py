from datetime import date

from sqlalchemy.orm import Session

from app import models


def log_activity(db: Session, user: models.User | None, action: str, spot_id: int | None = None, detail: str = "") -> None:
    """Escribe un renglón de bitácora. No hace commit -- se guarda junto con
    el commit del endpoint que ya está mutando algo, para no duplicar
    round-trips a la base de datos ni dejarlo a medias si algo más falla."""
    db.add(
        models.ActivityLog(
            fecha=date.today(),
            user_id=user.id if user else None,
            action=action,
            spot_id=spot_id,
            detail=detail,
        )
    )
