"""Colorimetric closure test: lightning emission model -> camera RGB.

Model: Planck continuum at T_chan plus Gaussian emission lines (N II, H Balmer),
relative line energies from slitless-spectra literature (Orville 1968; Orville &
Henderson 1984). Push through approximate Bayer channel sensitivities with an
IR-cut edge, apply white-balance gains that neutralize a 5600 K blackbody
(skyshot.sh locks daylight WB), compare to the baseline-subtracted, sRGB-
linearized measured flash color. Free parameters: continuum energy fraction f_c
(0..1) and IR-cut edge lambda_cut. Grid-search chi^2 on the two color ratios.
"""
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

h, c, kB = 6.626e-34, 2.998e8, 1.381e-23
lam = np.linspace(380e-9, 720e-9, 2000)
nm = lam * 1e9

def planck(lam, T):
    x = h * c / (lam * kB * T)
    return (2 * h * c**2 / lam**5) / np.expm1(x)

T_CHAN = 30000.0   # return-stroke peak channel temperature (Orville 1968)
T_WB   = 5600.0    # white-balance reference illuminant

# visible emission lines: (nm, relative energy, sigma_nm)
# N II multiplets + H Balmer; relative energies follow the ordering in
# Orville & Henderson (1984) slitless spectral irradiance (375-880 nm):
# Halpha strongest; N II 500.5 strong; blue N II cluster collectively strong.
LINES = [
    (399.5, 0.45, 2.5),   # N II
    (444.7, 0.30, 2.5),   # N II
    (463.1, 0.40, 2.5),   # N II 463.0/464.3 blend
    (486.1, 0.20, 2.5),   # H beta
    (500.5, 0.70, 2.5),   # N II (strong)
    (568.0, 0.20, 2.5),   # N II
    (594.2, 0.10, 2.5),   # N II
    (656.3, 1.00, 3.0),   # H alpha (strongest visible line)
]

def line_spectrum(lam):
    s = np.zeros_like(lam)
    for lc, amp, sig in LINES:
        s += amp * np.exp(-0.5 * ((lam * 1e9 - lc) / sig) ** 2) / sig
    return s

def sigmoid(x):
    return 1 / (1 + np.exp(-x))

def channels(lam, lam_cut):
    """Approximate Bayer RGB sensitivities (Gaussians, Jiang et al. 2013 family)
    x an IR-cut long-pass edge on all channels."""
    nm_ = lam * 1e9
    B = np.exp(-0.5 * ((nm_ - 460) / 33) ** 2)
    G = np.exp(-0.5 * ((nm_ - 535) / 45) ** 2)
    R = np.exp(-0.5 * ((nm_ - 600) / 42) ** 2)
    cut = sigmoid((lam_cut - nm_) / 6.0)       # transmission ~1 below the edge
    blue_cut = sigmoid((nm_ - 400) / 6.0)      # UV edge
    return [ch * cut * blue_cut for ch in (R, G, B)]

def cam_rgb(spec, lam, lam_cut):
    chans = channels(lam, lam_cut)
    raw = np.array([np.trapezoid(spec * ch, lam) for ch in chans])
    wb_raw = np.array([np.trapezoid(planck(lam, T_WB) * ch, lam) for ch in chans])
    return raw / wb_raw          # WB gains: 5600 K blackbody -> (1,1,1)

# ---- measurement: sky band means, sRGB-decoded, baseline-subtracted ----
def srgb_lin(v):
    v = np.asarray(v) / 255.0
    return np.where(v <= 0.04045, v / 12.92, ((v + 0.055) / 1.055) ** 2.4)

flash_lin = srgb_lin([172, 170, 223])
base_lin = srgb_lin([21.9, 18.6, 7.0])
meas = flash_lin - base_lin
meas_ratios = np.array([meas[0] / meas[1], meas[2] / meas[1]])   # R/G, B/G
print(f"measured linear flash-only RGB: {meas.round(4)}  R/G={meas_ratios[0]:.3f} B/G={meas_ratios[1]:.3f}")

# ---- fit f_c (continuum energy fraction) and lam_cut ----
cont = planck(lam, T_CHAN)
cont /= np.trapezoid(cont, lam)
lines = line_spectrum(lam)
lines /= np.trapezoid(lines, lam)

