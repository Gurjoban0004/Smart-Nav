# ═══════════════════════════════════════════════════════════════════════════════
#  MAC NAVIGATION BRAIN  —  mac_nav.py
# ═══════════════════════════════════════════════════════════════════════════════

import json
import socket
import wave
import os
import sys
import time
import threading
import asyncio
import difflib
import re
import struct
import math
import shutil
import subprocess
import queue
import signal
import atexit
import tempfile
import hashlib
from enum import Enum

from nav_protocol import (
    PALETTE, STATUS_DISPLAY, pc,
    AUDIO_PORT, STATUS_PORT, CALLBACK_PORT, LISTEN_PORT,
    MAX_AUDIO_BYTES, STATUS_READY, STATUS_RECEIVING, STATUS_PROCESSING,
    STATUS_NAVIGATING, STATUS_REPLAYING, STATUS_NOT_FOUND, STATUS_HELP,
    STATUS_WAITING, STATUS_AUTO_NAVIGATE, STATUS_CANCELLED, STATUS_OFFLINE,
    CB_PLAYBACK_DONE, CB_HEARTBEAT,
)

import whisper
import edge_tts

# ─── CONFIG ───────────────────────────────────────────────────────────────────

_SCRIPT_DIR     = os.path.dirname(os.path.abspath(__file__))
ROUTES_FILE     = os.path.join(_SCRIPT_DIR, "routes_v2.json")
PI_IP           = "192.168.245.8"
PI_AUDIO_PORT   = AUDIO_PORT
PI_STATUS_PORT  = STATUS_PORT
SAMPLE_RATE     = 16000
MIN_AUDIO_BYTES = 8000

TTS_VOICE       = "en-US-AriaNeural"
TTS_SLOW_RATE   = "-20%"
TTS_NORM_RATE   = "+0%"

MODE_WAIT_SEC   = 20     # auto-full after 20s of no input

_TMP_DIR        = tempfile.mkdtemp(prefix="nav_")
TTS_MP3         = os.path.join(_TMP_DIR, "nav_tts.mp3")
MERGED_MP3      = os.path.join(_TMP_DIR, "nav_merged.mp3")
CACHE_DIR       = os.path.join(_SCRIPT_DIR, "tts_cache")
os.makedirs(CACHE_DIR, exist_ok=True)
CHIME_START_MP3 = "chime_start_v2.mp3"
CHIME_END_MP3   = "chime_end_v2.mp3"

# ─── ENUMS ────────────────────────────────────────────────────────────────────

class AppStatus(Enum):
    READY         = "READY"
    RECEIVING     = "RECEIVING"
    PROCESSING    = "PROCESSING"
    NAVIGATING    = "NAVIGATING"
    REPLAYING     = "REPLAYING"
    NOT_FOUND     = "NOT FOUND"
    HELP          = "HELP"
    WAITING       = "WAITING"
    AUTO_NAVIGATE = "AUTO-NAVIGATE"
    CANCELLED     = "CANCELLED"

# ─── STATE ────────────────────────────────────────────────────────────────────

app = {
    "status":           AppStatus.READY,
    "destination":      "—",
    "distance":         "—",
    "eta":              "—",
    "mode":             "—",
    "speed":            "NORMAL",
    "requests":         0,
    "last_event":       "System started",
    "last_heard":       "—",
    "speaking":         False,
    "last_route":       None,
    "slow_mode":        False,
    "awaiting_mode":    False,
    "mode_wait_start":  0.0,
}

app_lock  = threading.Lock()
boot_time = time.time()

# ─── INCOMING AUDIO QUEUE ─────────────────────────────────────────────────────
# ESP button presses arrive as TCP connections.
# A receiver thread accepts them and puts raw audio bytes into this queue.
# The main processing thread reads from this queue.
# This way we NEVER miss a button press even if speak() is blocking.

incoming_queue = queue.Queue(maxsize=5)

# ─── FLAG TO INTERRUPT SPEECH ─────────────────────────────────────────────────
# When user presses button while system is speaking, we want to:
# 1. Stop Pi audio immediately
# 2. Stop waiting in speak()
# 3. Process the new command

interrupt_flag = threading.Event()

# ─── PLAYBACK CONFIRMATION FROM PI ────────────────────────────────────────────
playback_done = threading.Event()
last_pi_heartbeat = time.time()
_pi_heartbeat_lock = threading.Lock()
_welcome_spoken = False
_welcome_lock = threading.Lock()
mac_playback_id = 0
active_playback_id = "0"

# ─── PERSISTENT EVENT LOOP FOR TTS ───────────────────────────────────────────

_tts_loop = asyncio.new_event_loop()
threading.Thread(target=_tts_loop.run_forever, daemon=True, name='tts-loop').start()

# ─── PASTEL ANSI (from shared protocol) ───────────────────────────────────────

P = PALETTE

def strip_ansi(t):
    return re.sub(r'\033\[[0-9;]*m', '', t)

def term_cols():
    try:    return shutil.get_terminal_size().columns
    except: return 80

# ─── MAC CONSOLE ──────────────────────────────────────────────────────────────

_last_panel = 0

def mac_print(lines_list):
    global _last_panel
    if _last_panel > 0:
        sys.stdout.write(f"\033[{_last_panel}A\033[J")
    sys.stdout.write("\n".join(lines_list) + "\n")
    sys.stdout.flush()
    _last_panel = len(lines_list) + 1


