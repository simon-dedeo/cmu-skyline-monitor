#!/usr/bin/env /opt/local/bin/python3.11
# Prototype: exposure-bracket fusion on the sky cam (0x0578).
# Captures a range of exposures and blends with Mertens exposure fusion,
# which keeps the best-exposed regions from each frame -> sky AND buildings.
import cv2, usb1, numpy as np, time, sys

EXPS = [3, 8, 20, 50, 120, 300]          # short (protect sky) .. long (reveal buildings)
ctx = usb1.USBContext()
h = [d.open() for d in ctx.getDeviceList() if d.getVendorID() == 0x0c45 and d.getProductID() == 0x0578][0]
def w(sel, unit, ln, v): h.controlWrite(0x21, 0x01, sel << 8, unit << 8, int(v).to_bytes(ln, "little"))

cap = cv2.VideoCapture(0); cap.set(3, 1920); cap.set(4, 1200)
for _ in range(8): cap.read()
w(2, 1, 1, 1); w(5, 2, 1, 0)             # manual, powerline off

frames = []
for e in EXPS:
    w(2, 1, 1, 1); w(4, 1, 4, e); time.sleep(0.35)
    for _ in range(8): cap.read()
    ok, f = cap.read()
    if ok:
        frames.append(f)
        cv2.imwrite(f"/tmp/hdr_e{e:04d}.jpg", f, [cv2.IMWRITE_JPEG_QUALITY, 90])
        print("exp=%4d mean=%.0f" % (e, np.mean(f)))
cap.release(); h.close()

# Mertens exposure fusion (weights: contrast, saturation, well-exposedness)
fusion = cv2.createMergeMertens(1.0, 1.0, 1.0).process(frames)
fusion = np.clip(fusion * 255, 0, 255).astype(np.uint8)
cv2.imwrite("/tmp/hdr_fused.jpg", fusion, [cv2.IMWRITE_JPEG_QUALITY, 92])
print("fused mean=%.0f  -> /tmp/hdr_fused.jpg" % np.mean(fusion))
