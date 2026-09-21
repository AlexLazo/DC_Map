from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, false, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    full_name: Mapped[str] = mapped_column(String(120))
    role: Mapped[str] = mapped_column(String(20))  # super_admin | admin | supervisor | conductor_patio
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    # True = debe crear una contraseña nueva antes de usar el sistema (tras un
    # restablecimiento por un admin, o mientras siga la contraseña de fábrica).
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())


class Truck(Base):
    __tablename__ = "trucks"

    id: Mapped[int] = mapped_column(primary_key=True)
    hod_code: Mapped[str | None] = mapped_column(String(30), nullable=True)
    placa: Mapped[str | None] = mapped_column(String(30), nullable=True)
    sv_code: Mapped[str | None] = mapped_column(String(30), nullable=True)
    source_cell: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)

    spots: Mapped[list["Spot"]] = relationship(back_populates="truck")


class Spot(Base):
    __tablename__ = "spots"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    supervisor_name: Mapped[str | None] = mapped_column(String(40), nullable=True)
    color_hex: Mapped[str | None] = mapped_column(String(7), nullable=True)
    grid_row: Mapped[int] = mapped_column(Integer)
    grid_col: Mapped[int] = mapped_column(Integer)
    row_span: Mapped[int] = mapped_column(Integer, default=1)
    col_span: Mapped[int] = mapped_column(Integer, default=1)
    truck_id: Mapped[int | None] = mapped_column(ForeignKey("trucks.id"), nullable=True)

    truck: Mapped["Truck | None"] = relationship(back_populates="spots")
    statuses: Mapped[list["DailyStatus"]] = relationship(back_populates="spot")


class MapLabel(Base):
    """Etiquetas de zona del layout (BODEGA, OFICINAS, TALLER, etc.) para dar
    contexto visual — no son Spots, no tienen estado ni camión."""

    __tablename__ = "map_labels"

    id: Mapped[int] = mapped_column(primary_key=True)
    text: Mapped[str] = mapped_column(String(120))
    grid_row: Mapped[int] = mapped_column(Integer)
    grid_col: Mapped[int] = mapped_column(Integer)
    row_span: Mapped[int] = mapped_column(Integer, default=1)
    col_span: Mapped[int] = mapped_column(Integer, default=1)


class DailyStatus(Base):
    __tablename__ = "daily_status"
    __table_args__ = (UniqueConstraint("spot_id", "fecha", name="uq_spot_fecha"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    spot_id: Mapped[int] = mapped_column(ForeignKey("spots.id"))
    fecha: Mapped[date] = mapped_column(Date, index=True)
    status: Mapped[str] = mapped_column(String(20), default="pendiente")
    comentario: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
    # Reasignación de camión SOLO para este día (ver app/truck_resolution.py):
    # `truck_overridden=True` marca que hubo un "Asignar/cambiar camión" ESE
    # día -- `truck_id` es el camión vigente ese día (puede ser None = se
    # quitó el camión ese día). Si `truck_overridden` es False, el día no
    # tuvo reasignación y el Spot muestra su camión oficial (`Spot.truck`,
    # el que trae el Excel) -- así una reasignación de hace 3 días no se
    # queda pegada indefinidamente.
    truck_id: Mapped[int | None] = mapped_column(ForeignKey("trucks.id"), nullable=True)
    truck_overridden: Mapped[bool] = mapped_column(Boolean, default=False)

    spot: Mapped["Spot"] = relationship(back_populates="statuses")
    updated_by: Mapped["User | None"] = relationship()
    truck: Mapped["Truck | None"] = relationship()


class ActivityLog(Base):
    """Bitácora: un renglón por cada acción que cambia algo (estatus,
    asignación, edición de camión, movimiento/creación/borrado de Spot o
    etiqueta, alta de usuario). A diferencia de `DailyStatus` (que guarda
    solo el estado ACTUAL de cada Spot por día), esto es un historial que
    nunca se sobreescribe -- es la fuente de la Bitácora y los Reportes."""

    __tablename__ = "activity_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    fecha: Mapped[date] = mapped_column(Date, index=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    action: Mapped[str] = mapped_column(String(30))
    spot_id: Mapped[int | None] = mapped_column(ForeignKey("spots.id"), nullable=True)
    detail: Mapped[str] = mapped_column(Text, default="")

    user: Mapped["User | None"] = relationship()
    spot: Mapped["Spot | None"] = relationship()
