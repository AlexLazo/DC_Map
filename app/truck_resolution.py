"""Resuelve qué camión mostrar en un Spot para una fecha dada: si esa fecha
tuvo una reasignación ("Asignar/cambiar camión", ver `truck_overridden` en
`DailyStatus`), ese es el camión vigente ese día -- puede ser `None` si se
quitó el camión ese día. Si no hubo reasignación ese día, se usa el camión
oficial del Spot (`Spot.truck`, el que trae el Excel). Compartido por
`board.py` (/api/board, el tablero del día) y `admin.py` (dashboard/reportes
de un día concreto) para que ambos muestren siempre el mismo camión."""

from app import models


def resolve_truck(spot: models.Spot, daily_status: models.DailyStatus | None) -> models.Truck | None:
    if daily_status is not None and daily_status.truck_overridden:
        return daily_status.truck
    return spot.truck