best = None
for f_c in np.linspace(0.0, 1.0, 201):
    spec = f_c * cont + (1 - f_c) * lines
    for lam_cut in np.arange(640, 701, 2.0):
        rgb = cam_rgb(spec, lam, lam_cut)
        ratios = np.array([rgb[0] / rgb[1], rgb[2] / rgb[1]])
        chi2 = np.sum((np.log(ratios) - np.log(meas_ratios)) ** 2)
        if best is None or chi2 < best[0]:
            best = (chi2, f_c, lam_cut, rgb, ratios)

chi2, f_c, lam_cut, rgb, ratios = best
print(f"best fit: f_c={f_c:.2f}, lam_cut={lam_cut:.0f} nm, chi2={chi2:.5f}")
print(f"model R/G={ratios[0]:.3f} B/G={ratios[1]:.3f}   (meas {meas_ratios[0]:.3f} {meas_ratios[1]:.3f})")

# continuum-only and lines-only predictions for the paper's comparison
for tag, f in (("continuum-only", 1.0), ("lines-only", 0.0)):
    r = cam_rgb(f * cont + (1 - f) * lines, lam, lam_cut)
    print(f"{tag}: R/G={r[0]/r[1]:.3f} B/G={r[2]/r[1]:.3f}")

# what hexes do model and measurement imply (scaled so G matches flash G)?
def lin_srgb(v):
    v = np.clip(v, 0, 1)
    return np.where(v <= 0.0031308, 12.92 * v, 1.055 * v ** (1 / 2.4) - 0.055)

scale = meas[1] / (rgb[1] / 1.0)
model_lin = rgb * scale + base_lin
model_hex = (255 * lin_srgb(model_lin)).round().astype(int)
print(f"model sky hex (incl. baseline): #{model_hex[0]:02x}{model_hex[1]:02x}{model_hex[2]:02x}  vs measured #acaadf-ish (172,170,223)")

# ---- figure: spectrum + channel sensitivities + RGB bars ----
INK, MUTED, GRID, SURF = '#333331', '#6b6b68', '#e8e8e6', '#ffffff'
fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11, 3.7), facecolor=SURF,
                               gridspec_kw={'width_ratios': [1.9, 1]})
spec = f_c * cont + (1 - f_c) * lines
spec_n = spec / spec.max()
cont_n = f_c * cont / spec.max()
ax1.fill_between(nm, spec_n, color='#b9b5ed', alpha=0.55, lw=0, label='total model spectrum')
ax1.plot(nm, cont_n, color=INK, lw=1.4, ls='--', label=f'{T_CHAN:.0f} K continuum ($f_c$={f_c:.2f})')
Rz, Gz, Bz = channels(lam, lam_cut)
for ch, col, lab in ((Rz, '#e34948', 'R channel'), (Gz, '#008300', 'G channel'), (Bz, '#2a78d6', 'B channel')):
    ax1.plot(nm, ch, color=col, lw=1.6, alpha=0.9, label=lab)
for lc, amp, sig in LINES:
    if amp >= 0.4:
        ax1.annotate({399.5: 'N II', 463.1: 'N II', 500.5: 'N II', 656.3: r'H$\alpha$'}.get(lc, ''),
                     (lc, min(1.0, ((1-f_c)*amp/sig/ (spec.max()/ (1/ np.trapezoid(line_spectrum(lam), lam) if False else 1)) ) )),
                     xytext=(lc, 1.04), ha='center', fontsize=8.5, color=INK)
ax1.set_xlim(380, 720); ax1.set_ylim(0, 1.12)
ax1.set_xlabel('wavelength (nm)'); ax1.set_ylabel('normalized intensity / sensitivity')
ax1.legend(frameon=False, fontsize=8, loc='upper right')
ax1.set_title('(a) Emission model and camera channels', fontsize=10)

x = np.arange(3); wdt = 0.38
ax2.bar(x - wdt/2, meas / meas[1], wdt, color=['#e34948', '#008300', '#2a78d6'], alpha=0.85, label='measured')
ax2.bar(x + wdt/2, rgb / rgb[1], wdt, color=['#e34948', '#008300', '#2a78d6'], alpha=0.4,
        hatch='//', label='model')
ax2.set_xticks(x, ['R', 'G', 'B']); ax2.set_ylabel('linear signal (G = 1)')
ax2.legend(frameon=False, fontsize=8)
ax2.set_title('(b) Measured vs. modeled flash color', fontsize=10)
for ax in (ax1, ax2):
    ax.grid(color=GRID, lw=0.6); ax.set_axisbelow(True)
    for s in ax.spines.values(): s.set_visible(False)
    ax.tick_params(colors=MUTED, length=0)
fig.tight_layout()
fig.savefig('fig_spectrum.pdf', facecolor=SURF)
print('figure saved')
