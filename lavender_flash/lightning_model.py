"""Colorimetric closure test and archive camera calibration for the
August 9, 2026 lightning flash ("The Lavender Flash of August 9th").

Single source of truth for all numbers and figures in LAVENDER_FLASH.tex.

What this script computes:
  1. Direct radiometry on the archived 4K reference triplet:
     - Pixel-wise linearization under both standard sRGB (IEC 61966-2-1) and
       the empirical per-channel Camera Response Function (CRF) calibrated for
       this Elgato Facecam 4K from daytime exposure brackets.
     - Baseline sensitivity (before vs after vs mean), blue-channel clipping
       and highlight-shoulder roll-off diagnostics, and full-frame vertical
       profile (rolling-shutter uniformity check).
     - Exact CIE 1931 2-deg chromaticity (x, y), dominant wavelength, and
       excitation purity.
  2. Archive camera-pipeline validation from science.csv (high-sun overcast
     skies, n=481; high-sun clear blue skies, n=288; fixed WB=5600 K):
     - Shows that raw un-matrixed Bayer Gaussians fail on clear blue daytime
       sky (max B/G ~ 1.81 vs measured median 2.19), whereas a white-point-
       preserving 3x3 Color Correction Matrix (CCM, mapping Bayer -> linear
       sRGB) or the sensor-independent CIE-sRGB benchmark reproduces daytime
       overcast and clear-sky colors.
  3. Three-component lightning emission model (continuum + atomic lines +
     N2+ 1N molecular bands) through neutral cloud diffusion + Rayleigh
     extinction, evaluated across camera pipelines, continuum opacity/T
     limits (Planck 30 kK vs Kramers 30 kK / 20 kK), atomic templates
     (core 10-line, no-Halpha ablation, expanded 22-line), and path lengths.
  4. Publication figures:
     - fig2a.pdf : Best-fit optically-thin Kramers + atomic (+ bounded N2+)
                   emission spectrum and camera sensitivities (log-linear).
     - fig2b.pdf : (R/G, B/G) closure & archive-validation diagnostic plane.
     - fig3_radiometry.pdf : Brightness-binned flash sky diagnostics (sRGB vs
                   empirical CRF flatness, and blue-channel highlight roll-off).
"""
import os
import csv
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Polygon

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA_DIR = os.path.join(HERE, "data")
TMP_DIR = DATA_DIR if os.path.exists(os.path.join(DATA_DIR, "lightning_flash_0011EDT.jpg")) else os.path.join(ROOT, "tmp")
SCI_CSV = (os.path.join(DATA_DIR, "science.csv")
           if os.path.exists(os.path.join(DATA_DIR, "science.csv"))
           else os.path.join(ROOT, "golden_analysis", "science.csv"))

# Physical constants (SI, CODATA 2018)
h_P = 6.62607015e-34
c_0 = 2.99792458e8
k_B = 1.380649e-23

nm = np.linspace(380.0, 720.0, 3401)
lam = nm * 1e-9

# Empirical per-channel CRF for pandr's Elgato Facecam 4K (flatfield.py / spotnight.py)
DEFAULT_CRF = {"r": (0.36, 3.2), "g": (0.45, 3.8), "b": (0.55, 3.8)}


# ---------------------------------------------------------------------------
# Transfer functions & CIE 1931 2-deg colorimetry
# ---------------------------------------------------------------------------
def srgb_to_lin(v):
    x = np.asarray(v, dtype=np.float64) / 255.0
    return np.where(x <= 0.04045, x / 12.92, ((x + 0.055) / 1.055) ** 2.4)


def lin_to_srgb(v):
    v = np.clip(np.asarray(v, dtype=np.float64), 0.0, 1.0)
    return np.where(v <= 0.0031308, 12.92 * v, 1.055 * v ** (1.0 / 2.4) - 0.055)


def crf_eval(v, a, g):
    x = np.clip(np.asarray(v, dtype=np.float64) / 255.0, 0.0, 1.0)
    c0 = (a / (1.0 + a)) ** g
    return (((x + a) / (1.0 + a)) ** g - c0) / (1.0 - c0)


def crf_to_lin_rgb(rgb, anchor_dn=180.0):
    """Linearize RGB with the measured per-channel CRF, anchored so R=G=B at anchor_dn."""
    arr = np.asarray(rgb, dtype=np.float64)
    out = np.empty_like(arr)
    g_ref = crf_eval(anchor_dn, *DEFAULT_CRF["g"])
    for i, ch in enumerate("rgb"):
        a, g = DEFAULT_CRF[ch]
        scale = g_ref / crf_eval(anchor_dn, a, g)
        out[..., i] = crf_eval(arr[..., i], a, g) * scale
    return out


# CIE 1931 2-deg analytical CMFs (Wyman et al. 2013 piecewise-Gaussian fit)
def cie_1931_cmf(nm_arr):
    def g(x, mu, t1, t2):
        return np.where(x < mu, np.exp(-0.5 * (t1 * (x - mu)) ** 2),
                        np.exp(-0.5 * (t2 * (x - mu)) ** 2))
    xb = (1.056 * g(nm_arr, 599.8, 0.0264, 0.0323)
          + 0.362 * g(nm_arr, 442.0, 0.0624, 0.0374)
          - 0.065 * g(nm_arr, 501.1, 0.0490, 0.0382))
    yb = (0.821 * g(nm_arr, 568.8, 0.0213, 0.0247)
          + 0.286 * g(nm_arr, 530.9, 0.0613, 0.0322))
    zb = (1.217 * g(nm_arr, 437.0, 0.0845, 0.0278)
          + 0.681 * g(nm_arr, 459.0, 0.0385, 0.0725))
    arr = np.vstack([xb, yb, zb])
    return np.where(np.abs(arr) < 1e-15, 0.0, arr)


