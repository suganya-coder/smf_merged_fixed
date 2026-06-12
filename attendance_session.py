# =============================================================
# attendance_session.py  —  Smart Attendance System  v9.3
#
# WHAT THIS FILE PROVIDES:
#
#   API MODE  (used by api.py / web dashboard):
#     _SESSION_STATE   — dict polled by api.py lines 9395, 9431
#     _FRAME_QUEUE     — MJPEG frame buffer for /video_feed
#     start_session()  — launches headless background thread
#     stop_session()   — signals thread to stop
#     get_status()     — returns safe copy of _SESSION_STATE
#     generate_frames()— MJPEG generator for /video_feed endpoint
#
#   CLI MODE  (used by main.py menu option [3]):
#     run_session()    — blocking camera loop with cv2 window
#
# HOW TO INSTALL:
#   Copy this file to your project folder and REPLACE the old
#   attendance_session.py completely.  Do NOT merge or append.
#
# REQUIRES:
#   pip install opencv-contrib-python
# =============================================================

import time
import queue
import logging
import datetime
import platform
import threading

import config
import database_postgres as db

log = logging.getLogger(__name__)

# ── CLI stop flag (used by run_session) ──────────────────────
_stop_requested: bool = False

# =============================================================
# API STATE  ← api.py accesses these directly by name
# =============================================================
_SESSION_STATE: dict = {
    "running":    False,   # True while background thread is alive
    "thread":     None,    # threading.Thread object or None
    "period":     None,    # period string e.g. "Period_2"
    "started_at": None,    # ISO datetime string
    "error":      None,    # last error message or None
}

# MJPEG frame queue — background thread pushes JPEG bytes here;
# generate_frames() reads them for the /video_feed endpoint.
_FRAME_QUEUE: queue.Queue = queue.Queue(maxsize=2)


# =============================================================
# Offline placeholder JPEG
# Returns a static dark frame shown when no session is running.
# =============================================================
def _build_offline_jpeg() -> bytes:
    try:
        import cv2
        import numpy as np
        img = np.zeros((360, 640, 3), dtype=np.uint8)
        cv2.putText(img, "Camera Offline",
                    (155, 165), cv2.FONT_HERSHEY_DUPLEX,
                    1.4, (70, 70, 70), 2)
        cv2.putText(img, "Start a session to see the live feed.",
                    (95, 215), cv2.FONT_HERSHEY_SIMPLEX,
                    0.65, (50, 50, 50), 1)
        ok, buf = cv2.imencode(".jpg", img,
                               [cv2.IMWRITE_JPEG_QUALITY, 60])
        if ok:
            return buf.tobytes()
    except Exception:
        pass
    # Minimal valid 1×1 black JPEG (fallback when cv2 is absent)
    return (
        b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01"
        b"\x00\x01\x00\x00\xff\xdb\x00C\x00\x08\x06\x06\x07\x06"
        b"\x05\x08\x07\x07\x07\x09\x09\x08\x0a\x0c\x14\x0d\x0c"
        b"\x0b\x0b\x0c\x19\x12\x13\x0f\x14\x1d\x1a\x1f\x1e\x1d"
        b"\x1a\x1c\x1c $.' \",#\x1c\x1c(7),01444\x1f'9=82<.342"
        b"\x1edL\x82\xff\xd9"
    )


_OFFLINE_JPEG: bytes = _build_offline_jpeg()


# =============================================================
# Camera open helper (Windows DSHOW fix)
# =============================================================
def _open_camera(index: int):
    """
    Try DSHOW first on Windows (avoids MSMF RGB24 error),
    then fall back to other backends.
    Returns cv2.VideoCapture on success, None on failure.
    """
    try:
        import cv2
    except ImportError:
        log.error("cv2 not available — cannot open camera")
        return None

    backends = (
        [cv2.CAP_DSHOW, cv2.CAP_MSMF, cv2.CAP_ANY]
        if platform.system() == "Windows"
        else [cv2.CAP_ANY, cv2.CAP_V4L2]
    )

    for backend in backends:
        try:
            cap = cv2.VideoCapture(index, backend)
            if not cap.isOpened():
                cap.release()
                continue
            cap.set(cv2.CAP_PROP_FRAME_WIDTH,  640)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
            cap.set(cv2.CAP_PROP_FPS,          30)
            cap.set(cv2.CAP_PROP_BUFFERSIZE,   2)
            for _ in range(8):          # flush stale frames
                cap.grab()
                time.sleep(0.02)
            ret, frame = cap.read()
            if ret and frame is not None and frame.size > 0:
                w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
                h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
                log.info("Camera opened: backend=%s %dx%d", backend, w, h)
                print(f"  Camera: {w}x{h}  backend={backend}")
                return cap
            cap.release()
        except Exception as exc:
            log.debug("Backend %s failed: %s", backend, exc)

    return None