def render_panel():
    with app_lock:
        a = dict(app)

    W = min(term_cols() - 2, 88)
    sym, col, label = STATUS_DISPLAY.get(a["status"].value, ("○", "STEEL", "?"))

    up = int(time.time() - boot_time)
    up_s = f"{up//3600:02d}:{(up%3600)//60:02d}:{up%60:02d}"
    spk = pc("MINT", "♫ Speaking") if a["speaking"] else pc("DKGREY", "  Silent")
    spd = pc("PEACH", a["speed"]) if a["slow_mode"] else pc("CREAM", a["speed"])
    mode = pc("MINT", a["mode"]) if a["mode"] not in ("—", "") else pc("DKGREY", "—")

    panel = [
        pc("BDR", "─" * W),
        pc("BLD", "") + pc("LAVEN", "  ✦ SMART NAV · MAC BRAIN") + pc("DKGREY", f"   Up:{up_s}  Req:{a['requests']}"),
        pc("BDR", "─" * W),
        f"  {pc(col, sym+' '+label):<28}  {spk}",
        f"  {pc('GREY','Dest:')} {pc('CREAM', str(a['destination'])[:22])}  {pc('GREY','Dist:')} {pc('FROST', str(a['distance'])[:8])}  {pc('GREY','ETA:')} {pc('PEACH', str(a['eta'])[:12])}",
        f"  {pc('GREY','Mode:')} {mode:<18}  {pc('GREY','Speed:')} {spd}",
        pc("BDR", "─" * W),
        f"  {pc('GREY','Event:')} {pc('PEACH', str(a['last_event'])[:W-10])}",
        f"  {pc('GREY','Heard:')} {pc('SKY', str(a['last_heard'])[:W-10])}",
        pc("BDR", "─" * W),
    ]
    mac_print(panel)


def log(msg, color="CREAM"):
    ts = time.strftime("%H:%M:%S")
    line = pc("DKGREY", f"  [{ts}]") + " " + pc(color, msg)
    print(line)
    sys.stdout.flush()
    try:
        clean = strip_ansi(msg)
        with open("/tmp/mac_nav.log", "a") as f:
            f.write(f"[{ts}] [{color}] {clean}\n")
    except:
        pass

# ─── BOOT ─────────────────────────────────────────────────────────────────────

print()
print(pc("BDR", "═" * 55))
print(pc("BLD", "") + pc("LAVEN", "  ✦ SMART NAVIGATION SYSTEM · MAC COMPUTE"))
print(pc("BDR", "═" * 55))
print()

log("Loading Whisper model (base)…", "GREY")
model = whisper.load_model("base")
log("Whisper loaded ✓", "OK")

log(f"Loading {ROUTES_FILE}…", "GREY")
with open(ROUTES_FILE, "r") as f:
    ROUTES_DATA = json.load(f)
log(f"{len(ROUTES_DATA['routes'])} routes ✓", "OK")

log(f"Listener on port {LISTEN_PORT}…", "GREY")
listen_sock = socket.socket()
listen_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
listen_sock.bind(("", LISTEN_PORT))
listen_sock.listen(10)

def _shutdown(*_):
    log("Shutting down…", "WARN")
    try: push_status(status="OFFLINE", last_event="Server offline")
    except: pass
    try: listen_sock.close()
    except: pass
    for f in [TTS_MP3, MERGED_MP3]:
        try: os.remove(f)
        except: pass
    sys.exit(0)

atexit.register(lambda: push_status(status="OFFLINE", last_event="Server offline") if True else None)
signal.signal(signal.SIGTERM, _shutdown)
signal.signal(signal.SIGINT, _shutdown)

log("Ready ✓", "OK")
log(f"Pi → {PI_IP}", "PEACH")

HAS_FFMPEG = shutil.which("ffmpeg") is not None
if not HAS_FFMPEG:
    log("ffmpeg not found — chimes disabled", "WARN")

# ─── CHIMES ───────────────────────────────────────────────────────────────────

def _gen_wav(path, freq_list, dur_ms, vol=0.35):
    sr = 44100
    n = int(sr * dur_ms / 1000)
    fade_in = int(sr * 0.02)
    samples = []
    if not isinstance(freq_list, list):
        freq_list = [freq_list]
    for i in range(n):
        env = math.exp(-5.0 * i / n)
        if i < fade_in:
            env *= (i / fade_in)
        val = 0.0
        for f in freq_list:
            val += math.sin(2 * math.pi * f * i / sr)
        val = val / len(freq_list)
        samples.append(int(vol * env * val * 32767))
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(sr)
        wf.writeframes(struct.pack(f"<{len(samples)}h", *samples))

