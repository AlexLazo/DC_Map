// Registra el service worker: es lo que permite "Instalar app" / "Agregar a la
// pantalla de inicio" y cargar más rápido los archivos estáticos.
if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/sw.js').catch(() => {});
  });
}
