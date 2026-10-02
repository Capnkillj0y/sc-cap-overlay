"""Convert the selected Flight artwork into the bundled PNG and Windows ICO."""
from pathlib import Path
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SIZES = [(n,n) for n in (16,20,24,32,40,48,64,128,256)]


def build():
    with Image.open(ROOT/'assets'/'flight-icon-master.png') as source:
        icon = source.convert('RGBA').resize((256,256),Image.Resampling.LANCZOS)
    icon.save(ROOT/'sc_capacitor_icon.png')
    icon.save(ROOT/'sc_capacitor.ico',sizes=SIZES)


if __name__ == '__main__':
    build()