def _make_chime(mp3, freq, dur_ms):
    if os.path.exists(mp3) and os.path.getsize(mp3) > 200:
        return
    wav = mp3.replace(".mp3", ".wav")
    _gen_wav(wav, freq, dur_ms)
    if HAS_FFMPEG:
        subprocess.call(
            ["ffmpeg", "-y", "-loglevel", "quiet", "-i", wav, "-b:a", "192k", mp3],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        if os.path.exists(mp3) and os.path.getsize(mp3) > 200:
            os.remove(wav)
            return
    if os.path.exists(wav):
        shutil.copy2(wav, mp3)
        os.remove(wav)

_make_chime(CHIME_START_MP3, [880, 1100], 400)
_make_chime(CHIME_END_MP3, [660, 825], 350)
log("Chimes ready ✓", "OK")
print()
render_panel()

# ─── ROUTE HELPERS ────────────────────────────────────────────────────────────

def clean_text(t):
    return re.sub(r"[.,?!'\"\-]+", "", t.lower()).strip()

def normalize_query(text):
    t = clean_text(text)
    for p in [
        "take me to", "guide me to", "where is", "go to",
        "navigate to", "i want to go to", "can you take me to",
        "please take me to", "directions to", "how do i get to",
        "show me", "find",
    ]:
        t = t.replace(p, "")
    return t.strip()

def find_route(speech):
    query = normalize_query(speech)
    best, score = None, 0.0
    for r in ROUTES_DATA["routes"]:
        for name in [r["destination"]] + r.get("aliases", []):
            s = difflib.SequenceMatcher(None, query, clean_text(name)).ratio()
            if s > score:
                score, best = s, r
    return best if score >= 0.55 else None

def route_step_list(route):
    out = []
    for step in route.get("steps", []):
        line = step["instruction"]
        if "distance" in step: line += f" ({step['distance']}m)"
        if "landmark" in step: line += f" near {step['landmark']}"
        out.append(line)
    return out

def build_speech(route, mode):
    dest  = route["destination"]
    dist  = route["distance_meters"]
    eta   = route["estimated_walking_time"]
    steps = route.get("steps", [])
    parts = [f"Navigating to {dest}."]

    if mode == "short":
        parts.append(f"Distance: {dist} meters.")
        parts.append(f"Estimated walking time: {eta}.")
        parts.append("You have reached your destination.")
        return " ".join(parts)

    if mode == "fast":
        for i, s in enumerate(steps):
            parts.append(f"{'First' if i==0 else 'Then'}, {s['instruction']}.")
        parts.append(f"Estimated time: {eta}.")
        return " ".join(parts)

    labels = ["First", "Next", "Then", "After that"]
    for i, s in enumerate(steps):
        pfx = labels[min(i, len(labels)-1)]
        d = f" for {s['distance']} meters" if "distance" in s else ""
        l = f", near {s['landmark']}" if "landmark" in s else ""
        parts.append(f"{pfx}, {s['instruction']}{d}{l}.")
    parts.append(f"Approximate walking time: {eta}.")
    parts.append("You have reached your destination.")
    return " ".join(parts)

def list_destinations():
    return ", ".join(r["destination"] for r in ROUTES_DATA["routes"])

# ─── PI COMMUNICATION ─────────────────────────────────────────────────────────

def _tcp_send(ip, port, data, timeout=10, retries=2):
    for attempt in range(retries):
        s = socket.socket()
        try:
            s.settimeout(timeout)
            s.connect((ip, port))
            s.sendall(data)
            return True
        except Exception:
            if attempt < retries - 1: time.sleep(0.3)
        finally:
            s.close()
    return False

_status_queue = queue.Queue()

def push_status(**kw):
    """Queue status updates for batched sending to Pi."""
    _status_queue.put(kw)

def _status_sender_thread():
    """Dedicated thread: batches queued status updates into single TCP sends."""
    while True:
        try:
            batch = _status_queue.get()  # block for first item
            # Drain any additional queued items (batch them)
            while not _status_queue.empty():
                try:
                    batch.update(_status_queue.get_nowait())
                except queue.Empty:
                    break
            payload = "\n".join(f"{k}={v}" for k, v in batch.items()) + "\n"
            _tcp_send(PI_IP, PI_STATUS_PORT, payload.encode())
        except Exception as e:
            log(f"Status sender error: {e}", "ERR")
            time.sleep(0.5)

def push_steps(route):
    steps = route_step_list(route)
    push_status(steps="|".join(steps), step_index="-1")

def stop_pi_audio():
    _tcp_send(PI_IP, PI_STATUS_PORT, b"command=STOP_AUDIO\n")

def send_audio_to_pi(filepath):
    if not os.path.exists(filepath): return False
    with _pi_heartbeat_lock:
        pi_age = time.time() - last_pi_heartbeat
    if pi_age > 30:
        log(f"⚠ Pi heartbeat stale ({pi_age:.0f}s ago)", "WARN")
    size = os.path.getsize(filepath)
    if size < 100: return False
    with open(filepath, "rb") as f:
        data = f.read()
    log(f"Sending {size:,}B → Pi", "GREY")
    ok = _tcp_send(PI_IP, PI_AUDIO_PORT, data, timeout=30, retries=2)
    if ok: log("Sent ✓", "OK")
    else:  log("Send FAILED", "ERR")
    return ok

# ─── SECURE CLOUD BROKER SYNC ─────────────────────────────────────────────────

_JSONBLOB_URL = "https://jsonblob.com/api/jsonBlob/019e5a61-300f-76df-88d2-658f90a89439"

def _cloud_sync_worker(route, mode, status, speaking):
    try:
        import urllib.request
        payload = {
            "route": route,
            "mode": mode,
            "status": status,
            "speaking": speaking
        }
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            _JSONBLOB_URL,
            data=data,
            headers={"Content-Type": "application/json"},
            method="PUT"
        )
        with urllib.request.urlopen(req, timeout=5) as response:
            response.read()
    except Exception as e:
        log(f"Cloud sync failed: {e}", "WARN")

def sync_route_to_cloud():
    """Reads current route state from app and posts to cloud in a background thread."""
    with app_lock:
        route = app["last_route"]
        mode = app["mode"]
        status = app["status"].value
        speaking = "YES" if app["speaking"] else "NO"
    
    threading.Thread(
        target=_cloud_sync_worker,
        args=(route, mode, status, speaking),
        daemon=True
    ).start()


# ─── TTS + MERGE ──────────────────────────────────────────────────────────────

async def _tts_gen(text, path, slow=False):
    rate = TTS_SLOW_RATE if slow else TTS_NORM_RATE
    c = edge_tts.Communicate(text, TTS_VOICE, rate=rate)
    await c.save(path)

_tts_fail_count = 0
_MAX_TTS_FAILS  = 3

