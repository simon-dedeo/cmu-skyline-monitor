#!/usr/bin/env /opt/local/bin/python3.11
"""usbreset.py — USB device-level reset of the GS sky cam (Sonix 0c45:0578).

Part of the camera-wedge recovery sequence (see the watchdog in capture.sh):
kill VDCAssistant first — the reset only gets through when no client holds the
device (otherwise the kernel's exclusive claim returns LIBUSB_ERROR_NO_DEVICE).
Validated 2026-07-14: a successful reset re-enumerates the device at a new bus
address, the software equivalent of a replug — but it does NOT remove VBUS
power, so a full firmware lockup can survive it and needs a physical replug.

With --check: only report presence (exit 0 = on the bus, 3 = absent), no reset —
the capture.sh watchdog uses this to skip pointless resets when the camera is
physically unplugged.
"""
import sys

import usb1

VID, PID = 0x0C45, 0x0578

check_only = "--check" in sys.argv[1:]
ctx = usb1.USBContext()
for dev in ctx.getDeviceList():
    if dev.getVendorID() == VID and dev.getProductID() == PID:
        if check_only:
            print(f"usbreset: GS cam present (addr {dev.getDeviceAddress()})")
            sys.exit(0)
        try:
            h = dev.open()
            h.resetDevice()
            print(f"usbreset: GS cam reset OK (was addr {dev.getDeviceAddress()})")
        except Exception as e:
            print(f"usbreset: failed: {type(e).__name__} {e}")
        break
else:
    print("usbreset: GS cam not found on bus")
    sys.exit(3)
