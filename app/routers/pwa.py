"""App instalable (PWA): manifiesto y service worker.

Se sirven desde la raíz (no desde /static) porque el service worker solo puede
controlar las rutas que están bajo su propia ubicación: /sw.js controla todo el
sitio, /static/sw.js solo /static/.
"""

from fastapi import APIRouter
from fastapi.responses import JSONResponse, Response

router = APIRouter()

MANIFEST = {
    "name": "DC Map · CD Soyapango",
    "short_name": "DC Map",
    "description": "Control de carga de camiones del CD Soyapango",
    "start_url": "/",
    "scope": "/",
    "display": "standalone",
    "orientation": "any",
    "background_color": "#111827",
    "theme_color": "#111827",
    "lang": "es",
    "icons": [
        {"src": "/static/icons/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any"},
        {"src": "/static/icons/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any"},
        {"src": "/static/icons/icon-maskable-512.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
    ],
}

# Solo se guardan en caché los archivos estáticos CON versión (?v=...): al cambiar
# el archivo cambia la URL, así que nunca se sirve una copia vieja. NO se cachea
# HTML ni /api/: contienen datos de la persona que inició sesión, y en una
# tablet compartida no deben quedar en el dispositivo ni servirse a otra persona.
SERVICE_WORKER = """// DC Map service worker (generado por app/routers/pwa.py)
const CACHE = 'dcmap-static-v1';

self.addEventListener('install', () => self.skipWaiting());

self.addEventListener('activate', (event) => {
  event.waitUntil(
    (async () => {
      for (const key of await caches.keys()) if (key !== CACHE) await caches.delete(key);
      await self.clients.claim();
    })()
  );
});

self.addEventListener('fetch', (event) => {
  const req = event.request;
  const url = new URL(req.url);
  const cacheable = req.method === 'GET' && url.origin === self.location.origin && url.pathname.startsWith('/static/') && url.searchParams.has('v');
  if (!cacheable) return; // todo lo demás va directo a la red, sin tocarlo
  event.respondWith(
    (async () => {
      const cache = await caches.open(CACHE);
      const hit = await cache.match(req);
      if (hit) return hit;
      const res = await fetch(req);
      if (res.ok) cache.put(req, res.clone());
      return res;
    })()
  );
});
"""


@router.get("/manifest.webmanifest")
async def manifest():
    return JSONResponse(MANIFEST, media_type="application/manifest+json")


@router.get("/sw.js")
async def service_worker():
    return Response(
        SERVICE_WORKER,
        media_type="application/javascript",
        # El navegador debe revisar el service worker en cada visita para poder actualizarlo.
        headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/"},
    )
