#!/usr/bin/env /Users/proofsandreasons/monitor/venv/bin/python3
"""
expose.py — controlled single-frame capture for the Arducam cameras.

Two capture modes, chosen per camera by what the hardware actually supports
(established empirically 2026-07-09 — see REPORT.md):

  sky  (Arducam B0578 GS, 1920x1200): UVC manual exposure WORKS.
        Predictive auto-exposure, no HDR: brightness is ~linear in exposure
        time, so one probe frame predicts the exposure that puts a metered
        percentile on target -> E' = E * (target / metered). Seeded from the
        last exposure and aimed at a constant target => flicker-free timelapse.
        This sensor's byte ceiling is ~228 (never hits 254); above SAT_CEIL the
        metered value is saturated, so we cut exposure hard until it's in the
        linear range, then predict. Night: raise gain (<=20) when exposure maxes.

  courtyard (Arducam 1080P Low Light, 1920x1080): UVC manual exposure has NO
        effect on this model (verified: frame mean is pinned regardless of the
        exposure written). So we use its own auto-exposure via imagesnap, which
        looks good by day. It will flicker frame-to-frame; deflicker in post.

Control is raw UVC over libusb1; frames come from OpenCV on the same physical
device. Camera control needs TCC camera access, so run this under the
ssh->localhost hop (see tick.sh), not directly from launchd.

    expose.py <sky|courtyard> <outfile.jpg>      # prints one JSON metadata line
"""
import cv2, numpy as np, usb1, time, json, os, sys, subprocess

EXP_MIN, EXP_MAX = 1, 5000        # 0.1 ms units -> 0.1 ms .. 500 ms
GAIN_MAX = 20                     # cap to limit noise (camera allows 0..100)
SAT_CEIL = 225                    # metered values >= this are saturated (sensor ceils ~228)
STATE_DIR = os.path.expanduser("~/monitor")
IMAGESNAP = "/opt/homebrew/bin/imagesnap"
SETTLE, FLUSH, JPEG_Q = 0.35, 8, 92

CAMERAS = {
    "sky":       dict(control=True,  product=0x0578, res=(1920, 1200), exclude=None,
                      roi=(0.00, 0.62), q=60, target=175, hdr=[1/3, 1, 3, 9, 27],
                      # ADAPTIVE HDR: only fuse when the single metered frame's building
                      # band (rows 52-82%) mean is below this (i.e. buildings in shadow /
                      # high contrast). Above it, keep the crisper single frame. Tunable.
                      hdr_building_dark=90,
                      label="Skyline · B0578 GS"),
    # Archive-only night/twilight boost variant (never uploaded). Long exposure
    # + raised gain to pull out the blue-hour gradient and city glow that the
    # flicker-free daytime exposure leaves black. Fixed settings, no metering /
    # HDR. Deep night is still mostly amplified noise (measured 2026-07-12/13:
    # even gain 100 gives mean ~8), but twilight has real recoverable detail.
    "sky_night": dict(control=True, product=0x0578, res=(1920, 1200), exclude=None,
                      roi=(0.00, 0.62), q=60, fixed_exposure=EXP_MAX, fixed_gain=60,
                      label="Skyline night-boost · B0578 GS"),
    # Radiometric REFERENCE frame (Simon 2026-07-14): the exposure comes from a
    # fixed sun-elevation lookup — NEVER from scene metering, never HDR — so the
    # same sky state is always measured with the same camera settings and the
    # colour-of-the-day bars can't be moved by metering drift or mode switches.
    # A single fixed exposure can't span noon (metered E≈12) to dusk (E≈450)
    # without clipping or blackness; elevation-keyed is the deterministic
    # compromise. Table = (min sun elevation °, exposure 0.1ms units, gain);
    # first row whose threshold the sun clears wins. Values seeded from the
    # 2026-07-14 metering logs targeting sky p60 ≈ 130-160 (clip is ~228);
    # tune against science.csv ref_exp/sky_hex as data accumulates.
    "sky_ref":   dict(control=True, product=0x0578, res=(1920, 1200), exclude=None,
                      roi=(0.00, 0.62), q=60,
                      elev_table=[(40, 8, 0), (25, 12, 0), (15, 20, 0), (8, 40, 0),
                                  (3, 90, 0), (0, 120, 0), (-4, 400, 0),
                                  (-90, 1500, 20)],
                      label="Skyline reference · B0578 GS"),
    "courtyard": dict(control=False, product=0x0261, device="Arducam 1080P Low Light",
                      roi=(0.42, 1.00), q=55, target=115,
                      # (exposure, brightness) bracket. This cam is so sensitive it
                      # blows the sky even at min exposure — but the brightness offset
                      # buys headroom, so the first frame (bright -64) captures the
                      # clouds while the later frames expose the courtyard. Fused with
                      # well-exposedness weight 0 (punchier, not milky).
                      hdr_eb=[(1, -64), (1, 0), (4, 0), (8, 0), (16, 0)], hdr_weights=(1, 1, 0),
                      label="Courtyard · 1080P Low Light"),
}


