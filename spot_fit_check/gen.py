#!/usr/bin/env python3
"""Build check.html — visual verification of the MAP spot-position fit.

Per day: stacked data, data minus fitted background (the isolated dip), the fitted
blob, and the residual. Plus the estimator trajectories, so the disagreement between
the drift prediction, the MAP fit and the refiner is legible rather than asserted.
"""
import json

HERE = "/Users/simon/Desktop/MBP/spot_fit_check"
S = json.load(open(f"{HERE}/summary.json"))
DAYS = S["days"]

# validated categorical slots (light / dark) -- see validate_palette run
SER = [("Drift prediction", "#2a78d6", "#3987e5", "square"),
       ("MAP fit",          "#eb6834", "#d95926", "circle"),
       ("Refiner (7-day)",  "#1baf7a", "#199e70", "cross")]

BAD = {"20260723"}          # cloud-dominated stack, position not trustworthy


def chart(key, title, unit):
    """One axis, one measure. Three series, each direct-labelled."""
    W, H = 620, 230
    ml, mr, mt, mb = 46, 108, 14, 34
    pw, ph = W - ml - mr, H - mt - mb
    vals = [d[k][0 if key == "x" else 1] for d in DAYS
            for k in ("prior", "map") if d.get(k)]
    vals += [d["refiner"][0 if key == "x" else 1] for d in DAYS if d.get("refiner")]
    lo, hi = min(vals), max(vals)
    pad = (hi - lo) * 0.12 or 1
    lo, hi = lo - pad, hi + pad
    sx = lambda i: ml + (pw * i / max(len(DAYS) - 1, 1))
    sy = lambda v: mt + ph - ph * (v - lo) / (hi - lo)

    g = [f'<svg viewBox="0 0 {W} {H}" width="100%" role="img" aria-label="{title}">']
    for f in range(5):
        v = lo + (hi - lo) * f / 4
        y = sy(v)
        g.append(f'<line x1="{ml}" y1="{y:.1f}" x2="{ml+pw}" y2="{y:.1f}" class="grid"/>')
        g.append(f'<text x="{ml-8}" y="{y+4:.1f}" class="tick tr">{v:.0f}</text>')
    for i, d in enumerate(DAYS):
        g.append(f'<text x="{sx(i):.1f}" y="{H-12}" class="tick tc">{d["day"][4:6]}-{d["day"][6:]}</text>')

    for name, cl, cd, shape in SER:
        k = {"Drift prediction": "prior", "MAP fit": "map", "Refiner (7-day)": "refiner"}[name]
        pts = [(sx(i), sy(d[k][0 if key == "x" else 1]))
               for i, d in enumerate(DAYS) if d.get(k)]
        if not pts:
            continue
        idx = SER.index((name, cl, cd, shape)) + 1
        path = " ".join(f"{'M' if j == 0 else 'L'}{x:.1f},{y:.1f}" for j, (x, y) in enumerate(pts))
        g.append(f'<path d="{path}" class="ln s{idx}"/>')
        for x, y in pts:
            if shape == "square":
                g.append(f'<rect x="{x-4:.1f}" y="{y-4:.1f}" width="8" height="8" class="mk s{idx}"/>')
            elif shape == "circle":
                g.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4.5" class="mk s{idx}"/>')
            else:
                g.append(f'<path d="M{x-4:.1f},{y-4:.1f}L{x+4:.1f},{y+4:.1f}M{x-4:.1f},{y+4:.1f}'
                         f'L{x+4:.1f},{y-4:.1f}" class="mk xk s{idx}"/>')
        lx, ly = pts[-1]
        g.append(f'<text x="{lx+10:.1f}" y="{ly+4:.1f}" class="dlab s{idx}">{name}</text>')
    g.append(f'<text x="{ml}" y="{mt-2}" class="ylab">{unit}</text>')
    g.append("</svg>")
    return f'<figure class="chart"><figcaption>{title}</figcaption>{"".join(g)}</figure>'


