"""Recadrage automatique de l'objet sur la photo (Phase 4), sans dépendance autre que Pillow.

Principe : sur une photo prise « sur un fond uni » (consigne de l'écran Ajouter), la couleur du
fond se lit sur les bords de l'image. Les pixels nettement différents de ce fond forment l'objet ;
on garde le cadre englobant des grandes taches (deux pièces côte à côte = les deux) avec une marge.

Le recadrage est prudent : si le fond n'est pas uni, si rien ne se détache, ou si l'objet remplit
déjà la photo, l'image est rendue telle quelle. Les fichiers originaux ne sont jamais modifiés :
seules l'image envoyée au modèle et les miniatures sont recadrées.
"""

from collections import deque
from statistics import median

from PIL import Image, ImageFilter

WORK_SIDE = 160  # analyse sur une copie réduite : rapide (~25 000 pixels) et insensible au bruit fin
BORDER_RATIO = 0.04  # épaisseur des bandes de bord qui servent à estimer le fond
MIN_THRESHOLD = 32  # écart minimal (0-255, sur le canal le plus différent) pour être « objet »
MAX_BORDER_SPREAD = 48  # au-delà, le fond n'est pas uni : pas de recadrage
KEEP_COMPONENT_RATIO = 0.15  # taches gardées : au moins 15 % de la plus grande
MIN_OBJECT_AREA = 0.02  # objet trop petit (< 2 % de la photo) : sans doute du bruit
MAX_OBJECT_AREA = 0.80  # objet déjà cadré (> 80 % de la photo) : inutile de recadrer
MARGIN_RATIO = 0.08  # marge ajoutée autour de l'objet (fraction de sa taille)


def _percentile(values: list[int], ratio: float) -> int:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(len(ordered) * ratio))]


def _components(mask: list[bool], width: int, height: int) -> list[tuple[int, tuple[int, int, int, int]]]:
    """Taches connexes (4-voisinage) du masque : [(aire, (x0, y0, x1, y1))]."""
    seen = bytearray(len(mask))
    found = []
    for start, on in enumerate(mask):
        if not on or seen[start]:
            continue
        seen[start] = 1
        queue = deque([start])
        area, x0, y0, x1, y1 = 0, width, height, 0, 0
        while queue:
            index = queue.popleft()
            y, x = divmod(index, width)
            area += 1
            x0, y0, x1, y1 = min(x0, x), min(y0, y), max(x1, x), max(y1, y)
            for neighbour, inside in (
                (index - 1, x > 0), (index + 1, x < width - 1),
                (index - width, y > 0), (index + width, y < height - 1),
            ):
                if inside and mask[neighbour] and not seen[neighbour]:
                    seen[neighbour] = 1
                    queue.append(neighbour)
        found.append((area, (x0, y0, x1 + 1, y1 + 1)))
    return found


def find_object_box(img: Image.Image) -> tuple[int, int, int, int] | None:
    """Cadre (gauche, haut, droite, bas) de l'objet dans `img` (déjà redressée), ou None."""
    work = img.convert("RGB")
    work.thumbnail((WORK_SIDE, WORK_SIDE))
    width, height = work.size
    if width < 16 or height < 16:
        return None
    pixels = work.get_flattened_data()

    border = max(2, round(min(width, height) * BORDER_RATIO))
    border_pixels = [
        pixels[y * width + x]
        for y in range(height)
        for x in range(width)
        if x < border or x >= width - border or y < border or y >= height - border
    ]
    background = tuple(median(p[c] for p in border_pixels) for c in range(3))

    def distance(p: tuple[int, int, int]) -> int:
        return max(abs(p[0] - background[0]), abs(p[1] - background[1]), abs(p[2] - background[2]))

    spread = _percentile([distance(p) for p in border_pixels], 0.9)
    if spread > MAX_BORDER_SPREAD:
        return None  # fond chargé (motif, plusieurs couleurs) : pas de recadrage fiable
    threshold = max(MIN_THRESHOLD, spread + 20)

    mask_img = Image.new("L", (width, height))
    mask_img.putdata([255 if distance(p) > threshold else 0 for p in pixels])
    # Ouverture morphologique : supprime les poussières et reflets isolés
    mask_img = mask_img.filter(ImageFilter.MinFilter(3)).filter(ImageFilter.MaxFilter(3))
    mask = [v > 0 for v in mask_img.get_flattened_data()]

    components = _components(mask, width, height)
    if not components:
        return None
    largest = max(area for area, _ in components)
    kept = [box for area, box in components if area >= largest * KEEP_COMPONENT_RATIO]
    x0, y0 = min(b[0] for b in kept), min(b[1] for b in kept)
    x1, y1 = max(b[2] for b in kept), max(b[3] for b in kept)

    box_area = (x1 - x0) * (y1 - y0) / (width * height)
    if not MIN_OBJECT_AREA <= box_area <= MAX_OBJECT_AREA:
        return None

    margin_x, margin_y = round((x1 - x0) * MARGIN_RATIO), round((y1 - y0) * MARGIN_RATIO)
    x0, y0 = max(0, x0 - margin_x), max(0, y0 - margin_y)
    x1, y1 = min(width, x1 + margin_x), min(height, y1 + margin_y)
    scale_x, scale_y = img.width / width, img.height / height
    return (
        int(x0 * scale_x), int(y0 * scale_y),
        min(img.width, round(x1 * scale_x)), min(img.height, round(y1 * scale_y)),
    )


def autocrop(img: Image.Image) -> Image.Image:
    """`img` recadrée sur l'objet, ou inchangée si le recadrage n'est pas sûr."""
    box = find_object_box(img)
    return img.crop(box) if box else img