# =============================================================
# Period helper
# =============================================================
def _auto_period() -> str:
    """Return current period from timetable, or a timestamp."""
    try:
        p = db.get_current_period()
        if p:
            return p
    except Exception:
        pass
    return f"Manual_{datetime.datetime.now().strftime('%H%M')}"


# =============================================================
# CLI helper
# =============================================================
def _print_today(period: str) -> None:
    print("\n--- Today's Attendance ---")
    try:
        rows = db.get_today_attendance(period)
        if not rows:
            print("  No attendance recorded yet.")
            return
        print(f"  {'Name':<20} {'ID':<15} {'Time':<10} "
              f"{'Conf':>5} {'Engine'}")
        print("  " + "-" * 65)
        for r in rows:
            print(f"  {r.get('name','?'):<20} "
                  f"{r.get('student_id','?'):<15} "
                  f"{str(r.get('time','?'))[:8]:<10} "
                  f"{int(float(r.get('confidence', 0)) * 100):>4}% "
                  f"{r.get('engine','?')}")
    except Exception as exc:
        log.error("Attendance read error: %s", exc)
    print()


# =============================================================
# API MODE — background worker thread (headless, no cv2 window)
# =============================================================
def _session_worker(period: str) -> None:
    """
    Runs in a daemon thread launched by start_session().

    Opens the webcam, calls SmartRecognizer.process_frame() on
    each frame, encodes the annotated result as JPEG, and pushes
    it into _FRAME_QUEUE for the /video_feed MJPEG endpoint.

    No cv2.imshow() or cv2.waitKey() — completely headless so it
    is safe inside a FastAPI / uvicorn server process.
    """
    global _SESSION_STATE

    log.info("[SESSION-WORKER] Starting period=%s", period)

    try:
        import cv2
    except ImportError:
        _SESSION_STATE["error"] = (
            "OpenCV is not installed.  "
            "Run: pip install opencv-contrib-python"
        )
        _SESSION_STATE["running"] = False
        log.error("[SESSION-WORKER] cv2 not available")
        return

    try:
        from recognizer1 import SmartRecognizer
        rec = SmartRecognizer()
    except Exception as exc:
        _SESSION_STATE["error"] = f"Recognizer init failed: {exc}"
        _SESSION_STATE["running"] = False
        log.error("[SESSION-WORKER] SmartRecognizer init error: %s", exc)
        return

    cap = _open_camera(config.CAMERA_INDEX)
    if cap is None:
        _SESSION_STATE["error"] = (
            "Cannot open camera. Close Teams/Zoom/OBS and retry. "
            "Or set CAMERA_INDEX=1 in .env if you have 2 cameras."
        )
        _SESSION_STATE["running"] = False
        log.error("[SESSION-WORKER] Camera open failed")
        return

    log.info("[SESSION-WORKER] Camera open, streaming period=%s", period)
    stall_n = 0

    while _SESSION_STATE.get("running"):
        grabbed = cap.grab()
        if grabbed:
            ret, frame = cap.retrieve()
        else:
            ret, frame = False, None

        if not ret or frame is None or frame.size == 0:
            stall_n += 1
            if stall_n > 60:
                log.warning("[SESSION-WORKER] Camera stalled, reconnecting")
                cap.release()
                time.sleep(1.0)
                cap = _open_camera(config.CAMERA_INDEX)
                stall_n = 0
                if cap is None:
                    _SESSION_STATE["error"] = "Camera lost during session."
                    _SESSION_STATE["running"] = False
                    break
            time.sleep(0.04)
            continue

        stall_n = 0

        # Run face recognition + draw overlays
        try:
            out, _results = rec.process_frame(
                frame, period, cam="CAM1", draw=True)
        except Exception as exc:
            log.debug("[SESSION-WORKER] process_frame error: %s", exc)
            out = frame     # show raw frame on error

        # Encode to JPEG and push to queue (drop oldest if full)
        try:
            ok, buf = cv2.imencode(
                ".jpg", out, [cv2.IMWRITE_JPEG_QUALITY, 75])
            if ok:
                jpeg = buf.tobytes()
                if _FRAME_QUEUE.full():
                    try:
                        _FRAME_QUEUE.get_nowait()
                    except queue.Empty:
                        pass
                try:
                    _FRAME_QUEUE.put_nowait(jpeg)
                except queue.Full:
                    pass
        except Exception as exc:
            log.debug("[SESSION-WORKER] JPEG encode error: %s", exc)

    # ── cleanup ──────────────────────────────────────────────
    cap.release()
    _SESSION_STATE["running"] = False
    _SESSION_STATE["thread"]  = None
    log.info("[SESSION-WORKER] Thread exited for period=%s", period)
    try:
        db.log_audit("system", "session_end_thread", period)
    except Exception:
        pass


