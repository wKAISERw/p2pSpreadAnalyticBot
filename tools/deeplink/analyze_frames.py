import cv2
import os

shots_dir = r"c:\p2p_scanner\tools\deeplink\shots"

for i in range(1, 9):
    fname = f"video_frame_{i:02d}.png"
    fpath = os.path.join(shots_dir, fname)
    if os.path.exists(fpath):
        img = cv2.imread(fpath)
        h, w, _ = img.shape
        print(f"{fname}: {w}x{h} pixels, size={os.path.getsize(fpath)} bytes")