def generate_audio(text, slow=False):
    """Generate TTS, merge with chimes into single file. Returns path or None."""
    global _tts_fail_count

    # Check cache first
    h = hashlib.md5(f"{text}||{slow}".encode("utf-8")).hexdigest()
    cached_path = os.path.join(CACHE_DIR, f"{h}.mp3")

    if os.path.exists(cached_path) and os.path.getsize(cached_path) > 500:
        try:
            shutil.copy2(cached_path, TTS_MP3)
            log(f"TTS Cache hit ✓", "OK")
        except Exception as e:
            log(f"Cache copy failed: {e}", "ERR")
            return cached_path
    else:
        if _tts_fail_count >= _MAX_TTS_FAILS:
            log(f"TTS offline ({_tts_fail_count} consecutive failures) — skipping", "ERR")
            if _tts_fail_count % 10 == 0:
                _tts_fail_count = 0  # retry every 10th call
            else:
                _tts_fail_count += 1
                return None

        for f in [TTS_MP3, MERGED_MP3]:
            if os.path.exists(f):
                try: os.remove(f)
                except: pass

        try:
            asyncio.run_coroutine_threadsafe(_tts_gen(text, TTS_MP3, slow), _tts_loop).result(timeout=30)
            _tts_fail_count = 0
            # Save to cache
            try:
                shutil.copy2(TTS_MP3, cached_path)
            except Exception as e:
                log(f"Failed to cache TTS: {e}", "ERR")
        except Exception as e:
            _tts_fail_count += 1
            log(f"TTS error ({_tts_fail_count}/{_MAX_TTS_FAILS}): {e}", "ERR")
            return None

    if not os.path.exists(TTS_MP3) or os.path.getsize(TTS_MP3) < 500:
        log("TTS empty output", "ERR")
        return None

    log(f"TTS ready: {os.path.getsize(TTS_MP3):,}B", "OK")

    if not HAS_FFMPEG:
        return TTS_MP3

    has_start = os.path.exists(CHIME_START_MP3) and os.path.getsize(CHIME_START_MP3) > 200
    has_end   = os.path.exists(CHIME_END_MP3) and os.path.getsize(CHIME_END_MP3) > 200

    inputs = []
    streams = []
    idx = 0

    if has_start:
        inputs += ["-i", CHIME_START_MP3]
        streams.append(f"[{idx}:a]aresample=44100[cs];")
        idx += 1

    inputs += ["-i", TTS_MP3]
    streams.append(f"[{idx}:a]aresample=44100[sp];")
    idx += 1

    if has_end:
        inputs += ["-i", CHIME_END_MP3]
        streams.append(f"[{idx}:a]aresample=44100[ce];")
        idx += 1

    # build concat
    concat_in = ""
    n = 0
    if has_start:
        streams.append("aevalsrc=0:d=0.25[g1];")
        concat_in += "[cs][g1]"
        n += 2
    concat_in += "[sp]"
    n += 1
    if has_end:
        streams.append("aevalsrc=0:d=0.25[g2];")
        concat_in += "[g2][ce]"
        n += 2

    filter_str = "".join(streams) + f"{concat_in}concat=n={n}:v=0:a=1[out]"

    cmd = ["ffmpeg", "-y"] + inputs + [
        "-filter_complex", filter_str,
        "-map", "[out]", "-b:a", "192k",
        "-loglevel", "quiet", MERGED_MP3,
    ]

    ret = subprocess.call(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if ret == 0 and os.path.exists(MERGED_MP3) and os.path.getsize(MERGED_MP3) > 500:
        log(f"Merged: {os.path.getsize(MERGED_MP3):,}B ✓", "OK")
        return MERGED_MP3

    log("Merge failed, using TTS only", "WARN")
    return TTS_MP3

def get_duration(filepath):
    if shutil.which("ffprobe"):
        try:
            r = subprocess.run(
                ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", filepath],
                capture_output=True, text=True, timeout=5,
            )
            return float(r.stdout.strip())
        except: pass
    return max(1.0, os.path.getsize(filepath) / 24000)  # ~192kbps MP3

# ─── SPEAK ────────────────────────────────────────────────────────────────────

speak_lock = threading.Lock()

def speak(text, status_kw=None, slow=None, min_play_sec=0.0):
    """
    Generate merged audio -> send single file to Pi -> wait for playback.
    Can be interrupted: if interrupt_flag is set, we stop Pi audio and return.
    min_play_sec: minimum seconds before allowing interrupt (for short confirmations).
    """
    with speak_lock:
        if interrupt_flag.is_set():
            log("Aborting speak() because interrupt is already set", "WARN")
            return
        interrupt_flag.clear()

        with app_lock:
            use_slow = app["slow_mode"] if slow is None else slow
            app["speaking"] = True

        global mac_playback_id, active_playback_id
        mac_playback_id += 1
        active_playback_id = str(mac_playback_id)

        pi_kw = {"speaking": "YES", "playback_id": active_playback_id}
        if status_kw:
            with app_lock:
                for k, v in status_kw.items():
                    if k == "status" and isinstance(v, AppStatus):
                        app["status"] = v
                        pi_kw[k] = v.value
                    elif k in app:
                        app[k] = v
                        pi_kw[k] = str(v)
            push_status(**pi_kw)
            render_panel()
        else:
            push_status(speaking="YES", playback_id=active_playback_id)

        stop_pi_audio()
        time.sleep(0.1)

        # Check interrupt before TTS generation
        if interrupt_flag.is_set():
            log("Interrupted before TTS", "WARN")
            with app_lock: app["speaking"] = False
            push_status(speaking="NO")
            render_panel()
            return

        preview = text[:80] + ("…" if len(text) > 80 else "")
        log(f'TTS: "{preview}"', "GREY")

        audio_path = generate_audio(text, slow=use_slow)

        # Check interrupt after TTS generation
        if interrupt_flag.is_set():
            log("Interrupted after TTS", "WARN")
            with app_lock: app["speaking"] = False
            push_status(speaking="NO")
            render_panel()
            return

        if not audio_path:
            log("TTS failed", "ERR")
            with app_lock: app["speaking"] = False
            push_status(speaking="NO")
            render_panel()
            return

        duration = get_duration(audio_path)
        log(f"Duration: {duration:.1f}s", "GREY")

        ok = send_audio_to_pi(audio_path)

        if ok:
            # Wait for playback: use Pi callback if available, else timer
            playback_done.clear()
            waited = 0.0
            deadline = duration + 2.0  # safety margin
            while waited < deadline:
                if interrupt_flag.is_set() and waited >= min_play_sec:
                    log("Interrupted by new input", "WARN")
                    stop_pi_audio()
                    break
                if playback_done.wait(timeout=0.25):
                    log("Pi confirmed playback done", "OK")
                    break
                waited += 0.25

        with app_lock: app["speaking"] = False
        push_status(speaking="NO")
        render_panel()

# ─── RECEIVER THREAD ──────────────────────────────────────────────────────────

def receiver_thread():
    """
    Accepts ESP connections in a dedicated thread.
    Puts received audio bytes into incoming_queue.
    If system is speaking, sets interrupt_flag to cut speech short.
    """
    while True:
        try:
            listen_sock.settimeout(None)  # blocking accept
            conn, addr = listen_sock.accept()

            with app_lock:
                app["requests"] += 1
                req_num = app["requests"]
                app["status"]     = AppStatus.RECEIVING
                app["last_event"] = f"Button #{req_num}"
                is_speaking = app["speaking"]

            render_panel()
            push_status(status="RECEIVING", requests=str(req_num),
                        last_event=f"Button #{req_num}")
            log(f"Button #{req_num} from {addr[0]}", "FROST")

            # Always interrupt current speech/navigation on new button press
            log("Interrupting for new request…", "WARN")
            interrupt_flag.set()
            stop_pi_audio()

            # Receive audio
            raw = b""
            try:
                conn.settimeout(15)
                while len(raw) < MAX_AUDIO_BYTES:
                    chunk = conn.recv(8192)
                    if not chunk: break
                    raw += chunk
            except Exception:
                pass
            finally:
                conn.close()

            log(f"Got {len(raw):,} bytes", "GREY")

            # Put into queue (drop oldest if full)
            if incoming_queue.full():
                try:
                    incoming_queue.get_nowait()
                    log("Queue full — dropped oldest request", "WARN")
                except: pass
            incoming_queue.put(raw)

        except Exception as e:
            log(f"Receiver error: {e}", "ERR")
            time.sleep(0.5)

# ─── COMMAND DETECTION ────────────────────────────────────────────────────────

# Commands are checked FIRST before destination matching.
# This prevents "short" being interpreted as a destination.

COMMAND_PATTERNS = [
    ("cancel",        r'\b(cancel(l?ed|ling)?|cancle|counsel|council|stop|quit|end|exit|never\s*mind|cant\s*sell(\s*it)?|can\s*sell(\s*it)?|cancel\s*it|clear\s*route)\b'),
    ("help",          r'\b(help|commands|options|what\s*can\s*i\s*say)\b'),
    ("repeat",        r'\b(repeat|again|replay|one\s*more\s*time|say\s*again)\b'),
    ("mode_short",    r'\b(short|brief|summary|summarize|overview|short\s*route)\b'),
    ("mode_fast",     r'\b(fast|quick|rapid|fast\s*route)\b'),
    ("mode_full",     r'\b(full|detailed?|complete|step\s*by\s*step|all\s*steps|full\s*route)\b'),
    ("slow",          r'\b(slow(ly|er|est)?|slow\s*down|speak\s*slow(er|ly)?)\b'),
    ("normal",        r'\b(normal|regular|default|reset\s*speed|speed\s*up)\b'),
    ("volume_up",     r'\b(volume\s*up|speak\s*louder|louder|increase\s*volume|up\s*volume)\b'),
    ("volume_down",   r'\b(volume\s*down|speak\s*softer|quieter|lower\s*volume|down\s*volume)\b'),
    ("volume_mute",   r'\b(mute|silent|silence|turn\s*off\s*sound)\b'),
    ("volume_unmute", r'\b(unmute|speak\s*up|sound\s*on|turn\s*on\s*sound)\b'),
    ("volume_max",    r'\b(max(imum)?\s*volume|volume\s*max(imum)?|full\s*volume|highest?\s*volume|loudest)\b'),
]

NAV_PREFIXES = [
    "take me to", "guide me to", "where is", "go to",
    "navigate to", "i want to go to", "can you take me to",
    "please take me to", "directions to", "how do i get to",
    "show me", "find",
]

def detect_command(cmd):
    """Check for commands FIRST, but skip if a navigation prefix is present."""
    if any(cmd.startswith(p) for p in NAV_PREFIXES):
        return "destination"
    for name, pattern in COMMAND_PATTERNS:
        if re.search(pattern, cmd):
            return name
    return "destination"

# ─── HANDLERS ─────────────────────────────────────────────────────────────────

def handle_cancel():
    with app_lock:
        app["status"]        = AppStatus.CANCELLED
        app["destination"]   = "—"
        app["distance"]      = "—"
        app["eta"]           = "—"
        app["mode"]          = "—"
        app["last_route"]    = None
        app["awaiting_mode"] = False
        app["last_event"]    = "Cancelled"
    push_status(status="CANCELLED", destination="—", distance="—",
                eta="—", mode="—", steps="", step_index="-1")
    render_panel()
    log("CANCEL", "CORAL")
    speak("Navigation cancelled. Say a destination whenever you are ready.",
          {"status": AppStatus.READY, "last_event": "Cancelled"}, min_play_sec=1.5)
    with app_lock: app["status"] = AppStatus.READY
    render_panel()
    sync_route_to_cloud()


def run_navigation(route, mode):
    """
    Speaks navigation steps one by one, updating the step_index on the Pi.
    Can be interrupted at any step.
    """
    dest  = route["destination"]
    dist  = route["distance_meters"]
    eta   = route["estimated_walking_time"]
    steps = route.get("steps", [])

    # Ensure step index is reset initially
    push_status(step_index="-1")

    # 1. Intro announcement
    intro_parts = [f"Navigating to {dest}."]
    if mode == "short":
        intro_parts.append(f"Distance: {dist} meters.")
        intro_parts.append(f"Estimated walking time: {eta}.")

    speak(" ".join(intro_parts), {"status": AppStatus.REPLAYING, "last_event": "Starting nav"})

    # Check for interrupt
    if interrupt_flag.is_set() or not incoming_queue.empty():
        log("Navigation aborted (intro)", "WARN")
        return

    # 2. Step-by-step instructions
    if mode in ("full", "fast"):
        labels = ["First", "Next", "Then", "After that"]
        for i, s in enumerate(steps):
            # Highlight current step on Pi
            with app_lock:
                app["last_event"] = f"Step {i+1}/{len(steps)}"
            push_status(step_index=str(i), last_event=f"Step {i+1}/{len(steps)}")
            render_panel()

            # Build step instruction
            if mode == "fast":
                text = f"{'First' if i==0 else 'Then'}, {s['instruction']}."
            else:
                pfx = labels[min(i, len(labels)-1)]
                d = f" for {s['distance']} meters" if "distance" in s else ""
                l = f", near {s['landmark']}" if "landmark" in s else ""
                text = f"{pfx}, {s['instruction']}{d}{l}."

            speak(text)

            # Check for interrupt after each step
            if interrupt_flag.is_set() or not incoming_queue.empty():
                log(f"Navigation aborted at step {i+1}", "WARN")
                return

    # 3. Outro announcement
    push_status(step_index="-1")
    outro_parts = []
    if mode == "short":
        outro_parts.append("You have reached your destination.")
    elif mode == "fast":
        outro_parts.append(f"Estimated walking time: {eta}. You have reached your destination.")
    else:
        outro_parts.append(f"Approximate walking time: {eta}. You have reached your destination.")

    speak(" ".join(outro_parts), {"status": AppStatus.NAVIGATING, "last_event": "Reached dest"})


def handle_repeat():
    with app_lock:
        route = app["last_route"]
        mode  = app["mode"]
    if not route:
        speak("No active route. Please say a destination first.",
              {"status": AppStatus.WAITING, "last_event": "Nothing to repeat"}, min_play_sec=1.5)
        with app_lock: app["status"] = AppStatus.READY
        return
    log(f"REPEAT ({mode})", "SKY")
    run_navigation(route, mode)


def handle_mode(mode):
    with app_lock:
        route = app["last_route"]
        app["mode"] = mode
        app["awaiting_mode"] = False
    if not route:
        speak("No destination set. Please say a destination first.",
              {"status": AppStatus.WAITING, "last_event": "No route"}, min_play_sec=1.5)
        with app_lock: app["status"] = AppStatus.READY
        return
    log(f"MODE → {mode}", "MINT")
    push_status(mode=mode)
    render_panel()
    sync_route_to_cloud()
    run_navigation(route, mode)


def handle_slow():
    with app_lock:
        app["slow_mode"] = True
        app["speed"] = "SLOW"
        app["last_event"] = "Speed → slow"
        am = app["awaiting_mode"]
        lr = app["last_route"]
        mode = app["mode"]
    push_status(speed="SLOW")
    render_panel()
    log("SLOW", "PEACH")
    speak("Speech speed set to slow.", {"last_event": "Slow mode"}, min_play_sec=1.5)
    if am and lr:
        handle_mode("full")
    elif lr and mode in ("full", "fast", "short"):
        run_navigation(lr, mode)


def handle_normal():
    with app_lock:
        app["slow_mode"] = False
        app["speed"] = "NORMAL"
        app["last_event"] = "Speed → normal"
        am = app["awaiting_mode"]
        lr = app["last_route"]
        mode = app["mode"]
    push_status(speed="NORMAL")
    render_panel()
    log("NORMAL", "CREAM")
    speak("Speech speed set to normal.", {"last_event": "Normal speed"}, min_play_sec=1.5)
    if am and lr:
        handle_mode("full")
    elif lr and mode in ("full", "fast", "short"):
        run_navigation(lr, mode)


def handle_volume(action):
    log(f"VOLUME → {action}", "PEACH")
    push_status(command=f"VOL_{action}")
    
    # Speak confirmation
    if action == "UP":
        speak("Volume increased.", {"last_event": "Volume up"}, min_play_sec=1.2)
    elif action == "DOWN":
        speak("Volume decreased.", {"last_event": "Volume down"}, min_play_sec=1.2)
    elif action == "MUTE":
        speak("Volume muted.", {"last_event": "Volume muted"}, min_play_sec=1.2)
    elif action == "UNMUTE":
        speak("Volume restored.", {"last_event": "Volume restored"}, min_play_sec=1.2)
    elif action == "MAX":
        speak("Volume set to maximum.", {"last_event": "Volume max"}, min_play_sec=1.2)


def handle_help():
    log("HELP", "LAVEN")
    speak(
        "Available commands. "
        "Say a place name to navigate. "
        "Say full route for detailed directions. "
        "Say short route for a summary. "
        "Say fast route for a quick version. "
        "Say repeat to hear directions again. "
        "Say slowly for slower speech. "
        "Say normal for normal speed. "
        "Say cancel to stop. "
        "Say help for this message.",
        {"status": AppStatus.HELP, "last_event": "Help"},
    )
    with app_lock: app["status"] = AppStatus.READY
    render_panel()


def handle_destination(speech):
    route = find_route(speech)
    if not route:
        log(f'Not found: "{speech[:35]}"', "CORAL")
        with app_lock:
            app["status"] = AppStatus.NOT_FOUND
            app["last_event"] = f"Unknown: {speech[:25]}"
        render_panel()
        speak(
            f"Sorry, I could not find that location. "
            f"Available destinations are: {list_destinations()}. "
            "Please try again.",
            {"status": AppStatus.NOT_FOUND, "last_event": f"Unknown: {speech[:20]}"},
        )
        with app_lock: app["status"] = AppStatus.READY
        render_panel()
        return

    dest = route["destination"]
    dist = route["distance_meters"]
    eta  = route["estimated_walking_time"]

    log(f"FOUND: {dest} ({dist}m, {eta})", "MINT")

    with app_lock:
        app["last_route"]      = route
        app["mode"]            = "full"
        app["destination"]     = dest
        app["distance"]        = f"{dist}m"
        app["eta"]             = eta
        app["awaiting_mode"]   = True
        app["mode_wait_start"] = time.time()
        app["status"]          = AppStatus.NAVIGATING
        app["last_event"]      = f"Found: {dest}"

    push_steps(route)
    push_status(status="NAVIGATING", destination=dest,
                distance=f"{dist}m", eta=eta, mode="full",
                last_event=f"Found: {dest}")
    render_panel()
    sync_route_to_cloud()

    # Single complete announcement — will NOT be cut
    speak(
        f"Destination found. {dest}. "
        f"It is approximately {dist} meters away. "
        f"Estimated walking time is {eta}. "
        f"Press the button and say full route for step by step directions. "
        f"Or say short route for a summary. "
        f"Or say fast route for a quick version. "
        f"You can also say repeat, slowly, or help at any time.",
        {"status": AppStatus.NAVIGATING, "last_event": f"Announced {dest}"},
    )

    # After announcement, status stays NAVIGATING + awaiting_mode
    with app_lock:
        app["last_event"] = f"Waiting for mode — {dest}"
    push_status(last_event=f"Waiting for mode — {dest}")
    render_panel()


# ─── TRANSCRIBE HELPER ────────────────────────────────────────────────────────

def transcribe(raw_audio):
    """Write raw PCM to wav, run Whisper, return (speech_text, cleaned_cmd)."""
    wav_path = os.path.join(_TMP_DIR, "nav_input.wav")
    with wave.open(wav_path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(raw_audio)

    with app_lock:
        app["status"] = AppStatus.PROCESSING
        app["last_event"] = "Transcribing…"
    push_status(status="PROCESSING", last_event="Transcribing…")
    render_panel()
    log("Transcribing…", "GREY")

    result = model.transcribe(wav_path, language="en")
    speech = result["text"].strip()
    cmd    = clean_text(speech)

    with app_lock:
        app["last_heard"]  = speech
        app["last_event"]  = f"Heard: {speech[:35]}"
    push_status(last_heard=speech[:40], last_event=f"Heard: {speech[:35]}")
    render_panel()
    log(f'Heard: "{speech}"', "CREAM")

    try: os.remove(wav_path)
    except: pass

    return speech, cmd


# ─── MODE WAIT TIMEOUT ────────────────────────────────────────────────────────

def check_mode_timeout():
    with app_lock:
        if not app["awaiting_mode"]:
            return False
        if (time.time() - app["mode_wait_start"]) < MODE_WAIT_SEC:
            return False
        app["awaiting_mode"] = False
        route = app["last_route"]

    if not route:
        return False

    log("Timeout → auto full route", "WARN")
    with app_lock:
        app["status"] = AppStatus.AUTO_NAVIGATE
        app["last_event"] = "Auto: full route"
    push_status(status="AUTO-NAVIGATE", last_event="Auto: full route")
    render_panel()

    run_navigation(route, "full")
    with app_lock:
        app["status"] = AppStatus.NAVIGATING
    render_panel()
    return True


# ─── PI CALLBACK LISTENER ─────────────────────────────────────────────────────

def pi_callback_listener():
    """Listen for Pi -> Mac messages: playback done, heartbeat."""
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("", CALLBACK_PORT))
    srv.listen(10)
    log(f"Pi callback listener on port {CALLBACK_PORT}", "GREY")

    while True:
        try:
            conn, addr = srv.accept()
            conn.settimeout(5)
            data = b""
            try:
                while len(data) < 1024:
                    chunk = conn.recv(1024)
                    if not chunk: break
                    data += chunk
            except Exception:
                pass
            finally:
                conn.close()

            msg = data.decode(errors="ignore").strip()
            if CB_PLAYBACK_DONE in msg:
                parts = msg.split(":")
                p_id = parts[1] if len(parts) > 1 else None
                if p_id:
                    if p_id == active_playback_id:
                        log(f"Callback matches active playback ID {p_id}", "OK")
                        playback_done.set()
                    else:
                        log(f"Callback ignored: obsolete playback ID {p_id} (active {active_playback_id})", "WARN")
                else:
                    playback_done.set()
            if CB_HEARTBEAT in msg:
                global last_pi_heartbeat
                with _pi_heartbeat_lock:
                    last_pi_heartbeat = time.time()
                global _welcome_spoken
                trigger_welcome = False
                with _welcome_lock:
                    if not _welcome_spoken:
                        _welcome_spoken = True
                        trigger_welcome = True
                if trigger_welcome:
                    def _speak_welcome():
                        time.sleep(1.0)
                        speak(
                            "Welcome to Chitkara University. The navigation system is ready. "
                            "Press the button and say a destination to begin.",
                            {"status": AppStatus.READY, "last_event": "Welcome greeting"}
                        )
                    threading.Thread(target=_speak_welcome, daemon=True).start()
        except Exception as e:
            log(f"Callback listener error: {e}", "ERR")
            time.sleep(0.5)


# ─── PERIODIC MAC-TO-PI PING ──────────────────────────────────────────────────

def _mac_ping_thread():
    """Periodically send status directly to Pi so it discovers/retains Mac's IP."""
    time.sleep(5)  # wait for startup
    while True:
        try:
            with app_lock:
                status_val = app["status"].value
                last_evt = app["last_event"]
            payload = f"status={status_val}\nlast_event={last_evt}\nping=1\n"
            _tcp_send(PI_IP, PI_STATUS_PORT, payload.encode(), timeout=2, retries=1)
        except Exception:
            pass
        time.sleep(10)


# ─── EMBEDDED HTTP SERVER ─────────────────────────────────────────────────────

from http.server import HTTPServer, BaseHTTPRequestHandler

class KioskHTTPHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        # Suppress http.server stdout logs to keep TUI panel clean
        pass

    def do_GET(self):
        if self.path == "/api/active-route":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            
            with app_lock:
                route = app["last_route"]
                mode  = app["mode"]
                status = app["status"].value
                speaking = "YES" if app["speaking"] else "NO"
                
            response = {
                "route": route,
                "mode": mode,
                "status": status,
                "speaking": speaking
            }
            self.wfile.write(json.dumps(response).encode("utf-8"))
        elif self.path == "/" or self.path == "/index.html":
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            try:
                html_path = os.path.join(_SCRIPT_DIR, "index.html")
                with open(html_path, "rb") as f:
                    self.wfile.write(f.read())
            except Exception as e:
                self.wfile.write(f"Error loading index.html: {e}".encode("utf-8"))
        else:
            self.send_response(404)
            self.end_headers()

def run_http_server():
    try:
        server = HTTPServer(("0.0.0.0", 8000), KioskHTTPHandler)
        log("HTTP Server running on port 8000...", "OK")
        server.serve_forever()
    except Exception as e:
        log(f"HTTP Server failed: {e}", "ERR")


# ─── PRE-GENERATE CACHE ────────────────────────────────────────────────────────

def pre_generate_cache():
    """Runs in a background thread to pre-generate TTS audio for all static texts."""
    log("Starting background TTS pre-generation...", "GREY")
    
    # 1. Gather all static texts
    texts = set()
    
    # Common system messages
    texts.add("Speech speed set to slow.")
    texts.add("Speech speed set to normal.")
    texts.add("Navigation cancelled. Say a destination whenever you are ready.")
    texts.add("No active route. Please say a destination first.")
    texts.add("No destination set. Please say a destination first.")
    texts.add("I did not hear anything. Please press the button and speak clearly.")
    texts.add("I could not understand that. Please press the button and speak clearly.")
    texts.add("Welcome to Chitkara University. The navigation system is ready. Press the button and say a destination to begin.")
    texts.add("Volume increased.")
    texts.add("Volume decreased.")
    texts.add("Volume muted.")
    texts.add("Volume restored.")
    texts.add(
        "Available commands. "
        "Say a place name to navigate. "
        "Say full route for detailed directions. "
        "Say short route for a summary. "
        "Say fast route for a quick version. "
        "Say repeat to hear directions again. "
        "Say slowly for slower speech. "
        "Say normal for normal speed. "
        "Say cancel to stop. "
        "Say help for this message."
    )
    
    # Route announcements and steps
    for r in ROUTES_DATA["routes"]:
        dest = r["destination"]
        dist = r["distance_meters"]
        eta  = r["estimated_walking_time"]
        steps = r.get("steps", [])
        
        # Intro & Outros
        texts.add(f"Destination found. {dest}. "
                  f"It is approximately {dist} meters away. "
                  f"Estimated walking time is {eta}. "
                  f"Press the button and say full route for step by step directions. "
                  f"Or say short route for a summary. "
                  f"Or say fast route for a quick version. "
                  f"You can also say repeat, slowly, or help at any time.")
        
        texts.add(f"Navigating to {dest}.")
        
        # Short mode intro
        texts.add(f"Navigating to {dest}. Distance: {dist} meters. Estimated walking time: {eta}.")
        
        # Outros
        texts.add("You have reached your destination.")
        texts.add(f"Estimated walking time: {eta}. You have reached your destination.")
        texts.add(f"Approximate walking time: {eta}. You have reached your destination.")
        
        # Steps
        labels = ["First", "Next", "Then", "After that"]
        for i, s in enumerate(steps):
            # Fast mode step
            texts.add(f"{'First' if i==0 else 'Then'}, {s['instruction']}.")
            # Full mode step
            pfx = labels[min(i, len(labels)-1)]
            d = f" for {s['distance']} meters" if "distance" in s else ""
            l = f", near {s['landmark']}" if "landmark" in s else ""
            texts.add(f"{pfx}, {s['instruction']}{d}{l}.")
            
    # Destination not found
    dests_list = ", ".join(r["destination"] for r in ROUTES_DATA["routes"])
    texts.add(f"Sorry, I could not find that location. Available destinations are: {dests_list}. Please try again.")

    # 2. Generate files in background
    count = 0
    for t in texts:
        for slow in (False, True):
            h = hashlib.md5(f"{t}||{slow}".encode("utf-8")).hexdigest()
            cached_path = os.path.join(CACHE_DIR, f"{h}.mp3")
            if not os.path.exists(cached_path):
                try:
                    # Let the TTS loop do it
                    asyncio.run_coroutine_threadsafe(_tts_gen(t, cached_path, slow), _tts_loop).result(timeout=30)
                    count += 1
                    time.sleep(0.05)
                except Exception:
                    pass
                    
    if count > 0:
        log(f"Pre-generated {count} new TTS audio files in background ✓", "OK")
    else:
        log("TTS cache is fully up to date ✓", "OK")


# ─── START THREADS ─────────────────────────────────────────────────────────────

threading.Thread(target=receiver_thread, daemon=True).start()
threading.Thread(target=pi_callback_listener, daemon=True).start()
threading.Thread(target=_status_sender_thread, daemon=True, name='status-sender').start()
threading.Thread(target=pre_generate_cache, daemon=True, name='tts-pregenerator').start()
threading.Thread(target=_mac_ping_thread, daemon=True, name='mac-pinger').start()
threading.Thread(target=run_http_server, daemon=True, name='http-server').start()

# ─── MAIN PROCESSING LOOP ─────────────────────────────────────────────────────

WHISPER_HALLUCINATIONS = {
    "thank you", "thanks for watching", "subscribe",
    "thank you for watching", "please subscribe",
    "like and subscribe", "see you next time",
    "bye", "goodbye", "thanks", "thank you very much",
    "you", "i", "so",
}

# Push initial status to Pi on boot to trigger IP discovery on the Pi
log("Pinging Pi to register Mac IP...", "GREY")
push_status(status="READY", last_event="Server started")
sync_route_to_cloud()

while True:

    # Check mode timeout (only fires if nothing in queue)
    if incoming_queue.empty():
        if check_mode_timeout():
            continue

    # Wait for next button press audio
    try:
        raw = incoming_queue.get(timeout=1.0)
        interrupt_flag.clear()  # Clear the interrupt when starting a new command
    except queue.Empty:
        continue

    # Validate size
    if len(raw) < MIN_AUDIO_BYTES:
        log("Too short — silence?", "WARN")
        speak("I did not hear anything. Please press the button and speak clearly.",
              {"status": AppStatus.WAITING, "last_event": "Silence"}, min_play_sec=1.5)
        with app_lock: app["status"] = AppStatus.READY
        render_panel()
        continue

    # Transcribe
    speech, cmd = transcribe(raw)

    # Validate content
    noise = {"", ".", "...", "uh", "um", "hmm", "huh", "ah", "oh", "the", "a", "you"}
    if len(cmd) < 3 or cmd in noise or cmd in WHISPER_HALLUCINATIONS:
        log("Unclear", "WARN")
        speak("I could not understand that. Please press the button and speak clearly.",
              {"status": AppStatus.WAITING, "last_event": "Unclear"}, min_play_sec=1.5)
        with app_lock: app["status"] = AppStatus.READY
        render_panel()
        continue

    # Detect command (commands checked BEFORE destinations)
    command = detect_command(cmd)
    log(f"Command: {command}", "LAVEN")

    # Dispatch
    if command == "cancel":
        handle_cancel()
    elif command == "repeat":
        handle_repeat()
    elif command == "mode_short":
        handle_mode("short")
    elif command == "mode_fast":
        handle_mode("fast")
    elif command == "mode_full":
        handle_mode("full")
    elif command == "slow":
        handle_slow()
    elif command == "normal":
        handle_normal()
    elif command == "volume_up":
        handle_volume("UP")
    elif command == "volume_down":
        handle_volume("DOWN")
    elif command == "volume_mute":
        handle_volume("MUTE")
    elif command == "volume_unmute":
        handle_volume("UNMUTE")
    elif command == "volume_max":
        handle_volume("MAX")
    elif command == "help":
        handle_help()
    else:
        handle_destination(speech)

    # Final status
    with app_lock:
        if app["status"] not in (
            AppStatus.NAVIGATING, AppStatus.REPLAYING,
            AppStatus.WAITING, AppStatus.CANCELLED,
        ):
            app["status"] = AppStatus.READY
    render_panel()