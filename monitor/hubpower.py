#!/usr/bin/env /opt/local/bin/python3.11
"""Power-cycle downstream ports of the Genesys 05e3:0610 hub cascade (uhubctl-style).
CLEAR_FEATURE(PORT_POWER) then SET_FEATURE(PORT_POWER) on every port of every 0610 hub.
Only the GS cam chain hangs off these hubs, so this is safe."""
import time, sys
import usb1

HUB_VID, HUB_PID = 0x05E3, 0x0610
PORT_POWER = 8
USB_RT_PORT = 0x23  # class request, recipient=other (port)
CLEAR_FEATURE, SET_FEATURE = 1, 3

ctx = usb1.USBContext()
hubs = [d for d in ctx.getDeviceList()
        if d.getVendorID() == HUB_VID and d.getProductID() == HUB_PID]
print(f"found {len(hubs)} Genesys hub(s)")
handles = []
for dev in hubs:
    try:
        h = dev.open()
        handles.append((dev, h))
    except Exception as e:
        print(f"hub addr {dev.getDeviceAddress()}: open failed: {e}")

# power OFF all ports (4-port hubs)
for dev, h in handles:
    for port in range(1, 5):
        try:
            h.controlWrite(USB_RT_PORT, CLEAR_FEATURE, PORT_POWER, port, b"", timeout=1000)
            print(f"hub addr {dev.getDeviceAddress()} port {port}: power OFF ok")
        except Exception as e:
            print(f"hub addr {dev.getDeviceAddress()} port {port}: OFF failed: {type(e).__name__} {e}")
time.sleep(20)
# power ON all ports
for dev, h in handles:
    for port in range(1, 5):
        try:
            h.controlWrite(USB_RT_PORT, SET_FEATURE, PORT_POWER, port, b"", timeout=1000)
            print(f"hub addr {dev.getDeviceAddress()} port {port}: power ON ok")
        except Exception as e:
            print(f"hub addr {dev.getDeviceAddress()} port {port}: ON failed: {type(e).__name__} {e}")
for _, h in handles:
    h.close()
