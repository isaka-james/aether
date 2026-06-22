"""Webcam: capture a still photo from the default camera."""
from __future__ import annotations

import os
import time

from ._util import fail, has, ok, run
from .registry import skill

_DEV = "/dev/video0"


@skill("camera")
def camera(_):
    """Take a photo with the webcam and save it. Uses fswebcam, or ffmpeg as a fallback."""
    if not os.path.exists(_DEV):
        return fail("No webcam was found on this machine.")
    path = f"/tmp/aether-photo-{int(time.time())}.jpg"
    if has("fswebcam"):
        rc, _, err = run(["fswebcam", "-q", "-r", "1280x720", "--no-banner", path], timeout=20)
    elif has("ffmpeg"):
        rc, _, err = run(["ffmpeg", "-y", "-f", "v4l2", "-i", _DEV, "-frames:v", "1", path], timeout=20)
    else:
        return fail("No camera tool is installed. Install fswebcam or ffmpeg.")
    if rc == 0 and os.path.exists(path):
        return ok("Photo captured.", path=path)
    return fail("Couldn't take a photo with the webcam.", error=err)
