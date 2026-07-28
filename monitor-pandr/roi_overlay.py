import cv2
img = cv2.imread("/Users/proofsandreasons/monitor/sky.jpg")
H, W = img.shape[:2]
def box(r0, r1, c0, c1, color, label, ty):
    cv2.rectangle(img, (int(c0*W), int(r0*H)), (int(c1*W)-2, int(r1*H)-2), color, 6)
    cv2.putText(img, label, (int(c0*W)+14, int(ty*H)), cv2.FONT_HERSHEY_SIMPLEX, 1.4, color, 4)
box(0.0, 0.5, 1/3, 2/3,  (255, 128, 0),  "SKY colour/hue", 0.06)          # blue-ish
box(0.0, 0.40, 0.0, 1.0, (255, 255, 0),  "CLOUD band (top 40%)", 0.38)    # cyan
box(0.52, 0.82, 0.0, 1.0,(0, 0, 255),    "FACADE / lit windows (52-82%)", 0.565) # red
box(0.55, 1.0, 0.0, 1.0, (0, 165, 255),  "CAMPUS colour (55-100%)", 0.98) # orange
box(0.66, 0.82, 0.30, 0.95,(0, 255, 0),  "LAWN gcc (66-82% x 30-95%)", 0.71) # green
cv2.imwrite("/tmp/roi_overlay.jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 82])
print("wrote /tmp/roi_overlay.jpg", img.shape)
