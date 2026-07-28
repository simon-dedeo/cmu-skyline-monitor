#!/usr/bin/env python3
"""Build morning.html / evening.html — golden-hour frames binned by sun elevation.

One row per 1-degree elevation bin, rows in chronological order (elevation rising
for the morning page, falling for the evening page). Within a row every frame we
have at that sun position, grouped by date. Click a frame for the 1400px version.
"""
import json
import collections
from html import escape

HERE = "/Users/simon/Desktop/MBP/golden_hours"
BIN = 1.0  # degrees of elevation per row

rows = json.load(open(f"{HERE}/manifest.json"))


def bin_lo(el):
    import math
    return math.floor(el / BIN) * BIN


def build(half):
    frames = [r for r in rows if r["half"] == half]
    bins = collections.defaultdict(list)
    for r in frames:
        bins[bin_lo(r["el"])].append(r)
    # chronological: morning = sun rising, evening = sun falling
    keys = sorted(bins, reverse=(half == "evening"))
    dates = sorted({r["date"] for r in frames})

    out = []
    for k in keys:
        group = sorted(bins[k], key=lambda r: (r["date"], r["time"]))
        by_date = collections.defaultdict(list)
        for r in group:
            by_date[r["date"]].append(r)

        cells = []
        for d in sorted(by_date):
            shots = []
            for r in by_date[d]:
                prov = "corr" if r.get("reproc") else ("legacy" if r["kind"] == "gold" else "orig")
                shots.append(
                    f'<figure class="shot{" peak" if r["kind"] == "gold" else ""} p-{prov}" '
                    f'data-med="med/{escape(r["file"])}" '
                    f'data-caption="{escape(d)} &middot; {escape(r["time"])} &middot; '
                    f'{r["el"]:+.2f}&deg; &middot; '
                    f'{"spot-corrected (reprocessed)" if prov == "corr" else ("1-min golden frame, no raw brackets kept &mdash; era correction as served" if prov == "legacy" else "original as served")}">'
                    f'<img src="thumb/{escape(r["file"])}" loading="lazy" '
                    f'alt="{escape(d)} {escape(r["time"])}">'
                    f'<figcaption>{escape(r["time"])}{"&thinsp;&#10003;" if prov == "corr" else ""}</figcaption></figure>'
                )
            cells.append(
                f'<div class="daygroup"><div class="daylabel">'
                f'{escape(d[5:])}<span class="n">{len(by_date[d])}</span></div>'
                f'<div class="shots">{"".join(shots)}</div></div>'
            )

        lo, hi = k, k + BIN
        out.append(
            f'<section class="band">'
            f'<div class="bandhead">'
            f'<div class="elev">{lo:+.0f}&deg; &ndash; {hi:+.0f}&deg;</div>'
            f'<div class="sundial">{sundial(lo + BIN / 2)}</div>'
            f'<div class="count">{len(group)} frames &middot; {len(by_date)} days</div>'
            f'</div>'
            f'<div class="strip">{"".join(cells)}</div>'
            f'</section>'
        )

    label = "Morning" if half == "morning" else "Evening"
    sub = ("sun rising — top of page is deepest twilight"
           if half == "morning" else
           "sun falling — top of page is highest light")
    times = sorted({r["time"] for r in frames})
    meta = (f"{len(frames)} frames &middot; {len(dates)} days "
            f"({dates[0]} &rarr; {dates[-1]}) &middot; "
            f"{times[0]}&ndash;{times[-1]} local")

    other = "evening" if half == "morning" else "morning"
    return PAGE.format(half=half, label=label, sub=sub, meta=meta,
                       other=other, other_label=other.capitalize(),
                       bands="\n".join(out))


def sundial(el):
    """Tiny inline SVG: horizon line with the sun at this elevation."""
    # map -8..+12 deg onto the 44px-tall box
    y = 30 - (el / 12.0) * 24
    lit = "#f0b849" if el > 0 else "#7d6ba8"
    return (
        f'<svg viewBox="0 0 56 44" width="56" height="44" aria-hidden="true">'
        f'<line x1="4" y1="30" x2="52" y2="30" stroke="currentColor" '
        f'stroke-opacity=".35" stroke-width="1"/>'
        f'<circle cx="28" cy="{y:.1f}" r="5" fill="{lit}"/>'
        f'</svg>'
    )


