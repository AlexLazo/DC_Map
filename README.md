# DC Map — Control de Carga (CD Soyapango)

App web para digitalizar el layout de parqueo del Centro de Distribución: cada
Spot muestra su Ruta, el camión asignado (HOD/Placa/SV) y su estado de carga
del día (Pendiente / Carga en Piso / Cargado / No Cargado), en tiempo real
para Distribución y Almacén.

## Requisitos

- Python 3.10+
- El archivo `LAY OUT PARQUEO (CD SOYAPANGO) V2.xlsx` en la raíz del proyecto (ya está ahí).

## Instalación

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
```

## Importar el layout desde Excel (una sola vez)

```bash
python scripts/import_layout.py
```

Esto crea la base de datos SQLite (`dc_map.db`) y siembra:
- ~120 Spots con su Ruta y posición real tomada del Excel.
- Catálogo de camiones (Placa/SV/HOD) detectados en la hoja `PARQUEOS `.
- Etiquetas decorativas del mapa (BODEGA, TALLER, PORTON, etc.) para dar contexto visual.

**Importante:** el Excel no vincula Ruta↔Placa/SV de forma inequívoca (son dos
hojas independientes), así que los Spots quedan **sin camión asignado**. Desde
el tablero, con un usuario de rol Distribución o Admin, haz clic en cada Spot
y usa "Asignar / cambiar camión" para completar esa asignación una vez — es un
buscador rápido por placa, HOD o SV. Puedes volver a correr el script si el
Excel cambia; no duplica datos, solo sincroniza posiciones/rutas.

## Correr la app

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Ábrela en `http://localhost:8000` (o `http://<IP-de-esta-PC>:8000` desde otra
computadora en la misma red del CD).

En el primer arranque se crea un usuario administrador:

```
usuario: admin
contraseña: admin123
```

**Cámbiala de inmediato** creando tu propio usuario admin desde `/admin` y
desactivando el de por defecto (o al menos cambia su contraseña).

Desde `/admin` (solo rol Admin) crea los usuarios de Distribución y Almacén.

## Roles

- **Almacén**: marca un Spot como "Carga en Piso" o "Cargado".
- **Distribución**: marca "No Cargado" (con comentario obligatorio) o reinicia
  a "Pendiente"; también asigna/reasigna qué camión va en cada Spot.
- **Admin**: todo lo anterior + gestión de usuarios y catálogo de camiones.

El tablero de "hoy" se reinicia solo cada día (cada Spot vuelve a "Pendiente"
si nadie lo ha tocado ese día), pero el historial de días anteriores queda
guardado — usa el selector de fecha en la parte superior para consultarlo
(modo solo lectura).

## Notas para cuando se hostee en otro servidor

- Cambia `SESSION_SECRET` en `.env` por un valor largo y aleatorio.
- Cambia `DATABASE_URL` a Postgres cuando aplique, por ejemplo:
  `postgresql+psycopg2://usuario:password@host/dbname` — el código no cambia,
  solo instala `psycopg2-binary`.
- Corre uvicorn detrás de un proceso persistente (servicio de Windows, Docker,
  systemd, etc.) en vez de `--reload`.