rows = []
for d in DAYS:
    day = d["day"]
    pretty = f"{day[:4]}-{day[4:6]}-{day[6:]}"
    ref = f'{d["refiner"][0]}, {d["refiner"][1]}' if d.get("refiner") else "&mdash;"
    flag = ('<span class="flag">cloud-dominated stack &mdash; position not trustworthy</span>'
            if day in BAD else "")
    panels = "".join(
        f'<figure class="pan"><img src="{day}_{k}.png" alt="{lbl} for {pretty}" loading="lazy">'
        f'<figcaption>{lbl}</figcaption></figure>'
        for k, lbl in (("data", "stacked data"), ("dip", "data &minus; background"),
                       ("model", "fitted blob"), ("resid", "residual")))
    rows.append(f"""<section class="day">
  <div class="dayhead">
    <h3>{pretty}</h3>{flag}
    <dl>
      <div><dt>depth A</dt><dd>{d['A_pct']:.2f}%</dd></div>
      <div><dt>observed dip</dt><dd>{d['dip_min_pct']:.2f}%</dd></div>
      <div><dt>residual rms</dt><dd>{d['resid_rms_pct']:.3f}%</dd></div>
      <div><dt>drift pred.</dt><dd>{d['prior'][0]}, {d['prior'][1]}</dd></div>
      <div><dt>MAP</dt><dd>{d['map'][0]:.0f}, {d['map'][1]:.0f}</dd></div>
      <div><dt>refiner</dt><dd>{ref}</dd></div>
    </dl>
  </div>
  <div class="panels">{panels}</div>
</section>""")

table = "".join(
    f"<tr><th scope='row'>{d['day'][4:6]}-{d['day'][6:]}</th>"
    f"<td>{d['prior'][0]}</td><td>{d['map'][0]:.0f}</td>"
    f"<td>{d['refiner'][0] if d.get('refiner') else '—'}</td>"
    f"<td>{d['prior'][1]}</td><td>{d['map'][1]:.0f}</td>"
    f"<td>{d['refiner'][1] if d.get('refiner') else '—'}</td>"
    f"<td>{d['A_pct']:.2f}</td><td>{d['resid_rms_pct']:.3f}</td></tr>" for d in DAYS)

