"""Interactive video subtitle ROI selector for Windows.

Requirements: pip install opencv-python
Run: python subtitle_region_selector.py [video_path]
Mouse: drag to select ROI. Keys: A/D = +/- 1 second; J/L = +/- 5 seconds;
S/Enter = save JSON and annotated PNG; R = clear; Q/Esc = quit.
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

import cv2


WINDOW_NAME = "Subtitle Region Selector"
MAX_PREVIEW_WIDTH = 1100
MAX_PREVIEW_HEIGHT = 700


def choose_video():
    """Native file dialog only if path isn't provided."""
    import tkinter as tk
    from tkinter import filedialog

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    path = filedialog.askopenfilename(
        title="Select a video",
        filetypes=[
            ("Video files", "*.mp4 *.mkv *.mov *.avi *.webm *.m4v"),
            ("All files", "*.*"),
        ],
    )
    root.destroy()
    return path


class SubtitleRegionSelector:
    def __init__(self, video_path, output_path=None):
        self.video_path = Path(video_path).expanduser().resolve()
        self.capture = cv2.VideoCapture(str(self.video_path))
        if not self.capture.isOpened():
            raise RuntimeError(f"Cannot open video: {self.video_path}")

        self.video_width = int(self.capture.get(cv2.CAP_PROP_FRAME_WIDTH))
        self.video_height = int(self.capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
        self.frame_count = int(self.capture.get(cv2.CAP_PROP_FRAME_COUNT))
        self.fps = float(self.capture.get(cv2.CAP_PROP_FPS))
        if self.video_width <= 0 or self.video_height <= 0:
            raise RuntimeError("Invalid video dimensions")
        if self.fps <= 0:
            self.fps = 25.0

        self.out_file = (Path(output_path).expanduser().resolve() if output_path
                         else self.video_path.with_name(self.video_path.stem + "_subtitle_region.json"))
        self.scale = min(MAX_PREVIEW_WIDTH / self.video_width,
                         MAX_PREVIEW_HEIGHT / self.video_height, 1.0)
        self.preview_width = max(1, round(self.video_width * self.scale))
        self.preview_height = max(1, round(self.video_height * self.scale))

        self.frame_index = min(round(10 * self.fps), max(0, self.frame_count - 1))
        self.frame = None
        self.preview = None
        self.start = None
        self.end = None
        self.dragging = False
        self.region = None  # (x, y, width, height) in ORIGINAL pixels
        self.seek(self.frame_index)

    def seek(self, frame_index):
        if self.frame_count > 0:
            frame_index = min(max(0, frame_index), self.frame_count - 1)
        else:
            frame_index = max(0, frame_index)
        self.capture.set(cv2.CAP_PROP_POS_FRAMES, frame_index)
        ok, frame = self.capture.read()
        if not ok:
            return False
        self.frame_index = frame_index
        self.frame = frame
        self.preview = cv2.resize(frame, (self.preview_width, self.preview_height),
                                  interpolation=cv2.INTER_AREA)
        return True

    def to_video_coords(self, point):
        # Preview is resized from the actual VIDEO frame (not screen pixels).
        xp, yp = point
        x = round(xp * self.video_width / self.preview_width)
        y = round(yp * self.video_height / self.preview_height)
        return (min(max(x, 0), self.video_width),
                min(max(y, 0), self.video_height))

    def on_mouse(self, event, x, y, flags, userdata):
        x = min(max(x, 0), self.preview_width - 1)
        y = min(max(y, 0), self.preview_height - 1)
        if event == cv2.EVENT_LBUTTONDOWN:
            self.dragging = True
            self.start = (x, y)
            self.end = (x, y)
        elif event == cv2.EVENT_MOUSEMOVE and self.dragging:
            self.end = (x, y)
        elif event == cv2.EVENT_LBUTTONUP and self.dragging:
            self.dragging = False
            self.end = (x, y)
            p1 = self.to_video_coords(self.start)
            p2 = self.to_video_coords(self.end)
            left, right = sorted((p1[0], p2[0]))
            top, bottom = sorted((p1[1], p2[1]))
            if right > left and bottom > top:
                self.region = (left, top, right - left, bottom - top)

    def render(self):
        canvas = self.preview.copy()
        if self.region:
            rx, ry, rw, rh = self.region
            x1 = round(rx * self.preview_width / self.video_width)
            y1 = round(ry * self.preview_height / self.video_height)
            x2 = round((rx + rw) * self.preview_width / self.video_width)
            y2 = round((ry + rh) * self.preview_height / self.video_height)
            cv2.rectangle(canvas, (x1, y1), (x2, y2), (0, 255, 0), 2)
        if self.dragging and self.start and self.end:
            cv2.rectangle(canvas, self.start, self.end, (0, 255, 255), 2)
        secs = self.frame_index / self.fps
        title = f"{self.video_width}x{self.video_height} | {secs:.2f}s | Drag ROI | A/D:1s J/L:5s S:save R:reset Q:quit"
        cv2.rectangle(canvas, (0, 0), (self.preview_width, 27), (20, 20, 20), -1)
        cv2.putText(canvas, title, (8, 19), cv2.FONT_HERSHEY_SIMPLEX, 0.43,
                    (255, 255, 255), 1, cv2.LINE_AA)
        return canvas

    def save(self):
        if self.region is None:
            print("[INFO] Draw a rectangle over subtitles before saving.")
            return
        x, y, w, h = self.region
        normalized = {"x": x / self.video_width, "y": y / self.video_height,
                      "width": w / self.video_width, "height": h / self.video_height}
        result = {
            "video": str(self.video_path),
            "video_resolution": {"width": self.video_width, "height": self.video_height},
            "selected_frame_time_seconds": round(self.frame_index / self.fps, 3),
            "region_pixels": {"x": x, "y": y, "width": w, "height": h},
            "region_normalized": {key: round(value, 8) for key, value in normalized.items()},
            "region_percent": {key: round(value * 100, 4) for key, value in normalized.items()},
            "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        }
        self.out_file.parent.mkdir(parents=True, exist_ok=True)
        with self.out_file.open("w", encoding="utf-8") as stream:
            json.dump(result, stream, ensure_ascii=False, indent=2)
        annotated = self.frame.copy()
        cv2.rectangle(annotated, (x, y), (x+w, y+h), (0, 255, 0), 3)
        preview_file = self.out_file.with_name(self.out_file.stem + "_preview.png")
        # imencode + tofile supports non-ASCII Windows file paths.
        success, encoded = cv2.imencode(".png", annotated)
        if success:
            encoded.tofile(str(preview_file))
        print(f"[SAVED] JSON: {self.out_file}")
        if success:
            print(f"[SAVED] Preview: {preview_file}")
        print(json.dumps(result["region_percent"], ensure_ascii=False, indent=2))

    def run(self):
        cv2.namedWindow(WINDOW_NAME, cv2.WINDOW_AUTOSIZE)
        cv2.setMouseCallback(WINDOW_NAME, self.on_mouse)
        print("Drag with mouse to select subtitle region. S/Enter=save, R=reset, Q/Esc=quit.")
        print("A/D: -/+1 second; J/L: -/+5 seconds. Preview is scaled; saved values use video pixels.")
        try:
            while True:
                cv2.imshow(WINDOW_NAME, self.render())
                key = cv2.waitKey(30) & 0xFF
                if key in (ord('q'), 27):
                    break
                if key in (ord('s'), 13, 10):
                    self.save()
                elif key == ord('r'):
                    self.region = None
                elif key in (ord('a'), ord('d'), ord('j'), ord('l')):
                    delta = {ord('a'): -1, ord('d'): 1, ord('j'): -5, ord('l'): 5}[key]
                    self.seek(self.frame_index + round(delta * self.fps))
                # Close via window titlebar X.
                if cv2.getWindowProperty(WINDOW_NAME, cv2.WND_PROP_VISIBLE) < 1:
                    break
        finally:
            self.capture.release()
            cv2.destroyAllWindows()


def main():
    parser = argparse.ArgumentParser(description="Draw a subtitle ROI on a video and export pixel/percent coordinates.")
    parser.add_argument("video", nargs="?", help="Path to video (opens file picker if omitted)")
    parser.add_argument("--output", help="Optional path for saved JSON")
    args = parser.parse_args()
    path = args.video or choose_video()
    if not path:
        print("No video selected.")
        return 0
    try:
        SubtitleRegionSelector(path, args.output).run()
    except Exception as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
