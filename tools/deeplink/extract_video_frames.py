import cv2
import os

video_path = r"C:\Users\user\Downloads\video_2026-07-25_17-13-32.mp4"
out_dir = r"c:\p2p_scanner\tools\deeplink\shots"

cap = cv2.VideoCapture(video_path)
total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
fps = cap.get(cv2.CAP_PROP_FPS)
duration = total_frames / fps if fps > 0 else 0

print(f"Video loaded: {total_frames} frames, {fps:.1f} FPS, duration: {duration:.1f}s")

# Extract 8 keyframes across video duration
num_samples = 8
step = max(1, total_frames // num_samples)

saved_count = 0
for i in range(num_samples):
    frame_idx = i * step
    cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
    ret, frame = cap.read()
    if ret:
        saved_count += 1
        out_name = f"video_frame_{saved_count:02d}.png"
        out_path = os.path.join(out_dir, out_name)
        cv2.imwrite(out_path, frame)
        timestamp_sec = frame_idx / fps if fps > 0 else 0
        print(f"Saved {out_name} at t={timestamp_sec:.1f}s")

cap.release()
print(f"Extraction complete: {saved_count} keyframes saved to {out_dir}")
