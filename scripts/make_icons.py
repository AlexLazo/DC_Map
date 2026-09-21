"""Genera los íconos de la app instalable (PWA) en app/static/icons/.

Se corre a mano, una sola vez (o si se quiere cambiar el diseño); los PNG
resultantes se suben al repositorio, así que Railway NO necesita Pillow.

    python scripts/make_icons.py
"""

from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parent.parent / "app" / "static" / "icons"
BG = (17, 24, 39)  # #111827, igual que la barra superior de la app
S = 1024  # se dibuja grande y se reduce para que los bordes queden suaves


def draw_truck(d: ImageDraw.ImageDraw, scale: float) -> None:
    """Camión con palomita de "cargado", centrado; `scale` deja margen de
    seguridad (los íconos "maskable" se recortan en círculo/squircle)."""
    cx, cy = S / 2, S / 2

    def p(x: float, y: float) -> tuple[float, float]:
        return cx + (x - 512) * scale, cy + (y - 540) * scale

    def rect(x0, y0, x1, y1, **kw):
        d.rectangle([*p(x0, y0), *p(x1, y1)], **kw)

    def poly(points, **kw):
        d.polygon([p(x, y) for x, y in points], **kw)

    def circle(x, y, r, **kw):
        a, b = p(x - r, y - r), p(x + r, y + r)
        d.ellipse([*a, *b], **kw)

    rect(190, 330, 630, 640, fill=(229, 231, 235))                                   # caja de carga
    poly([(650, 430), (790, 430), (860, 545), (860, 640), (650, 640)], fill=(59, 130, 246))  # cabina
    poly([(680, 460), (768, 460), (820, 545), (680, 545)], fill=(219, 234, 254))     # ventana
    rect(190, 640, 860, 672, fill=(156, 163, 175))                                   # chasis
    for wx in (330, 730):
        circle(wx, 700, 66, fill=BG, outline=(229, 231, 235), width=int(14 * scale))
        circle(wx, 700, 22, fill=(156, 163, 175))
    circle(410, 485, 84, fill=(21, 128, 61))                                         # insignia "cargado"
    d.line([p(365, 488), p(398, 522), p(458, 450)], fill="white", width=int(24 * scale), joint="curve")


def render(size: int, *, rounded: bool, scale: float) -> Image.Image:
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    if rounded:
        d.rounded_rectangle([0, 0, S - 1, S - 1], radius=int(S * 0.2), fill=BG)
    else:
        d.rectangle([0, 0, S, S], fill=BG)
    draw_truck(d, scale)
    return img.resize((size, size), Image.LANCZOS)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    render(192, rounded=True, scale=1.0).save(OUT / "icon-192.png")
    render(512, rounded=True, scale=1.0).save(OUT / "icon-512.png")
    # maskable: fondo a sangre y dibujo dentro del ~70% central (zona segura)
    render(512, rounded=False, scale=0.74).save(OUT / "icon-maskable-512.png")
    # iOS no admite transparencia en el ícono de la pantalla de inicio
    render(180, rounded=False, scale=0.86).convert("RGB").save(OUT / "icon-180.png")
    print("Íconos generados en", OUT)


if __name__ == "__main__":
    main()