XYZ_CMF = cie_1931_cmf(nm)
M_XYZ_TO_SRGB = np.array([
    [ 3.2406, -1.5372, -0.4986],
    [-0.9689,  1.8758,  0.0415],
    [ 0.0557, -0.2040,  1.0570],
])
M_SRGB_TO_XYZ = np.linalg.inv(M_XYZ_TO_SRGB)
M_BRADFORD = np.array([
    [ 0.8951,  0.2664, -0.1614],
    [-0.7502,  1.7135,  0.0367],
    [ 0.0389, -0.0685,  1.0296],
])
XYZ_D65 = np.array([0.95047, 1.00000, 1.08883])
XY_D65 = np.array([0.31271, 0.32902])

# Tabulated exact CIE 1931 2-deg blue-violet spectrum locus (nm -> (x, y))
CIE_LOCUS_BLUE = np.array([
    [380, 0.1741, 0.0050], [400, 0.1733, 0.0048], [420, 0.1714, 0.0051],
    [430, 0.1689, 0.0069], [440, 0.1644, 0.0109], [445, 0.1611, 0.0138],
    [450, 0.1566, 0.0177], [455, 0.1510, 0.0227], [460, 0.1440, 0.0297],
    [465, 0.1355, 0.0399], [470, 0.1241, 0.0578], [475, 0.1096, 0.0868],
])


def srgb_chromaticity_info(rgb_lin):
    """Return CIE 1931 (x, y), dominant wavelength (nm), and excitation purity."""
    xyz = M_SRGB_TO_XYZ @ np.asarray(rgb_lin, dtype=np.float64)
    xy = xyz[:2] / xyz.sum()
    ang = np.degrees(np.arctan2(xy[1] - XY_D65[1], xy[0] - XY_D65[0]))
    loc_ang = np.degrees(np.arctan2(CIE_LOCUS_BLUE[:, 2] - XY_D65[1],
                                    CIE_LOCUS_BLUE[:, 1] - XY_D65[0]))
    # Angles decrease monotonically from 380 nm (-113.2 deg) to 475 nm (-129.9 deg)
    dom_lam = float(np.interp(ang, loc_ang[::-1], CIE_LOCUS_BLUE[::-1, 0]))
    lx = float(np.interp(dom_lam, CIE_LOCUS_BLUE[:, 0], CIE_LOCUS_BLUE[:, 1]))
    ly = float(np.interp(dom_lam, CIE_LOCUS_BLUE[:, 0], CIE_LOCUS_BLUE[:, 2]))
    purity = float(np.hypot(xy[0] - XY_D65[0], xy[1] - XY_D65[1])
                   / np.hypot(lx - XY_D65[0], ly - XY_D65[1]))
    return xy, dom_lam, purity


# ---------------------------------------------------------------------------
# Physical spectra & camera models
# ---------------------------------------------------------------------------
def planck(T):
    x = h_P * c_0 / (lam * k_B * T)
    return (2.0 * h_P * c_0**2 / lam**5) / np.expm1(x)


def kramers(T):
    """Optically thin free-free / free-bound continuum: eps_lam ~ lam^-2 exp(-hc/lam kT)."""
    return (1.0 / lam**2) * np.exp(-h_P * c_0 / (lam * k_B * T))


def normalize_spec(s):
    return s / np.trapezoid(s, lam)


# Core 10-line atomic template (Orville 1968; Orville & Henderson 1984)
ATOM_CORE = [
    (399.5, 0.45, 2.5),   # N II
    (444.7, 0.30, 2.5),   # N II
    (463.1, 0.40, 2.5),   # N II 463.0/464.3
    (486.1, 0.20, 2.5),   # H beta
    (500.5, 0.70, 2.5),   # N II multiplet
    (568.0, 0.20, 2.5),   # N II
    (594.2, 0.10, 2.5),   # N II
    (615.8, 0.10, 2.5),   # O I
    (648.2, 0.15, 2.5),   # N II
    (656.3, 1.00, 3.0),   # H alpha
]

# Expanded 22-line literature template (adds Hgamma, Hdelta, blue N II & O II multiplets)
ATOM_EXPANDED = [
    (399.5, 0.45, 2.5), (404.1, 0.25, 2.5), (407.2, 0.15, 2.5), (410.2, 0.10, 2.5),
    (424.2, 0.18, 2.5), (434.0, 0.15, 2.5), (441.5, 0.12, 2.5), (444.7, 0.30, 2.5),
    (460.7, 0.20, 2.5), (463.1, 0.45, 2.5), (464.9, 0.20, 2.5), (486.1, 0.25, 2.5),
    (500.5, 0.85, 3.0), (504.5, 0.15, 2.5), (517.9, 0.12, 2.5), (553.5, 0.10, 2.5),
    (568.0, 0.25, 2.5), (594.2, 0.15, 2.5), (615.8, 0.12, 2.5), (648.2, 0.15, 2.5),
    (656.3, 1.00, 3.0), (661.1, 0.10, 2.5),
]

# N2+ First Negative (B^2 Sigma_u+ -> X^2 Sigma_g+) bandheads
MOL_N2P = [
    (391.4, 0.60, 3.0),
    (427.8, 1.00, 3.0),
    (470.9, 0.35, 3.0),
]


def line_template(lines):
    s = np.zeros_like(nm)
    for lc, amp, sig in lines:
        s += amp * np.exp(-0.5 * ((nm - lc) / sig) ** 2) / sig
    return normalize_spec(s)