# =============================================================
# PUBLIC API INTERFACE
# All three functions below are called directly by api.py.
# =============================================================

def start_session(period: str) -> dict:
    """
    Launch the headless recognition thread.
    Called by api.py  POST /session/start

    Returns {"ok": True} on success or
            {"ok": False, "error": "<reason>"} on failure.
    """
    global _SESSION_STATE

    # If previous thread died without clean stop, reset state
    t = _SESSION_STATE.get("thread")
    if t is not None and not t.is_alive():
        _SESSION_STATE["running"] = False
        _SESSION_STATE["thread"]  = None

    if _SESSION_STATE.get("running"):
        return {"ok": False, "error": "Session already running."}

    # Drain any frames left over from a previous session
    while not _FRAME_QUEUE.empty():
        try:
            _FRAME_QUEUE.get_nowait()
        except queue.Empty:
            break

    # Reset state before starting thread
    _SESSION_STATE.update({
        "running":    True,
        "period":     period,
        "started_at": datetime.datetime.now().isoformat(),
        "error":      None,
        "thread":     None,
    })

    t = threading.Thread(
        target=_session_worker,
        args=(period,),
        daemon=True,
        name=f"SessionWorker-{period}",
    )
    _SESSION_STATE["thread"] = t
    t.start()

    log.info("[SESSION] Thread started for period=%s", period)
    return {"ok": True}


def stop_session() -> None:
    """
    Signal the recognition thread to stop and wait up to 4 s.
    Called by api.py  POST /session/stop
    """
    _SESSION_STATE["running"] = False
    t = _SESSION_STATE.get("thread")
    if t is not None and t.is_alive():
        t.join(timeout=4.0)
    _SESSION_STATE["thread"] = None
    log.info("[SESSION] Stopped.")


def get_status() -> dict:
    """
    Return a safe copy of _SESSION_STATE for the status endpoint.
    Called by api.py  GET /session/status

    Also auto-detects if the worker thread exited unexpectedly.
    """
    # Auto-detect dead thread
    t = _SESSION_STATE.get("thread")
    if (t is not None
            and not t.is_alive()
            and _SESSION_STATE.get("running")):
        _SESSION_STATE["running"] = False
        _SESSION_STATE["thread"]  = None
        if not _SESSION_STATE.get("error"):
            _SESSION_STATE["error"] = (
                "Session thread stopped unexpectedly."
            )

    return {
        "running":    _SESSION_STATE.get("running", False),
        "period":     _SESSION_STATE.get("period"),
        "started_at": _SESSION_STATE.get("started_at"),
        "error":      _SESSION_STATE.get("error"),
    }