PAGE = """<title>Golden hours — {label} · CMU skyline</title>
<style>
  :root {{
    --bg:#faf7f2; --panel:#fff; --ink:#1b1a18; --dim:#6d6862;
    --line:#e2dbd0; --accent:#b7791f;
  }}
  @media (prefers-color-scheme: dark) {{
    :root {{ --bg:#12100e; --panel:#1b1815; --ink:#efe9e0; --dim:#9b938a;
             --line:#302a24; --accent:#f0b849; }}
  }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; background:var(--bg); color:var(--ink);
    font:15px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }}
  header {{ padding:28px 24px 18px; border-bottom:1px solid var(--line);
    position:sticky; top:0; background:var(--bg); z-index:5; }}
  h1 {{ margin:0 0 4px; font-size:26px; letter-spacing:-.01em; font-weight:600; }}
  h1 em {{ color:var(--accent); font-style:normal; }}
  .sub {{ color:var(--dim); font-size:14px; }}
  .meta {{ color:var(--dim); font-size:12.5px; margin-top:6px;
    font-variant-numeric:tabular-nums; }}
  nav {{ margin-top:12px; display:flex; gap:8px; align-items:center; }}
  nav a, nav button {{ font:inherit; font-size:13px; padding:5px 11px;
    border:1px solid var(--line); border-radius:999px; background:var(--panel);
    color:var(--ink); text-decoration:none; cursor:pointer; }}
  nav a:hover, nav button:hover {{ border-color:var(--accent); color:var(--accent); }}
  nav button[aria-pressed="true"] {{ border-color:var(--accent); color:var(--accent); }}

  .band {{ border-bottom:1px solid var(--line); display:flex; align-items:stretch; }}
  .bandhead {{ flex:0 0 132px; padding:14px 12px; border-right:1px solid var(--line);
    display:flex; flex-direction:column; gap:2px; position:sticky; left:0;
    background:var(--bg); z-index:2; }}
  .elev {{ font-size:17px; font-weight:600; font-variant-numeric:tabular-nums;
    letter-spacing:-.02em; }}
  .sundial {{ color:var(--ink); opacity:.9; margin:2px 0; }}
  .count {{ font-size:11.5px; color:var(--dim); }}

  .strip {{ flex:1 1 auto; overflow-x:auto; display:flex; gap:22px;
    padding:14px 18px; scrollbar-width:thin; }}
  .daygroup {{ flex:0 0 auto; }}
  .daylabel {{ font-size:11.5px; color:var(--dim); margin-bottom:5px;
    font-variant-numeric:tabular-nums; display:flex; gap:6px; align-items:center; }}
  .daylabel .n {{ font-size:10px; padding:0 5px; border-radius:999px;
    background:var(--line); color:var(--dim); }}
  .shots {{ display:flex; gap:5px; }}
  .shot {{ margin:0; width:150px; cursor:zoom-in; position:relative; }}
  .shot img {{ display:block; width:150px; height:84px; object-fit:cover;
    border-radius:3px; background:var(--line); }}
  .shot figcaption {{ font-size:10.5px; color:var(--dim); text-align:center;
    margin-top:3px; font-variant-numeric:tabular-nums; }}
  .shot.peak img {{ box-shadow:0 0 0 1.5px var(--accent); }}
  .shot.p-corr figcaption {{ color:#2e7d32; }}
  .shot.p-legacy img {{ opacity:.92; }}
  .shot:hover img {{ filter:brightness(1.06); }}
  body.big .shot, body.big .shot img {{ width:300px; }}
  body.big .shot img {{ height:169px; }}

  #lb {{ position:fixed; inset:0; background:rgba(8,6,4,.94); display:none;
    align-items:center; justify-content:center; flex-direction:column; z-index:50;
    cursor:zoom-out; }}
  #lb.on {{ display:flex; }}
  #lb img {{ max-width:94vw; max-height:84vh; border-radius:4px; }}
  #lbcap {{ color:#e8e0d4; font-size:13.5px; margin-top:12px;
    font-variant-numeric:tabular-nums; }}
  #lbhint {{ color:#8b8377; font-size:11.5px; margin-top:4px; }}
</style>

<header>
  <h1>Golden hours &mdash; <em>{label}</em></h1>
  <div class="sub">Each row is one degree of sun elevation; {sub}.</div>
  <div class="meta">{meta} &middot; gold-ringed frames are 1-min golden-scan captures,
    the rest are the 5-min archive &middot; <span style="color:#2e7d32">&#10003;</span> =
    window-spot removed by reprocessing the raw brackets (2026-07-28 backfill); 1-min gold
    frames kept no raw brackets and carry whatever correction they were served with</div>
  <nav>
    <a href="{other}.html">&rarr; {other_label} page</a>
    <button id="zoom" aria-pressed="false">Bigger thumbnails</button>
  </nav>
</header>

{bands}

<div id="lb"><img id="lbimg" alt=""><div id="lbcap"></div>
  <div id="lbhint">&larr; &rarr; to step through &middot; esc / click to close</div></div>

<script>
  var shots = Array.prototype.slice.call(document.querySelectorAll('.shot'));
  var lb = document.getElementById('lb'), lbimg = document.getElementById('lbimg'),
      lbcap = document.getElementById('lbcap'), cur = -1;

  function show(i) {{
    if (i < 0 || i >= shots.length) return;
    cur = i;
    lbimg.src = shots[i].dataset.med;
    lbcap.innerHTML = shots[i].dataset.caption;
    lb.classList.add('on');
  }}
  shots.forEach(function (s, i) {{ s.addEventListener('click', function () {{ show(i); }}); }});
  lb.addEventListener('click', function () {{ lb.classList.remove('on'); }});
  document.addEventListener('keydown', function (e) {{
    if (!lb.classList.contains('on')) return;
    if (e.key === 'Escape') lb.classList.remove('on');
    if (e.key === 'ArrowRight') show(cur + 1);
    if (e.key === 'ArrowLeft') show(cur - 1);
  }});

  var zb = document.getElementById('zoom');
  zb.addEventListener('click', function () {{
    var on = document.body.classList.toggle('big');
    zb.setAttribute('aria-pressed', on ? 'true' : 'false');
  }});
</script>
"""

for half in ("morning", "evening"):
    with open(f"{HERE}/{half}.html", "w") as f:
        f.write(build(half))
    print(f"wrote {half}.html")