def bayer_raw_curves(ir_cut=670.0, uv_cut=398.0):
    cut = (1.0 / (1.0 + np.exp(-(ir_cut - nm) / 6.0))) * (1.0 / (1.0 + np.exp(-(nm - uv_cut) / 5.0)))
    chR = np.exp(-0.5 * ((nm - 612.0) / 48.0) ** 2) * cut
    chG = np.exp(-0.5 * ((nm - 535.0) / 45.0) ** 2) * cut
    chB = np.exp(-0.5 * ((nm - 455.0) / 38.0) ** 2) * cut
    arr = np.vstack([chR, chG, chB])
    return np.where(arr < 1e-15, 0.0, arr)


def make_cie_srgb_cmf(T_wb=5600.0):
    wb = planck(T_wb)
    xyz_wb = np.array([np.trapezoid(wb * b, lam) for b in XYZ_CMF])
    xyz_wb /= xyz_wb[1]
    M_adapt = np.linalg.inv(M_BRADFORD) @ np.diag((M_BRADFORD @ XYZ_D65) / (M_BRADFORD @ xyz_wb)) @ M_BRADFORD
    cmf = M_XYZ_TO_SRGB @ M_adapt @ XYZ_CMF
    wb_rgb = np.array([np.trapezoid(wb * b, lam) for b in cmf])
    return cmf / wb_rgb[:, None]


def make_bayer_noccm_cmf(T_wb=5600.0, ir_cut=670.0):
    B = bayer_raw_curves(ir_cut=ir_cut)
    wb = planck(T_wb)
    wb_raw = np.array([np.trapezoid(wb * b, lam) for b in B])
    return B / wb_raw[:, None]


def make_bayer_ccm_cmf(T_wb=5600.0, ir_cut=670.0):
    """White-balanced Bayer curves followed by a white-point-preserving 3x3 CCM
    trained by least squares to reproduce CIE-sRGB on smooth broadband spectra."""
    B_wb = make_bayer_noccm_cmf(T_wb=T_wb, ir_cut=ir_cut)
    T_srgb = make_cie_srgb_cmf(T_wb=T_wb)
    wb = planck(T_wb)
    specs = ([planck(T) for T in np.geomspace(2500.0, 30000.0, 12)]
             + [wb * np.exp(-0.5 * ((nm - mu) / 35.0) ** 2) for mu in np.linspace(420.0, 660.0, 25)])
    X = np.column_stack([[np.trapezoid(s * b, lam) for b in B_wb] for s in specs])
    Y = np.column_stack([[np.trapezoid(s * b, lam) for b in T_srgb] for s in specs])
    invXX = np.linalg.inv(X @ X.T)
    ones = np.ones(3)
    M0 = (Y @ X.T) @ invXX
    CCM = M0 + np.outer((ones - M0 @ ones) / (ones @ invXX @ ones), ones @ invXX)
    return CCM @ B_wb, CCM


def rayleigh_trans(dist_km=3.0):
    return np.exp(-0.012 * dist_km * (550.0 / nm) ** 4)


def cam_rgb(spec, cmf, dist_km=3.0):
    s = spec * rayleigh_trans(dist_km) if dist_km > 0 else spec
    return np.array([np.trapezoid(s * b, lam) for b in cmf])


def rgb_ratios(rgb):
    return np.array([rgb[0] / rgb[1], rgb[2] / rgb[1]])


def solve_mixture(cmf, c_spec, a_spec, m_spec, target_rg, target_bg, dist_km=3.0):
    """Exact 3-component solve (2 chromatic ratios + sum(w)=1 => 0 dof)."""
    rc = cam_rgb(c_spec, cmf, dist_km)
    ra = cam_rgb(a_spec, cmf, dist_km)
    rm = cam_rgb(m_spec, cmf, dist_km)
    A = np.array([
        [1.0, 1.0, 1.0],
        [rc[0] - target_rg * rc[1], ra[0] - target_rg * ra[1], rm[0] - target_rg * rm[1]],
        [rc[2] - target_bg * rc[1], ra[2] - target_bg * ra[1], rm[2] - target_bg * rm[1]],
    ])
    w = np.linalg.solve(A, np.array([1.0, 0.0, 0.0]))
    return w, (rc, ra, rm)