class Arducam:
    """Raw UVC control over libusb1 (generalized from the old capture.py)."""
    def __init__(self, product_id, vendor_id=0x0c45):
        self.ctx = usb1.USBContext(); self.handle = None
        for dev in self.ctx.getDeviceList():
            if dev.getVendorID() == vendor_id and dev.getProductID() == product_id:
                self.handle = dev.open(); break
        if not self.handle:
            raise RuntimeError(f"USB camera {vendor_id:#06x}:{product_id:#06x} not found")

    def _write(self, sel, unit, ln, v, signed=False):
        self.handle.controlWrite(0x21, 0x01, sel << 8, unit << 8, int(v).to_bytes(ln, "little", signed=signed))
    def manual(self):            self._write(2, 1, 1, 1)     # AE mode 1 = manual
    def auto(self):              self._write(2, 1, 1, 8)     # AE mode 8 = aperture-priority auto
    def powerline(self, m=0):    self._write(5, 2, 1, m)     # 0 = off
    def set_exposure(self, v):   self._write(4, 1, 4, max(EXP_MIN, min(EXP_MAX, int(v))))
    def set_gain(self, v):       self._write(4, 2, 2, max(0, min(GAIN_MAX, int(v))))
    def set_brightness(self, v): self._write(2, 2, 2, max(-64, min(64, int(v))), signed=True)  # digital offset
    def close(self):
        if self.handle: self.handle.close(); self.handle = None


def _supports(cap, res):
    cap.set(3, res[0]); cap.set(4, res[1])
    return (int(cap.get(3)), int(cap.get(4))) == tuple(res)


def find_index(res, exclude=None, cached=None):
    """OpenCV index delivering `res` but NOT `exclude` (the GS accepts both 1200
    and 1080, so the low-light cam is 'does 1080 but not 1200')."""
    order = ([cached] if cached is not None else []) + [i for i in range(5) if i != cached]
    for idx in order:
        cap = cv2.VideoCapture(idx)
        if not cap.isOpened():
            cap.release(); continue
        ok = _supports(cap, res) and not (exclude and _supports(cap, exclude))
        cap.release()
        if ok:
            return idx
    raise RuntimeError(f"no OpenCV camera delivers {res} (excluding {exclude})")


def fuse(frames, weights=(1.0, 1.0, 1.0)):
    """Mertens exposure fusion. weights = (contrast, saturation, well-exposedness).
    OpenCV's default well-exposedness is 0.0; a high value pulls toward mid-gray
    (milkier).

    DROP any frame whose resolution differs from the majority rather than
    resizing it: the GS sky cam occasionally delivers 1920x1080 instead of 1200,
    and stretching that to match then fusing it produced a ghosted, horizontally
    -banded 'venetian-blind' frame (seen at the dawn transition, 2026-07-11
    06:39). Fusing fewer clean frames beats blending one stretched frame."""
    frames = [f for f in frames if f is not None]
    shapes = [f.shape[:2] for f in frames]
    ref = max(set(shapes), key=shapes.count)   # modal resolution
    frames = [f for f in frames if f.shape[:2] == ref]
    return np.clip(cv2.createMergeMertens(*weights).process(frames) * 255, 0, 255).astype(np.uint8)


