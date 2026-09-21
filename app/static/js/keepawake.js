// Mantiene la pantalla encendida (Screen Wake Lock API) mientras la página lo
// pida: en una tablet de patio que nadie toca por ratos, la pantalla se apagaría
// y el tablero dejaría de verse. El navegador suelta el bloqueo solo cuando la
// pestaña queda oculta, así que se vuelve a pedir al regresar. Donde el navegador
// no la soporta (iOS viejo, http sin candado) simplemente no hace nada.
(function () {
  let sentinel = null;
  let wanted = false;

  function paint() {
    const clock = document.getElementById('live-clock');
    if (clock) clock.classList.toggle('awake', !!sentinel);
  }

  async function acquire() {
    if (!wanted || sentinel || !('wakeLock' in navigator) || document.hidden) return;
    try {
      sentinel = await navigator.wakeLock.request('screen');
      sentinel.addEventListener('release', () => {
        sentinel = null;
        paint();
      });
    } catch (err) {
      sentinel = null; // ahorro de batería activo, permiso denegado, etc.
    }
    paint();
  }

  function release() {
    if (sentinel) sentinel.release().catch(() => {});
    sentinel = null;
    paint();
  }

  window.KeepAwake = {
    supported: 'wakeLock' in navigator,
    active: () => !!sentinel,
    set(on) {
      wanted = !!on;
      if (wanted) acquire();
      else release();
    },
  };

  document.addEventListener('visibilitychange', () => {
    if (!document.hidden) acquire();
  });
})();