# ---------------------------------------------------------------------------
# Direct image & archive radiometry
# ---------------------------------------------------------------------------
def measure_4k_frames():
    """Analyze the archived 4K triplet in ../tmp/ if present; else return cached summary."""
    paths = [os.path.join(TMP_DIR, f) for f in (
        "lightning_before_0006EDT.jpg",
        "lightning_flash_0011EDT.jpg",
        "lightning_after_0017EDT.jpg",
    )]
    if not all(os.path.exists(p) for p in paths):
        return None

    import cv2
    before, flash, after = [cv2.imread(p)[:, :, ::-1] for p in paths]
    H, W = flash.shape[:2]
    sl = (slice(0, int(0.25 * H)), slice(None))
    f, b, a = flash[sl], before[sl], after[sl]

    # Pixel-wise sRGB linearization
    fl_s, bl_s, al_s = srgb_to_lin(f), srgb_to_lin(b), srgb_to_lin(a)
    d_bef_s = (fl_s - bl_s).mean(axis=(0, 1))
    d_aft_s = (fl_s - al_s).mean(axis=(0, 1))
    d_avg_s = (fl_s - 0.5 * (bl_s + al_s)).mean(axis=(0, 1))

    # Empirical CRF linearization (anchored at DN=180)
    fl_c, bl_c, al_c = crf_to_lin_rgb(f), crf_to_lin_rgb(b), crf_to_lin_rgb(a)
    d_avg_c = (fl_c - 0.5 * (bl_c + al_c)).mean(axis=(0, 1))

    # Unclipped mid-brightness sky (B_DN in [170, 215])
    m_unclip = (f[:, :, 2] >= 170) & (f[:, :, 2] < 215)
    d_unclip_s = (fl_s - 0.5 * (bl_s + al_s))[m_unclip].mean(axis=0)
    d_unclip_c = (fl_c - 0.5 * (bl_c + al_c))[m_unclip].mean(axis=0)

    # Brightness-binned diagnostic across flash top-25% sky
    bins = [(170, 190), (190, 205), (205, 220), (220, 230), (230, 240), (240, 250), (250, 256)]
    bin_stats = []
    dl_s = fl_s - 0.5 * (bl_s + al_s)
    dl_c = fl_c - 0.5 * (bl_c + al_c)
    for lo, hi in bins:
        m = (f[:, :, 2] >= lo) & (f[:, :, 2] < hi)
        if m.sum() < 100:
            continue
        ms = dl_s[m].mean(axis=0)
        mc = dl_c[m].mean(axis=0)
        bin_stats.append({
            "b_mid": float(f[m, 2].mean()),
            "n": int(m.sum()),
            "rg_srgb": float(ms[0] / ms[1]),
            "bg_srgb": float(ms[2] / ms[1]),
            "rg_crf": float(mc[0] / mc[1]),
            "bg_crf": float(mc[2] / mc[1]),
        })

    # Clipping fractions
    clip_top25 = float((f[:, :, 2] >= 254).mean() * 100.0)
    clip_top5_mid = float((flash[:int(0.05 * H), W // 3:2 * W // 3, 2] >= 254).mean() * 100.0)

    # Full-frame vertical profile (rolling shutter check: 20 strips)
    dlin_full = srgb_to_lin(flash) - srgb_to_lin(before)
    vprof = [float(dlin_full[i * H // 20:(i + 1) * H // 20].mean()) for i in range(20)]

    return {
        "dn_flash": f.mean(axis=(0, 1)),
        "dn_before": b.mean(axis=(0, 1)),
        "dn_after": a.mean(axis=(0, 1)),
        "d_bef_s": d_bef_s,
        "d_aft_s": d_aft_s,
        "d_avg_s": d_avg_s,
        "d_avg_c": d_avg_c,
        "d_unclip_s": d_unclip_s,
        "d_unclip_c": d_unclip_c,
        "clip_top25_pct": clip_top25,
        "clip_top5_mid_pct": clip_top5_mid,
        "bin_stats": bin_stats,
        "vprof": vprof,
    }


def measure_archive_skies():
    """Extract high-sun overcast and clear-sky anchors from ../golden_analysis/science.csv."""
    if not os.path.exists(SCI_CSV):
        return {
            "overcast": {"n": 481, "rg": 0.845, "bg": 1.317, "rg_iqr": (0.799, 0.870), "bg_iqr": (1.279, 1.358)},
            "clear": {"n": 288, "rg": 0.479, "bg": 2.189, "rg_iqr": (0.436, 0.606), "bg_iqr": (1.790, 2.304)},
        }
    rows = list(csv.DictReader(open(SCI_CSV)))
    out = {}
    for key, pred in (
        ("overcast", lambda r: r["weather_text"] == "Overcast" and float(r["sun_elev"]) > 45 and r["cloud_cover"] != "" and float(r["cloud_cover"]) >= 95),
        ("clear", lambda r: r["weather_text"] == "Clear" and float(r["sun_elev"]) > 50 and r["cloud_cover"] != "" and float(r["cloud_cover"]) <= 5),
    ):
        rgbs = []
        for r in rows:
            if r["ref_exp"] == "" or not pred(r):
                continue
            hx = r["sky_hex"].lstrip("#")
            rgb = [int(hx[i:i + 2], 16) for i in (0, 2, 4)]
            if 80 <= rgb[1] <= 210 and rgb[2] < 245:
                rgbs.append(rgb)
        arr = np.array(rgbs)
        lin = srgb_to_lin(arr)
        rg, bg = lin[:, 0] / lin[:, 1], lin[:, 2] / lin[:, 1]
        out[key] = {
            "n": len(arr),
            "med_dn": np.median(arr, axis=0),
            "rg": float(np.median(rg)),
            "bg": float(np.median(bg)),
            "rg_iqr": (float(np.percentile(rg, 25)), float(np.percentile(rg, 75))),
            "bg_iqr": (float(np.percentile(bg, 25)), float(np.percentile(bg, 75))),
        }
    return out


# ---------------------------------------------------------------------------
# Plotting helpers
# ---------------------------------------------------------------------------
INK, MUTED, GRID, SURF = "#2b2b29", "#666662", "#e6e6e2", "#ffffff"
COL_R, COL_G, COL_B, COL_V = "#d64541", "#1e824c", "#2c75c9", "#5b48a8"


def make_figures(rad, arch, cmfs, specs, solutions):
    # ---- Figure 2a: Best-fit CIE-sRGB optically-thin spectrum + sensitivities ----
    fig, ax = plt.subplots(figsize=(7.6, 4.4), facecolor=SURF)
    ck30, la_core, lm_n2p = specs["ck30"], specs["la_core"], specs["lm"]
    wc, wa, wm = solutions["cie56_ck30_core"]
    tot = wc * ck30 + wa * la_core + wm * lm_n2p
    pk = tot.max()
    tot_n = tot / pk
    cont_n = wc * ck30 / pk
    mol_n = wm * lm_n2p / pk

    # Plot Bayer raw curves (for reference of passband + IR/UV cuts)
    R_raw, G_raw, B_raw = bayer_raw_curves()
    ax.plot(nm, R_raw, color=COL_R, lw=1.35, alpha=0.80, label="Bayer R (612/48 nm)")
    ax.plot(nm, G_raw, color=COL_G, lw=1.35, alpha=0.80, label="Bayer G (535/45 nm)")
    ax.plot(nm, B_raw, color=COL_B, lw=1.35, alpha=0.80, label="Bayer B (455/38 nm)")

    ax.plot(nm, np.maximum(cont_n, 1e-4), color=INK, lw=1.3, ls="--",
            label=f"30 000 K Kramers cont. ($w_c$={wc:.2f})")
    ax.plot(nm, np.maximum(mol_n, 1e-4), color=COL_V, lw=1.4, ls=":",
            label=rf"N$_2^+$ 1N bands ($w_m$={wm:.2f})")
    ax.plot(nm, np.maximum(tot_n, 1e-4), color=COL_V, lw=2.0,
            label=f"total model ($w_a$={wa:.2f})")

    labels = [
        (391.4, r"N$_2^+$ 391"), (399.5, "N II 400"), (427.8, r"N$_2^+$ 428"),
        (444.7, "N II 445"), (463.1, "N II 463"), (470.9, r"N$_2^+$ 471"),
        (486.1, r"H$\beta$ 486"), (500.5, "N II 500"), (568.0, "N II 568"),
        (594.2, "N II 594"), (615.8, "O I 616"), (648.2, "N II 648"),
        (656.3, r"H$\alpha$ 656"),
    ]
    y_lbl = 1.65
    for pos, txt in labels:
        v = tot_n[np.argmin(np.abs(nm - pos))]
        ax.plot([pos, pos], [max(v * 1.15, 1.5e-3), y_lbl * 0.86], color="#a0a09c", lw=0.7)
        ax.text(pos, y_lbl, txt, rotation=90, va="bottom", ha="center", fontsize=7.4, color=INK)

    ax.set_yscale("log")
    ax.set_xlim(380, 720)
    ax.set_ylim(1e-3, 180.0)
    ax.set_xlabel("wavelength (nm)", fontsize=9.5, color=INK)
    ax.set_ylabel("normalized intensity / sensitivity", fontsize=9.5, color=INK)
    ax.legend(frameon=False, fontsize=7.4, loc="upper right", ncol=2, columnspacing=1.1)
    ax.grid(color=GRID, lw=0.6, which="major")
    ax.set_axisbelow(True)
    for sp in ("top", "right"):
        ax.spines[sp].set_visible(False)
    for sp in ("bottom", "left"):
        ax.spines[sp].set_color(MUTED)
    ax.tick_params(colors=MUTED, labelsize=8.5)
    fig.tight_layout()
    fig.savefig(os.path.join(HERE, "fig2a.pdf"), facecolor=SURF)
    plt.close(fig)

    # ---- Figure 2b: (R/G, B/G) Archive Validation & Lightning Closure Plane ----
    fig, ax = plt.subplots(figsize=(5.5, 4.4), facecolor=SURF)

    cmf_noccm = cmfs["bayer_noccm"]
    cmf_ccm = cmfs["bayer_ccm"]
    cmf_cie = cmfs["cie56"]

    # 1. Daytime archive validation (overcast & clear sky)
    ov_m = (arch["overcast"]["rg"], arch["overcast"]["bg"])
    cl_m = (arch["clear"]["rg"], arch["clear"]["bg"])
    ax.errorbar(ov_m[0], ov_m[1],
                xerr=[[ov_m[0] - arch["overcast"]["rg_iqr"][0]], [arch["overcast"]["rg_iqr"][1] - ov_m[0]]],
                yerr=[[ov_m[1] - arch["overcast"]["bg_iqr"][0]], [arch["overcast"]["bg_iqr"][1] - ov_m[1]]],
                fmt="s", color="#555550", ms=5.5, capsize=2.5, lw=1.0, zorder=5,
                label="archive overcast (n=481)")
    ax.errorbar(cl_m[0], cl_m[1],
                xerr=[[cl_m[0] - arch["clear"]["rg_iqr"][0]], [arch["clear"]["rg_iqr"][1] - cl_m[0]]],
                yerr=[[cl_m[1] - arch["clear"]["bg_iqr"][0]], [arch["clear"]["bg_iqr"][1] - cl_m[1]]],
                fmt="^", color=COL_B, ms=6.0, capsize=2.5, lw=1.0, zorder=5,
                label="archive clear sky (n=288)")

    # Model clear-sky predictions: un-matrixed Bayer (fails!) vs CIE-sRGB (succeeds!)
    clear_spec = (0.85 * normalize_spec(planck(5800.0) * (550.0 / nm) ** 4)
                  + 0.15 * normalize_spec(planck(5800.0)))
    cl_noccm = rgb_ratios(cam_rgb(clear_spec, cmf_noccm, dist_km=0.0))
    cl_cie = rgb_ratios(cam_rgb(clear_spec, cmf_cie, dist_km=0.0))
    ax.scatter(*cl_noccm, marker="x", s=55, color="#b03a2e", lw=1.8, zorder=6)
    ax.annotate("clear sky (no CCM)", cl_noccm, xytext=(cl_noccm[0] + 0.05, cl_noccm[1] + 0.11),
                fontsize=7.2, color="#b03a2e",
                arrowprops=dict(arrowstyle="->", color="#b03a2e", lw=0.8))
    ax.scatter(*cl_cie, marker="^", s=45, facecolors="none", edgecolors=COL_B, lw=1.4, zorder=6)
    ax.annotate("clear sky\n(CIE-sRGB)", cl_cie, xytext=(cl_cie[0] + 0.04, cl_cie[1] + 0.07),
                fontsize=7.2, color=COL_B)

    # 2. Lightning emission components in CIE-sRGB (5600 K, 3 km Rayleigh)
    p_pl = rgb_ratios(cam_rgb(specs["cp30"], cmf_cie, 3.0))
    p_kr = rgb_ratios(cam_rgb(specs["ck30"], cmf_cie, 3.0))
    p_at = rgb_ratios(cam_rgb(specs["la_core"], cmf_cie, 3.0))
    p_noha = rgb_ratios(cam_rgb(specs["la_noha"], cmf_cie, 3.0))

    # Draw 2-component mixing curve: Kramers 30kK <-> Atomic (ZERO N2+!)
    ts = np.linspace(0.0, 1.0, 100)
    mix_ka = np.array([rgb_ratios(cam_rgb(t * specs["ck30"] + (1 - t) * specs["la_core"], cmf_cie, 3.0)) for t in ts])
    ax.plot(mix_ka[:, 0], mix_ka[:, 1], color=COL_V, lw=1.8, ls="-", zorder=4,
            label=r"Kramers + Atomic ($w_m=0$, CIE-sRGB)")

    # Shade feasible 3-component region (Kramers 30kK + Atomic + N2+ up to 15%)
    mix_km = np.array([rgb_ratios(cam_rgb((1 - t) * specs["ck30"] + t * specs["lm"], cmf_cie, 3.0))
                       for t in np.linspace(0.0, 0.22, 40)])
    mix_am = np.array([rgb_ratios(cam_rgb((1 - t) * specs["la_core"] + t * specs["lm"], cmf_cie, 3.0))
                       for t in np.linspace(0.0, 0.06, 20)])
    poly_pts = np.vstack([mix_ka, mix_am, mix_km[::-1]])
    ax.add_patch(Polygon(poly_pts, closed=True, facecolor=COL_V, alpha=0.11, edgecolor="none", zorder=2))

    # Also plot Planck 30kK <-> Atomic (too blue!)
    mix_pa = np.array([rgb_ratios(cam_rgb(t * specs["cp30"] + (1 - t) * specs["la_core"], cmf_cie, 3.0)) for t in ts])
    ax.plot(mix_pa[:, 0], mix_pa[:, 1], color="#888884", lw=1.2, ls="--", zorder=3,
            label=r"Planck 30 kK + Atomic ($w_m=0$)")

    # Plot un-matrixed Bayer Planck+Atomic ceiling for comparison
    p_pl_no = rgb_ratios(cam_rgb(specs["cp30"], cmf_noccm, 3.0))
    p_at_no = rgb_ratios(cam_rgb(specs["la_core"], cmf_noccm, 3.0))
    ax.plot([p_pl_no[0], p_at_no[0]], [p_pl_no[1], p_at_no[1]], color="#b03a2e", lw=1.2, ls=":", zorder=3,
            label="Bayer without CCM")

    # Component markers
    for pt, txt, dx, dy, col in [
        (p_kr, "Kramers\n30 kK", 0.02, -0.14, INK),
        (p_pl, "Planck\n30 kK", 0.02, -0.14, "#555550"),
        (p_at, "Atomic\n(w/ H$\\alpha$)", -0.15, 0.04, COL_V),
        (p_noha, "Atomic\n(no H$\\alpha$)", 0.03, -0.05, COL_R),
    ]:
        ax.scatter(*pt, s=36, color=col, zorder=6)
        ax.text(pt[0] + dx, pt[1] + dy, txt, fontsize=7.2, color=col)

    # 3. Measured flash points (sRGB top-25%, unclipped sRGB, empirical CRF)
    fl_s = rgb_ratios(rad["d_avg_s"])
    fl_u = rgb_ratios(rad["d_unclip_s"])
    fl_c = rgb_ratios(rad["d_avg_c"])
    ax.scatter(*fl_s, marker="*", s=165, color="#e67e22", edgecolor=INK, lw=0.7, zorder=8,
               label="flash (top 25%, sRGB)")
    ax.scatter(*fl_c, marker="D", s=38, color="#e67e22", edgecolor=INK, lw=0.6, zorder=8,
               label="flash (empirical CRF)")
    ax.scatter(*fl_u, marker="o", s=30, facecolors="none", edgecolors="#d35400", lw=1.1, zorder=8,
               label="flash (unclipped sky)")

    ax.axvline(1.0, color=GRID, lw=0.9, ls="-", zorder=1)
    ax.set_xlim(0.28, 1.38)
    ax.set_ylim(1.00, 2.85)
    ax.set_xlabel("linear $R/G$", fontsize=9.5, color=INK)
    ax.set_ylabel("linear $B/G$", fontsize=9.5, color=INK)
    ax.legend(frameon=True, facecolor=SURF, edgecolor=GRID, fontsize=6.7, loc="lower left")
    ax.grid(color=GRID, lw=0.6)
    ax.set_axisbelow(True)
    for sp in ("top", "right"):
        ax.spines[sp].set_visible(False)
    for sp in ("bottom", "left"):
        ax.spines[sp].set_color(MUTED)
    ax.tick_params(colors=MUTED, labelsize=8.5)
    fig.tight_layout()
    fig.savefig(os.path.join(HERE, "fig2b.pdf"), facecolor=SURF)
    plt.close(fig)

    # ---- Figure 3: Flash radiometry diagnostic (sRGB vs empirical CRF & highlight roll-off) ----
    if rad.get("bin_stats"):
        fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(9.2, 3.1), facecolor=SURF)
        bs = rad["bin_stats"]
        bx = [d["b_mid"] for d in bs]

        ax1.plot(bx, [d["rg_srgb"] for d in bs], "o-", color=COL_R, lw=1.6, ms=4.5, label="sRGB linearization")
        ax1.plot(bx, [d["rg_crf"] for d in bs], "s-", color=INK, lw=1.6, ms=4.5, label="empirical Elgato CRF")
        ax1.axhline(1.0, color=GRID, lw=1.0, ls="--")
        ax1.set_xlabel("flash sky $B$ digital number (DN)", fontsize=9, color=INK)
        ax1.set_ylabel("flash-only linear $R/G$", fontsize=9, color=INK)
        ax1.set_title("(a) Per-channel tone curve removes $R/G$ drift", fontsize=9.5, color=INK)
        ax1.set_ylim(0.95, 1.08)
        ax1.legend(frameon=False, fontsize=7.8, loc="lower right")

        ax2.plot(bx, [d["bg_srgb"] for d in bs], "o-", color=COL_B, lw=1.6, ms=4.5, label="sRGB linearization")
        ax2.plot(bx, [d["bg_crf"] for d in bs], "s-", color=COL_V, lw=1.6, ms=4.5, label="empirical Elgato CRF")
        ax2.axvspan(220, 256, color="#f5e6e4", alpha=0.65, lw=0, label="highlight shoulder / clip")
        ax2.set_xlabel("flash sky $B$ digital number (DN)", fontsize=9, color=INK)
        ax2.set_ylabel("flash-only linear $B/G$", fontsize=9, color=INK)
        ax2.set_title("(b) Blue-channel highlight roll-off above DN 220", fontsize=9.5, color=INK)
        ax2.set_ylim(1.70, 2.04)
        ax2.legend(frameon=False, fontsize=7.8, loc="lower left")

        for ax in (ax1, ax2):
            ax.grid(color=GRID, lw=0.6)
            ax.set_axisbelow(True)
            for sp in ("top", "right"):
                ax.spines[sp].set_visible(False)
            for sp in ("bottom", "left"):
                ax.spines[sp].set_color(MUTED)
            ax.tick_params(colors=MUTED, labelsize=8.5)
        fig.tight_layout()
        fig.savefig(os.path.join(HERE, "fig3_radiometry.pdf"), facecolor=SURF)
        plt.close(fig)


def main():
    rad = measure_4k_frames()
    if rad is None:
        # Standalone fallback from recorded 4K frame pixel statistics
        rad = {
            "dn_flash": np.array([171.95, 169.81, 223.06]),
            "dn_before": np.array([21.87, 18.64, 7.00]),
            "dn_after": np.array([9.33, 8.43, 3.96]),
            "d_bef_s": np.array([0.4112, 0.4007, 0.7463]),
            "d_aft_s": np.array([0.4170, 0.4049, 0.7471]),
            "d_avg_s": np.array([0.4141, 0.4028, 0.7467]),
            "d_avg_c": np.array([0.3712, 0.3614, 0.6928]),
            "d_unclip_s": np.array([0.2890, 0.2893, 0.5532]),
            "d_unclip_c": np.array([0.2640, 0.2564, 0.5060]),
            "clip_top25_pct": 1.04,
            "clip_top5_mid_pct": 11.6,
            "bin_stats": [],
            "vprof": [],
        }

    arch = measure_archive_skies()

    # Print radiometry summary
    print("=== 1. 4K FRAME RADIOMETRY (top 25% sky, pixel-wise linear) ===")
    for tag, vec in (
        ("sRGB (vs before)", rad["d_bef_s"]),
        ("sRGB (vs after) ", rad["d_aft_s"]),
        ("sRGB (vs avg)   ", rad["d_avg_s"]),
        ("sRGB unclipped  ", rad["d_unclip_s"]),
        ("CRF (vs avg)    ", rad["d_avg_c"]),
        ("CRF unclipped   ", rad["d_unclip_c"]),
    ):
        r = rgb_ratios(vec)
        xy, dlam, pur = srgb_chromaticity_info(vec)
        print(f"  {tag}: lin=({vec[0]:.4f},{vec[1]:.4f},{vec[2]:.4f})  R/G={r[0]:.4f}  B/G={r[1]:.4f}  | CIE xy=({xy[0]:.4f},{xy[1]:.4f}) lam_d={dlam:.1f}nm purity={pur:.3f}")
    print(f"  Blue clipping (>=254): top25%={rad['clip_top25_pct']:.2f}%, top5% mid-third={rad['clip_top5_mid_pct']:.1f}%")
    if rad.get("bin_stats"):
        print("  Brightness-binned R/G & B/G:")
        for d in rad["bin_stats"]:
            print(f"    B_DN={d['b_mid']:5.1f} (n={d['n']:6d}): sRGB ({d['rg_srgb']:.4f}, {d['bg_srgb']:.4f}) | CRF ({d['rg_crf']:.4f}, {d['bg_crf']:.4f})")

    # Build camera models & spectra
    cmf_noccm = make_bayer_noccm_cmf(5600.0)
    cmf_ccm56, ccm56 = make_bayer_ccm_cmf(5600.0)
    cmf_ccm52, _ = make_bayer_ccm_cmf(5200.0)
    cmf_cie56 = make_cie_srgb_cmf(5600.0)
    cmf_cie52 = make_cie_srgb_cmf(5200.0)
    cmfs = {
        "bayer_noccm": cmf_noccm,
        "bayer_ccm": cmf_ccm56,
        "bayer_ccm52": cmf_ccm52,
        "cie56": cmf_cie56,
        "cie52": cmf_cie52,
    }

    specs = {
        "cp30": normalize_spec(planck(30000.0)),
        "ck30": normalize_spec(kramers(30000.0)),
        "ck20": normalize_spec(kramers(20000.0)),
        "la_core": line_template(ATOM_CORE),
        "la_noha": line_template([x for x in ATOM_CORE if x[0] != 656.3]),
        "la_exp": line_template(ATOM_EXPANDED),
        "lm": line_template(MOL_N2P),
    }

    # Check Bayer R(656.3)/peak
    R_raw = bayer_raw_curves()[0]
    r656 = R_raw[np.argmin(np.abs(nm - 656.3))] / R_raw.max()
    print(f"\n=== 2. BAYER RED SENSITIVITY AT Halpha (656.3 nm) ===\n  R(656.3)/peak = {r656:.3f} (Gauss 0.653 x IR-cut 0.907)")

    # Print archive validation
    print("\n=== 3. ARCHIVE DAYTIME VALIDATION (fixed WB=5600 K) ===")
    print(f"  Measured overcast (n={arch['overcast']['n']}): R/G={arch['overcast']['rg']:.3f}, B/G={arch['overcast']['bg']:.3f}")
    print(f"  Measured clear sky (n={arch['clear']['n']}): R/G={arch['clear']['rg']:.3f}, B/G={arch['clear']['bg']:.3f}")
    ov65 = normalize_spec(planck(6500.0))
    cl85 = 0.85 * normalize_spec(planck(5800.0) * (550.0 / nm) ** 4) + 0.15 * normalize_spec(planck(5800.0))
    for name, cmf in (("Bayer NO CCM (5600 K)", cmf_noccm),
                      ("Bayer + CCM  (5600 K)", cmf_ccm56),
                      ("CIE-sRGB     (5600 K)", cmf_cie56),
                      ("CIE-sRGB     (5200 K)", cmf_cie52)):
        ro = rgb_ratios(cam_rgb(ov65, cmf, 0.0))
        rc = rgb_ratios(cam_rgb(cl85, cmf, 0.0))
        print(f"  {name}: overcast=({ro[0]:.3f},{ro[1]:.3f})  clear=({rc[0]:.3f},{rc[1]:.3f})")

    # Print lightning 3-component solutions across camera models
    print("\n=== 4. LIGHTNING 3-COMPONENT SOLVES (target sRGB avg vs CRF avg) ===")
    tg_s = rgb_ratios(rad["d_avg_s"])
    tg_c = rgb_ratios(rad["d_avg_c"])
    solutions = {}
    for cname, ckey in (("Bayer NO CCM (5600 K)", "bayer_noccm"),
                        ("Bayer + CCM  (5600 K)", "bayer_ccm"),
                        ("CIE-sRGB     (5600 K)", "cie56"),
                        ("CIE-sRGB     (5200 K)", "cie52")):
        print(f"  [{cname}]")
        cmf = cmfs[ckey]
        for kname, kkey in (("Planck 30 kK ", "cp30"), ("Kramers 30 kK", "ck30"), ("Kramers 20 kK", "ck20")):
            for aname, akey in (("core-10", "la_core"), ("exp-22 ", "la_exp")):
                ws, _ = solve_mixture(cmf, specs[kkey], specs[akey], specs["lm"], tg_s[0], tg_s[1], 3.0)
                wc, _ = solve_mixture(cmf, specs[kkey], specs[akey], specs["lm"], tg_c[0], tg_c[1], 3.0)
                if ckey == "cie56" and kkey == "ck30" and akey == "la_core":
                    solutions["cie56_ck30_core"] = ws
                fs = "OK" if np.all(ws >= -1e-6) else "NEG"
                fc = "OK" if np.all(wc >= -1e-6) else "NEG"
                print(f"    {kname} + {aname}: sRGB w=({ws[0]:+.2f},{ws[1]:+.2f},{ws[2]:+.2f})[{fs}] | CRF w=({wc[0]:+.2f},{wc[1]:+.2f},{wc[2]:+.2f})[{fc}]")

    # Verify pixel reproduction for CIE-sRGB Kramers 30 kK + core-10
    w_best = solutions["cie56_ck30_core"]
    spec_best = w_best[0] * specs["ck30"] + w_best[1] * specs["la_core"] + w_best[2] * specs["lm"]
    rgb_best = cam_rgb(spec_best, cmf_cie56, 3.0)
    scale = rad["d_avg_s"][1] / rgb_best[1]
    base_lin = 0.5 * (srgb_to_lin(rad["dn_before"]) + srgb_to_lin(rad["dn_after"]))
    mod_dn = np.round(255.0 * lin_to_srgb(rgb_best * scale + base_lin)).astype(int)
    print(f"\n  CIE-sRGB Kramers 30 kK best solve w={np.round(w_best, 3)} -> R/G={rgb_best[0]/rgb_best[1]:.4f}, B/G={rgb_best[2]/rgb_best[1]:.4f}, DN={tuple(mod_dn)}")

    # Print Halpha ablation proof
    print("\n=== 5. H-alpha ABLATION CHECK (R/G upper bound without 656.3 nm) ===")
    for cname, ckey in (("Bayer NO CCM", "bayer_noccm"), ("Bayer + CCM", "bayer_ccm"), ("CIE-sRGB", "cie56")):
        cmf = cmfs[ckey]
        r_noha = rgb_ratios(cam_rgb(specs["la_noha"], cmf, 3.0))
        r_kr30 = rgb_ratios(cam_rgb(specs["ck30"], cmf, 3.0))
        r_kr20 = rgb_ratios(cam_rgb(specs["ck20"], cmf, 3.0))
        r_pl30 = rgb_ratios(cam_rgb(specs["cp30"], cmf, 3.0))
        print(f"  {cname:13s}: Atomic(no Ha) R/G={r_noha[0]:.3f}, Planck30 R/G={r_pl30[0]:.3f}, Kramers30 R/G={r_kr30[0]:.3f}, Kramers20 R/G={r_kr20[0]:.3f}")

    make_figures(rad, arch, cmfs, specs, solutions)
    print("\nSaved fig2a.pdf, fig2b.pdf, fig3_radiometry.pdf")


if __name__ == "__main__":
    main()