def meter(frame, cfg):
    """(percentile value within the ROI, clipped fraction of the full frame)."""
    g = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    clip = float(np.mean(g >= 254))
    t, b = cfg["roi"]; H = g.shape[0]
    return float(np.percentile(g[int(t * H):int(b * H), :], cfg["q"])), clip


def load_state(name):
    try:    return json.load(open(f"{STATE_DIR}/state_{name}.json"))
    except Exception: return {"exposure": 8, "gain": 0, "index": None}

def save_state(name, st):
    try:    json.dump(st, open(f"{STATE_DIR}/state_{name}.json", "w"))
    except Exception: pass


def _snap(device, outfile):
    subprocess.run([IMAGESNAP, "-q", "-w", "1.5", "-d", device, outfile],
                   check=True, timeout=40, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _plain_auto(cfg, outfile):
    """Single auto-exposure frame via imagesnap. Force AUTO AE mode first — a prior
    manual session leaves this cam stuck in manual (ignores exposure -> free-runs
    blown-white). Auto=8 is the only valid auto code. Retry if still blown."""
    try:
        c = Arducam(cfg["product"]); c.set_brightness(0); c.auto(); c.close(); time.sleep(0.5)
    except Exception:
        pass
    _snap(cfg["device"], outfile)                 # discard/settle frame
    frame = cv2.imread(outfile); mn = float(np.mean(frame))
    for _ in range(2):
        if mn < 248: break
        time.sleep(1.5); _snap(cfg["device"], outfile)
        frame = cv2.imread(outfile); mn = float(np.mean(frame))
    return frame


def capture_auto(name, cfg, outfile):
    """Capture for a cam whose live OpenCV session ignores manual control (the
    low-light courtyard cam). Uses fresh imagesnap sessions with libusb manual
    control — the only way settings take on this model.

    `hdr_eb` = an (exposure, brightness) bracket. The first frame uses a negative
    brightness offset to hold the bright sky (clouds); later frames use normal
    brightness and longer exposures for the courtyard. Mertens-fuses them (with
    `hdr_weights`) so clouds AND courtyard are both exposed — works in full sun,
    where a plain exposure bracket can't (the sky clips in every frame). Falls
    back to plain auto only when everything is too dark (deep night)."""
    mode = "auto"
    if cfg.get("hdr_eb"):
        try:
            cam = Arducam(cfg["product"]); temps = []

            def shoot(e, b):
                cam.manual(); cam.set_gain(0); cam.set_brightness(b); cam.set_exposure(e); time.sleep(0.3)
                tmp = f"{outfile}.b{e}_{b}.jpg"; temps.append(tmp)
                _snap(cfg["device"], tmp); return cv2.imread(tmp)

            frames = [shoot(e, b) for (e, b) in cfg["hdr_eb"]]
            cam.set_brightness(0); cam.auto(); cam.close()      # restore for other jobs
            if max(float(np.mean(f)) for f in frames if f is not None) >= 55:
                frame = fuse(frames, cfg.get("hdr_weights", (1, 1, 1)))
                cv2.imwrite(outfile, frame, [cv2.IMWRITE_JPEG_QUALITY, JPEG_Q])
                mode = "hdr"
            else:
                frame = _plain_auto(cfg, outfile); mode = "auto-night"
            for t in temps:
                try: os.remove(t)
                except Exception: pass
        except Exception:
            frame = _plain_auto(cfg, outfile)
    else:
        frame = _plain_auto(cfg, outfile)
    P, clip = meter(frame, cfg)
    return {"camera": name, "mode": mode, "exposure": None, "gain": None,
            "metered_p%d" % cfg["q"]: round(P, 1), "clip_pct": round(clip * 100, 2),
            "mean": round(float(np.mean(frame)), 1), "label": cfg["label"]}


def capture_manual(name, cfg, outfile, log):
    """Controlled-exposure capture via libusb + OpenCV.

    First find the good single exposure E* by predictive metering (brightness is
    ~linear in exposure, so E' = E*(target/metered) converges in 1-2 frames).
    Then, if the camera has an `hdr` bracket, capture exposures spread around E*
    and blend them with Mertens exposure fusion so the sky AND the shadowed
    buildings are both well-exposed in the final frame."""
    st = load_state(name)
    E, G = int(st.get("exposure", 8)), int(st.get("gain", 0))
    cam = Arducam(cfg["product"])
    try:
        idx = find_index(cfg["res"], cfg.get("exclude"), st.get("index"))
        cap = cv2.VideoCapture(idx); cap.set(3, cfg["res"][0]); cap.set(4, cfg["res"][1])
        for _ in range(FLUSH): cap.read()
        cam.manual(); cam.powerline(0)

        def grab(exp, gain=0):
            cam.manual(); cam.set_gain(gain); cam.set_exposure(exp)
            time.sleep(SETTLE)
            for _ in range(FLUSH): cap.read()
            ok, f = cap.read()
            return f if ok else None

        # --- 1. converge to a good single exposure E* (meters the ROI) ---
        T = cfg["target"]; frame = None; P = 0.0; clip = 0.0
        for attempt in range(6):
            frame = grab(E, G)
            if frame is None:
                time.sleep(0.2); continue
            P, clip = meter(frame, cfg)
            log(f"  meter{attempt}: exp={E:>5} gain={G:>2}  p{cfg['q']}(roi)={P:6.1f}")
            if 0.85 * T <= P <= 1.12 * T:
                break
            if P >= SAT_CEIL and E > EXP_MIN:
                E = max(EXP_MIN, E // 4); G = 0; continue
            ratio = T / max(P, 1.0)
            E_new = int(max(EXP_MIN, min(EXP_MAX, round(E * ratio))))
            if E_new >= EXP_MAX and P < 0.7 * T:
                E = EXP_MAX; G = int(max(5, min(GAIN_MAX, round(max(G, 5) * ratio))))
            else:
                E = E_new
                if E < EXP_MAX: G = 0

        mode = "manual"; exposures = [E]
        # Save the metered single-exposure frame (constant-target, NOT tone-mapped) as
        # the "standardized" frame for science / sky-colour. HDR fusion visibly
        # desaturates the sky (measured 2026-07-13: sky ROI sat 13% fused vs 21% here),
        # so science.py reads this instead of the HDR sky.jpg.
        if frame is not None:
            try:
                cv2.imwrite(os.path.join(STATE_DIR, f"{name}_std.jpg"), frame,
                            [cv2.IMWRITE_JPEG_QUALITY, JPEG_Q])
            except Exception:
                pass
        # --- 2. ADAPTIVE HDR (daytime only). Fuse a bracket ONLY when the single
        #     metered frame leaves the buildings in shadow (high-contrast dusk/
        #     overcast). On directly-lit scenes the single frame is crisper and far
        #     more saturated (measured: sky sat 21% single vs 13% fused, buildings
        #     equally bright), so we keep it. Building band = rows 52-82%. -->
        if (cfg.get("hdr") and G == 0 and E * max(cfg["hdr"]) < EXP_MAX
                and frame is not None):
            Hh = frame.shape[0]
            band = float(cv2.cvtColor(frame[int(0.52 * Hh):int(0.82 * Hh)],
                                      cv2.COLOR_BGR2GRAY).mean())
            if band < cfg.get("hdr_building_dark", 90):
                exposures = sorted({int(max(EXP_MIN, min(EXP_MAX, round(E * m)))) for m in cfg["hdr"]})
                RES = (cfg["res"][1], cfg["res"][0])   # (h, w) — drop stray 1080 frames
                frames = [f for f in (grab(e) for e in exposures)
                          if f is not None and f.shape[:2] == RES]
                if len(frames) >= 2:
                    frame = fuse(frames); mode = "hdr"
                    log(f"  hdr: buildings dark (band={band:.0f}) -> fused {len(frames)} {exposures}")
                else:
                    log(f"  hdr: only {len(frames)}/{len(exposures)} in-spec frames — keeping single")
            else:
                log(f"  single kept: buildings lit (band={band:.0f} >= {cfg.get('hdr_building_dark',90)})")

        cv2.imwrite(outfile, frame, [cv2.IMWRITE_JPEG_QUALITY, JPEG_Q])
        save_state(name, {"exposure": E, "gain": G, "index": idx})
        cap.release()
        return {"camera": name, "mode": mode, "exposure": E, "gain": G, "exposures": exposures,
                "metered_p%d" % cfg["q"]: round(P, 1), "clip_pct": round(clip * 100, 2),
                "target": T, "mean": round(float(np.mean(frame)), 1), "index": idx,
                "label": cfg["label"]}
    finally:
        cam.close()


def capture_fixed(name, cfg, outfile):
    """Single grab at a FIXED manual exposure/gain — no metering, no HDR. Used for
    the archive-only night-boost variant. Shares the sky cam's discovered OpenCV
    index; asserts manual after the session opens (opening resets to auto)."""
    st = load_state("sky")
    cam = Arducam(cfg["product"])
    try:
        idx = find_index(cfg["res"], cfg.get("exclude"), st.get("index"))
        cap = cv2.VideoCapture(idx); cap.set(3, cfg["res"][0]); cap.set(4, cfg["res"][1])
        try:
            for _ in range(FLUSH): cap.read()
            cam.manual(); cam.powerline(0)
            cam.set_gain(cfg["fixed_gain"]); cam.set_exposure(cfg["fixed_exposure"])
            time.sleep(SETTLE)
            for _ in range(FLUSH): cap.read()
            ok, frame = cap.read()
            if not ok or frame is None or frame.shape[:2] != (cfg["res"][1], cfg["res"][0]):
                raise RuntimeError("no in-spec frame")
            cv2.imwrite(outfile, frame, [cv2.IMWRITE_JPEG_QUALITY, JPEG_Q])
        finally:
            cap.release()
        P, clip = meter(frame, cfg)
        return {"camera": name, "mode": "night-fixed", "exposure": cfg["fixed_exposure"],
                "gain": cfg["fixed_gain"], "metered_p%d" % cfg["q"]: round(P, 1),
                "clip_pct": round(clip * 100, 2), "mean": round(float(np.mean(frame)), 1),
                "label": cfg["label"]}
    finally:
        cam.close()


def capture(name, outfile, verbose=False):
    cfg = CAMERAS[name]
    log = (lambda *a: print(*a, file=sys.stderr)) if verbose else (lambda *a: None)
    if cfg.get("elev_table"):
        # Deterministic sun-elevation-keyed settings (see sky_ref config).
        import solar
        elev = solar.sun_elevation()
        exp, gain = next((e, g) for thresh, e, g in cfg["elev_table"] if elev >= thresh)
        meta = capture_fixed(name, dict(cfg, fixed_exposure=exp, fixed_gain=gain), outfile)
        meta["sun_elev"] = round(elev, 2)
        return meta
    if cfg.get("fixed_exposure"):
        return capture_fixed(name, cfg, outfile)
    if cfg.get("control", True):
        return capture_manual(name, cfg, outfile, log)
    return capture_auto(name, cfg, outfile)


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("usage: expose.py <sky|courtyard> <outfile.jpg>", file=sys.stderr); sys.exit(2)
    print(json.dumps(capture(sys.argv[1], sys.argv[2], verbose=True)))
