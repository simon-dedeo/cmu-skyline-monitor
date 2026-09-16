#!/usr/bin/env python3
"""Burn a UTC timestamp into each staged 1080p frame.

frames/<YYYY-MM-DD>_<HHMM>.jpg -> labeled/<same name>, with the date and time
drawn bottom-left over a translucent plate so it stays legible against both
bright sky and night frames. Resumable; frames already labeled are skipped.
"""
import os, sys, glob, re
from concurrent.futures import ProcessPoolExecutor
from PIL import Image, ImageDraw, ImageFont

# Paths are explicit (they used to be assumed relative to this script, which broke once the
# script was archived out of the staging directory): tl_label.py [SRC_DIR] [DST_DIR]
SP  = os.path.dirname(os.path.abspath(__file__))
SRC = sys.argv[1] if len(sys.argv) > 1 else os.path.join(SP, "frames")
DST = sys.argv[2] if len(sys.argv) > 2 else os.path.join(SP, "labeled")
NAME = re.compile(r"^(\d{4}-\d{2}-\d{2})_(\d{2})(\d{2})\.jpg$")

def load_font(size):
    for p in ("/System/Library/Fonts/SFNSMono.ttf",
              "/System/Library/Fonts/Menlo.ttc",
              "/System/Library/Fonts/Supplemental/Courier New Bold.ttf"):
        try:
            return ImageFont.truetype(p, size)
        except Exception:
            continue
    return ImageFont.load_default()

def one(name):
    m = NAME.match(name)
    if not m:
        return name
    day, hh, mm = m.groups()
    src, dst = os.path.join(SRC, name), os.path.join(DST, name)
    try:
        im = Image.open(src).convert("RGB")
    except Exception:
        return name
    d = ImageDraw.Draw(im, "RGBA")
    font = load_font(34)
    text = "%s   %s:%s UTC" % (day, hh, mm)
    x, y = 28, im.height - 66
    box = d.textbbox((x, y), text, font=font)
    d.rectangle((box[0] - 14, box[1] - 10, box[2] + 14, box[3] + 10),
                fill=(0, 0, 0, 110))
    d.text((x, y), text, font=font, fill=(255, 255, 255, 235))
    tmp = dst + ".tmp.jpg"
    im.save(tmp, "JPEG", quality=92)
    os.replace(tmp, dst)
    return None

if __name__ == "__main__":
    if not os.path.isdir(SRC):
        sys.exit("tl_label: source dir not found: %s\n"
                 "usage: tl_label.py [SRC_DIR] [DST_DIR]  (SRC holds YYYY-MM-DD_HHMM.jpg)" % SRC)
    os.makedirs(DST, exist_ok=True)
    have = set(os.listdir(DST))
    todo = [n for n in sorted(os.listdir(SRC))
            if n.endswith(".jpg") and n not in have]
    print("todo %d" % len(todo), flush=True)
    bad, done = [], 0
    with ProcessPoolExecutor() as ex:
        for r in ex.map(one, todo, chunksize=16):
            done += 1
            if r: bad.append(r)
            if done % 500 == 0: print("%d/%d" % (done, len(todo)), flush=True)
    print("done %d, failed %d" % (done, len(bad)), flush=True)
    for b in bad[:20]: print("BAD %s" % b, flush=True)
