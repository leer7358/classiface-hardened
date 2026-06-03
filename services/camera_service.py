import cv2
import time

_video = None

def init_camera(index: int = 0, width: int = 640, height: int = 480):
    global _video
    if _video is None:
        _video = cv2.VideoCapture(index)
        _video.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        _video.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        _video.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
    return _video

def flush_camera(cap, n: int = 10):
    for _ in range(n):
        cap.read()

def read_frame(cap, retry: int = 3, sleep_s: float = 0.02):
    for _ in range(retry):
        ok, frame = cap.read()
        if ok:
            return True, frame
        time.sleep(sleep_s)
    return False, None