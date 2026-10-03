"""Tactical Clean alert lettering, rendered consistently without font installation."""
from functools import lru_cache
from pathlib import Path
import sys
from PIL import Image, ImageDraw, ImageFont, ImageColor, ImageFilter

LOW_COLOR = '#ffe83d'
EMPTY_COLOR = '#f77b16'
FLARE_COLOR = '#ff3038'


def migrate_alert_colors(cfg):
    """Upgrade legacy defaults once, preserving user-selected colors."""
    if cfg.get('alert_style_version', 0) >= 1:
        return False
    for key, old, new in [('alert_color_low', '#ffb238', LOW_COLOR),
                          ('alert_color_empty', '#ff4438', EMPTY_COLOR)]:
        if key not in cfg or str(cfg[key]).lower() == old:
            cfg[key] = new
    cfg['alert_style_version'] = 1
    return True


@lru_cache(maxsize=12)
def _font(px):
    base = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent))
    font = ImageFont.truetype(str(base / 'assets' / 'Orbitron.ttf'), px)
    font.set_variation_by_axes([800])
    return font


@lru_cache(maxsize=96)
def render_alert(text, color, px):
    """Return a transparent text bitmap with a subtle highlight and dark edge."""
    scale = 3
    font = _font(px * scale)
    box = font.getbbox(text)
    pad = 5 * scale
    size = (box[2]-box[0]+pad*2, box[3]-box[1]+pad*2)
    pos = (pad-box[0], pad-box[1])
    mask = Image.new('L', size)
    ImageDraw.Draw(mask).text(pos, text, font=font, fill=255)
    edge = mask.filter(ImageFilter.MaxFilter(7))
    out = Image.new('RGBA', size, (4, 7, 10, 0))
    out.putalpha(edge)
    rgb = ImageColor.getrgb(color)
    gradient = Image.new('RGBA', size)
    draw = ImageDraw.Draw(gradient)
    for y in range(size[1]):
        t = max(0, min(1, (y-pad)/max(1, size[1]-2*pad)))
        highlight = 0.16 * (1-t)
        shade = 1 - 0.10*t
        fill = tuple(round((v + (255-v)*highlight)*shade) for v in rgb)
        draw.line((0, y, size[0], y), fill=fill+(255,))
    out.paste(gradient, (0, 0), mask)
    return out.resize((round(size[0]/scale), round(size[1]/scale)), Image.Resampling.LANCZOS)