HTML = f"""<title>Spot position fit — visual check</title>
<style>
  :root {{
    color-scheme: light;
    --bg:#faf8f5; --card:#fff; --ink:#141312; --dim:#5d5954; --line:#e4ddd2;
    --s1:#2a78d6; --s2:#eb6834; --s3:#1baf7a; --warn:#b45309;
  }}
  @media (prefers-color-scheme: dark) {{
    :root:not([data-theme="light"]) {{
      color-scheme: dark;
      --bg:#111010; --card:#1b1a19; --ink:#f2ede6; --dim:#a09a91; --line:#302c27;
      --s1:#3987e5; --s2:#d95926; --s3:#199e70; --warn:#fab219;
    }}
  }}
  :root[data-theme="dark"] {{
    color-scheme: dark;
    --bg:#111010; --card:#1b1a19; --ink:#f2ede6; --dim:#a09a91; --line:#302c27;
    --s1:#3987e5; --s2:#d95926; --s3:#199e70; --warn:#fab219;
  }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; background:var(--bg); color:var(--ink);
    font:15px/1.55 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }}
  .wrap {{ max-width:1180px; margin:0 auto; padding:30px 22px 70px; }}
  h1 {{ font-size:25px; margin:0 0 6px; letter-spacing:-.01em; }}
  .lede {{ color:var(--dim); max-width:76ch; }}
  .verdict {{ margin:20px 0 26px; padding:14px 16px; background:var(--card);
    border:1px solid var(--line); border-left:3px solid var(--s2); border-radius:6px; }}
  .verdict p {{ margin:0 0 8px; }} .verdict p:last-child {{ margin:0; }}
  h2 {{ font-size:17px; margin:34px 0 12px; padding-bottom:7px; border-bottom:1px solid var(--line); }}

  .legend {{ display:flex; flex-wrap:wrap; gap:16px; margin:0 0 18px; font-size:13px;
    color:var(--dim); align-items:center; }}
  .legend .k {{ display:inline-flex; align-items:center; gap:7px; }}
  .glyph {{ width:16px; height:16px; flex:0 0 16px; }}

  .day {{ background:var(--card); border:1px solid var(--line); border-radius:8px;
    padding:14px 16px; margin-bottom:16px; }}
  .dayhead {{ display:flex; flex-wrap:wrap; gap:10px 18px; align-items:baseline; }}
  .dayhead h3 {{ margin:0; font-size:16px; font-variant-numeric:tabular-nums; }}
  .dayhead dl {{ display:flex; flex-wrap:wrap; gap:4px 16px; margin:0; font-size:12.5px; }}
  .dayhead dl div {{ display:flex; gap:5px; }}
  .dayhead dt {{ color:var(--dim); }}
  .dayhead dd {{ margin:0; font-variant-numeric:tabular-nums; }}
  .flag {{ font-size:12px; color:var(--warn); }}
  .panels {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(190px,1fr));
    gap:10px; margin-top:12px; }}
  .pan {{ margin:0; }}
  /* panels are baked with a light neutral midpoint, so they keep a light backing
     in both themes -- flipping it would misrepresent "zero" */
  .pan img {{ display:block; width:100%; height:auto; border-radius:4px;
    background:#f0efec; border:1px solid var(--line); }}
  .pan figcaption {{ font-size:11.5px; color:var(--dim); margin-top:4px; text-align:center; }}

  .charts {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(330px,1fr)); gap:16px; }}
  .chart {{ margin:0; background:var(--card); border:1px solid var(--line);
    border-radius:8px; padding:12px 14px 4px; overflow-x:auto; }}
  .chart figcaption {{ font-size:13.5px; margin-bottom:6px; }}
  .grid {{ stroke:var(--line); stroke-width:1; }}
  .tick {{ font-size:10.5px; fill:var(--dim); }}
  .tr {{ text-anchor:end; }} .tc {{ text-anchor:middle; }}
  .ylab {{ font-size:10.5px; fill:var(--dim); }}
  .ln {{ fill:none; stroke-width:2; }}
  .mk {{ stroke:var(--card); stroke-width:1.5; }}
  .xk {{ fill:none; stroke-width:2.5; }}
  .dlab {{ font-size:11.5px; }}
  .s1 {{ stroke:var(--s1); }} .mk.s1 {{ fill:var(--s1); }} text.s1 {{ fill:var(--s1); stroke:none; }}
  .s2 {{ stroke:var(--s2); }} .mk.s2 {{ fill:var(--s2); }} text.s2 {{ fill:var(--s2); stroke:none; }}
  .s3 {{ stroke:var(--s3); }} .mk.s3 {{ fill:none; }} text.s3 {{ fill:var(--s3); stroke:none; }}
  .xk.s3 {{ stroke:var(--s3); }}

  table {{ border-collapse:collapse; width:100%; font-size:13px; margin-top:6px;
    font-variant-numeric:tabular-nums; }}
  caption {{ text-align:left; font-size:13px; color:var(--dim); padding-bottom:6px; }}
  th, td {{ padding:5px 9px; border-bottom:1px solid var(--line); text-align:right; }}
  thead th {{ color:var(--dim); font-weight:500; }}
  tbody th {{ text-align:left; font-weight:500; }}
  .grp {{ border-left:1px solid var(--line); }}
</style>
<div class="wrap">
<h1>Spot position fit &mdash; visual check</h1>
<p class="lede">MAP fit of the window blemish, one centre per day tied across days by a
drift-informed random walk, shared width, depth free per day. Shape came out
<strong>&sigma; = {S['sg']:.2f} px &rarr; r<sub>half</sub> = {S['r_half']:.1f} px</strong>,
against the refiner's independently measured r = 38.2 px.</p>

<div class="verdict">
  <p><strong>It is working &mdash; and it overturns what I told you earlier.</strong>
  I said the MAP position disagreed with the refiner by 57&nbsp;px and so shouldn't be
  trusted. The panels show the opposite: the MAP circle sits on the dip, while the
  refiner cross and the drift prediction sit clearly <em>off</em> it.</p>
  <p>On 2026-07-20, with drift &asymp; 0, the MAP circle and the drift prediction
  coincide exactly on the blob centre &mdash; the method validates at baseline. On later
  days they separate, and the dip is where MAP says it is.</p>
  <p>Two consequences. The refiner lags by construction: it averages a 7-day window, so
  under monotonic drift it reports a stale position. And the blemish really does move
  faster than the scene &mdash; consistent with parallax, since it sits centimetres from
  the lens while the buildings are at infinity. That means <strong>tracking the aperture
  off <code>drift.csv</code> would still under-correct</strong>; it has to track the
  blemish itself.</p>
</div>

<h2>Estimator tracks</h2>
<div class="legend">
  <span class="k"><svg class="glyph" viewBox="0 0 16 16"><rect x="4" y="4" width="8" height="8" fill="var(--s1)"/></svg> Drift prediction</span>
  <span class="k"><svg class="glyph" viewBox="0 0 16 16"><circle cx="8" cy="8" r="4.5" fill="var(--s2)"/></svg> MAP fit</span>
  <span class="k"><svg class="glyph" viewBox="0 0 16 16"><path d="M4,4L12,12M4,12L12,4" stroke="var(--s3)" stroke-width="2.5" fill="none"/></svg> Refiner (7-day window)</span>
</div>
<div class="charts">
  {chart('x', 'Spot centre — x', 'px')}
  {chart('y', 'Spot centre — y', 'px')}
</div>
<table>
  <caption>Same values as the charts, for reading exactly.</caption>
  <thead><tr><th scope="col">day</th>
    <th scope="col">x drift</th><th scope="col">x MAP</th><th scope="col">x refiner</th>
    <th scope="col" class="grp">y drift</th><th scope="col">y MAP</th><th scope="col">y refiner</th>
    <th scope="col" class="grp">A %</th><th scope="col">resid rms %</th></tr></thead>
  <tbody>{table}</tbody>
</table>

<h2>Per-day panels</h2>
<div class="legend">
  <span class="k"><svg class="glyph" viewBox="0 0 16 16"><rect x="3" y="3" width="10" height="10" fill="none" stroke="currentColor" stroke-width="1.5"/></svg> drift prediction</span>
  <span class="k"><svg class="glyph" viewBox="0 0 16 16"><circle cx="8" cy="8" r="5.5" fill="none" stroke="currentColor" stroke-width="1.5"/></svg> MAP centre</span>
  <span class="k"><svg class="glyph" viewBox="0 0 16 16"><path d="M3,3L13,13M3,13L13,3" stroke="currentColor" stroke-width="1.8" fill="none"/></svg> refiner centre</span>
  <span>markers are shape-coded, not colour-coded</span>
</div>
<p class="lede" style="margin-bottom:16px">Dip, blob and residual panels share one fixed
diverging scale, &plusmn;{S['range_pct']:.0f}% around zero &mdash; blue below the fitted
background, red above, neutral grey at zero. The data panel is a single-hue magnitude ramp
over its own 1st&ndash;99th percentile. Each panel is 300&nbsp;px of the 4K frame; the top
is clipped on later days because the blemish is only ~150&nbsp;px below the frame edge.</p>
{"".join(rows)}
</div>
"""

open(f"{HERE}/check.html", "w").write(HTML)
print("wrote check.html")