def generate_frames():
    """
    MJPEG multipart generator for api.py  GET /video_feed.

    When session is idle  → yields the static offline JPEG once/sec.
    When session is live  → yields annotated webcam JPEG frames.

    NOTE: This generator intentionally continues streaming even when the
    session is idle so the browser <img> stays connected.  The frontend
    (renderSessionStatus) is responsible for hiding the <img> element
    when it detects running=False — the stream itself is never closed
    from this side except when the server shuts down.
    """
    boundary = b"frame"
    last_running = False   # track transitions so we don't flood the log

    while True:
        running = bool(_SESSION_STATE.get("running"))

        if not running:
            # No active session — serve the offline placeholder frame.
            # Yield once, then sleep so we don't busy-spin.
            if last_running:
                log.info("[FRAMES] Session ended — serving offline placeholder")
            last_running = False
            yield (
                b"--" + boundary + b"\r\n"
                b"Content-Type: image/jpeg\r\n\r\n"
                + _OFFLINE_JPEG
                + b"\r\n"
            )
            time.sleep(1.0)
            continue

        if not last_running:
            log.info("[FRAMES] Session started — streaming live frames")
        last_running = True

        try:
            jpeg = _FRAME_QUEUE.get(timeout=2.0)
        except queue.Empty:
            # Session running but no frame yet (camera warming up) — use placeholder
            jpeg = _OFFLINE_JPEG

        yield (
            b"--" + boundary + b"\r\n"
            b"Content-Type: image/jpeg\r\n\r\n"
            + jpeg
            + b"\r\n"
        )


# =============================================================
# CLI MODE — blocking session with cv2 window
# Called by main.py menu option [3].
# =============================================================
def run_session(period: str = None) -> None:
    """
    Blocking camera loop with an OpenCV window.
    Press Q or ESC to quit.
    """
    global _stop_requested
    _stop_requested = False

    try:
        import cv2
    except ImportError:
        print("  ERROR: OpenCV (cv2) is not installed.")
        print("  Run:   pip install opencv-contrib-python")
        return

    from recognizer1 import SmartRecognizer

    if not period:
        period = _auto_period()

    print(f"\n[SESSION] Starting: {period}")
    db.log_audit("system", "session_start", period)

    rec = SmartRecognizer()

    print("  Opening camera...")
    cap = _open_camera(config.CAMERA_INDEX)
    if cap is None:
        print("  ERROR: Cannot open camera.")
        print("  Solutions:")
        print("    - Close Teams, Zoom, or any app using the camera")
        print("    - Try: CAMERA_INDEX=1 in .env")
        print("    - Unplug and replug the webcam")
        return

    cv2.namedWindow("Attendance", cv2.WINDOW_NORMAL)
    cv2.resizeWindow("Attendance", 900, 560)

    print("[SESSION] Running.  Keys: Q/ESC=quit  S=stats  D=debug  T=twin")

    stall_n = 0
    while not _stop_requested:
        grabbed = cap.grab()
        if grabbed:
            ret, frame = cap.retrieve()
        else:
            ret, frame = False, None

        if not ret or frame is None or frame.size == 0:
            stall_n += 1
            if stall_n > 50:
                print("  Camera stalled. Reconnecting...")
                cap.release()
                time.sleep(1.0)
                cap = _open_camera(config.CAMERA_INDEX)
                stall_n = 0
                if cap is None:
                    print("  Camera lost. Session ended.")
                    break
            time.sleep(0.04)
            continue
        stall_n = 0

        out, _results = rec.process_frame(
            frame, period, cam="CAM1", draw=True)
        cv2.putText(out, f"Period: {period}  [Q=quit  D=debug]",
                    (10, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.6,
                    (255, 220, 0), 2)
        cv2.imshow("Attendance", out)

        key = cv2.waitKey(1) & 0xFF
        if key in (ord('q'), ord('Q'), 27):
            _stop_requested = True
        elif key in (ord('s'), ord('S')):
            rows = db.get_period_stats()
            print("\n--- Period Stats ---")
            for r in rows:
                print(f"  {r.get('period','?'):<15} "
                      f"{r.get('count', 0)} present")
        elif key in (ord('d'), ord('D')):
            import recognizer1 as _rm
            _rm.DEBUG = not _rm.DEBUG
            print(f"  Debug: {'ON' if _rm.DEBUG else 'OFF'}")
        elif key in (ord('t'), ord('T')):
            rows = db.get_twin_analysis_log(days=1)
            print("\n--- Twin Log (today) ---")
            if not rows:
                print("  No twin events today.")
            for r in rows[:10]:
                print(f"  {r.get('student_name','?'):20} "
                      f"{r.get('decision','?'):10} "
                      f"{float(r.get('final_confidence', 0)) * 100:.0f}%")

    cap.release()
    cv2.destroyAllWindows()
    for _ in range(10):
        cv2.waitKey(1)

    log.info("Session ended: %s", period)
    db.log_audit("system", "session_end", period)
    print(f"\n[SESSION] {period} ended.")
    _print_today(period)