"""Rebuild the code-drawn SC gauge icon; requires Pillow only."""
from pathlib import Path
from PIL import Image, ImageDraw

ROOT=Path(__file__).resolve().parents[1]

def build():
    q=4
    im=Image.new('RGBA',(256*q,256*q))
    d=ImageDraw.Draw(im)
    def box(values): return tuple(round(v*q) for v in values)
    d.rounded_rectangle(box((8,8,248,248)),radius=48*q,fill='#071b2b',outline='#3fa7ce',width=3*q)
    d.rounded_rectangle(box((14,14,242,242)),radius=43*q,outline='#19465e',width=2*q)
    d.arc(box((34,34,222,222)),0,360,fill='#1b4c65',width=12*q)
    d.arc(box((34,34,222,222)),-90,160,fill='#27dfef',width=12*q)
    d.arc(box((29,29,227,227)),-90,160,fill='#7ff5ff',width=q)
    d.line(box((128,23,128,39)),fill='#e3fbff',width=5*q)
    # Angular SC monogram uses geometry instead of a machine-specific font.
    for points in [((117,99),(81,99),(73,107),(73,120),(81,128),(106,128),(114,136),(114,149),(106,157),(70,157)),
                   ((185,99),(153,99),(143,110),(143,146),(153,157),(185,157))]:
        d.line([(x*q,y*q) for x,y in points],fill='#e8faff',width=10*q,joint='curve')
    im=im.resize((256,256),Image.Resampling.LANCZOS)
    im.save(ROOT/'sc_capacitor_icon.png')
    im.save(ROOT/'sc_capacitor.ico',sizes=[(16,16),(20,20),(24,24),(32,32),(40,40),(48,48),(64,64),(128,128),(256,256)])

if __name__=='__main__': build()
