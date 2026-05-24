# ═══════════════════════════════════════════════════════════════════════════════
#  PI DISPLAY & RECEIVER  —  pi_receiver.py
# ═══════════════════════════════════════════════════════════════════════════════

import socket
import threading
import subprocess
import os
import sys
import time
import shutil
import re
import atexit
import signal
import wave
import struct
import math

from nav_protocol import (
    PALETTE, STATUS_DISPLAY, pc,
    AUDIO_PORT, STATUS_PORT, CALLBACK_PORT,
    MAX_AUDIO_BYTES, CB_PLAYBACK_DONE, CB_HEARTBEAT,
)
P = PALETTE

# ─── CONFIG ───────────────────────────────────────────────────────────────────

AUDIO_FILE       = "/tmp/nav_audio.mp3"
REFRESH_HZ       = 4

# ─── STATE ────────────────────────────────────────────────────────────────────

state = {
    "status":      "READY",
    "destination": "—",
    "distance":    "—",
    "eta":         "—",
    "mode":        "—",
    "speed":       "NORMAL",
    "requests":    "0",
    "last_event":  "System started",
    "last_heard":  "—",
    "speaking":    "NO",
    "step_index":  "-1",
    "playback_id": "-1",
}

route_steps   = []
state_lock     = threading.Lock()
state_changed  = threading.Event()
boot_time      = time.time()

audio_proc    = None
audio_lock    = threading.Lock()
audio_count   = 0
last_audio_ts = None

# ─── MAC IP AUTO-DISCOVERY ────────────────────────────────────────────────────
_mac_ip = None
_mac_ip_lock = threading.Lock()

def set_mac_ip(addr):
    """Remember the Mac's IP from the first incoming connection."""
    global _mac_ip
    with _mac_ip_lock:
        if _mac_ip is None and addr and addr != "127.0.0.1":
            _mac_ip = addr
            pi_log(f"Mac IP discovered: {_mac_ip}")

def get_mac_ip():
    with _mac_ip_lock:
        return _mac_ip

# ─── TERMINAL ─────────────────────────────────────────────────────────────────

def term_size():
    try:
        c = shutil.get_terminal_size().columns
        r = shutil.get_terminal_size().lines
    except Exception:
        c, r = 80, 24
    return max(c, 50), max(r, 16)

def hide_cursor(): sys.stdout.write("\033[?25l"); sys.stdout.flush()
def show_cursor(): sys.stdout.write("\033[?25h"); sys.stdout.flush()
def go_home():     sys.stdout.write("\033[H");    sys.stdout.flush()
def clr():         sys.stdout.write("\033[2J\033[H"); sys.stdout.flush()

atexit.register(show_cursor)

# ─── PASTEL ANSI ──────────────────────────────────────────────────────────────


def strip_ansi(t):
    return re.sub(r'\033\[[0-9;]*m', '', t)

def vlen(t):
    return len(strip_ansi(t))

def pi_log(msg):
    """Debug log to file and stderr so it doesn't interfere with the TUI."""
    ts = time.strftime('%H:%M:%S')
    line = f"[{ts}] {msg}\n"
    sys.stderr.write(line)
    sys.stderr.flush()
    try:
        with open("/tmp/pi_nav.log", "a") as f:
            f.write(line)
    except:
        pass

# ─── AUDIO ────────────────────────────────────────────────────────────────────

def _kill():
    global audio_proc
    if audio_proc and audio_proc.poll() is None:
        try:
            audio_proc.terminate()
            audio_proc.wait(timeout=2)
        except Exception:
            try: audio_proc.kill()
            except Exception: pass
    audio_proc = None

def stop_audio():
    global audio_count
    with audio_lock:
        audio_count += 1
        _kill()

def play_audio(filepath, playback_id):
    global audio_proc, audio_count, last_audio_ts
    with audio_lock:
        audio_count += 1
        my_local_id = audio_count
        with state_lock:
            state["playback_id"] = playback_id
            my_mac_id = playback_id
        _kill()
        if not os.path.exists(filepath) or os.path.getsize(filepath) < 100:
            pi_log(f"play_audio: file {filepath} does not exist or too small")
            return
        pi_log(f"play_audio: starting playback of {filepath} ({os.path.getsize(filepath)} bytes)")
        
        # Check if the file is natively a WAV file
        is_wav = False
        try:
            with open(filepath, "rb") as f:
                header = f.read(12)
                if header.startswith(b"RIFF") and b"WAVE" in header:
                    is_wav = True
        except:
            pass

        if is_wav:
            pi_log("play_audio: file is WAV, playing directly via aplay")
            try:
                audio_proc = subprocess.Popen(
                    ["aplay", filepath], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
                last_audio_ts = time.time()
                # Notify Mac when playback finishes
                _proc_ref = audio_proc
                def _done_monitor_wav(proc=_proc_ref, local_id=my_local_id, mac_id=my_mac_id):
                    pi_log(f"play_audio: wav monitor thread started for PID {proc.pid}")
                    try:
                        code = proc.wait(timeout=120)
                        pi_log(f"play_audio: aplay finished with exit code {code}")
                    except Exception as me:
                        pi_log(f"play_audio: wav monitor wait error: {me}")
                    with audio_lock:
                        is_active_local = (local_id == audio_count)
                    with state_lock:
                        is_active_mac = (mac_id == state.get("playback_id", "-1"))
                    if is_active_local and is_active_mac:
                        pi_log(f"play_audio: sending PLAYBACK_DONE callback for WAV ID {mac_id} to Mac")
                        _send_callback(f"{CB_PLAYBACK_DONE}:{mac_id}")
                    else:
                        pi_log(f"play_audio: obsolete WAV playback local:{local_id} mac:{mac_id}, skipping callback")
                threading.Thread(target=_done_monitor_wav, daemon=True).start()
                return
            except Exception as ae:
                pi_log(f"play_audio: direct aplay launch failed: {ae}")
        players = [
            ["mpg123", "-q", filepath],
            ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", filepath],
            ["mplayer", "-really-quiet", filepath],
        ]
        for cmd in players:
            player_bin = cmd[0]
            bin_path = shutil.which(player_bin)
            pi_log(f"play_audio: checking player {player_bin} -> {bin_path}")
            if bin_path:
                try:
                    pi_log(f"play_audio: launching {cmd}")
                    audio_proc = subprocess.Popen(
                        cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    )
                    last_audio_ts = time.time()
                    # Notify Mac when playback finishes
                    _proc_ref = audio_proc
                    def _done_monitor(proc=_proc_ref, local_id=my_local_id, mac_id=my_mac_id):
                        pi_log(f"play_audio: monitor thread started for PID {proc.pid}")
                        try:
                            code = proc.wait(timeout=120)
                            pi_log(f"play_audio: player finished with exit code {code}")
                        except Exception as me:
                            pi_log(f"play_audio: monitor wait error: {me}")
                        with audio_lock:
                            is_active_local = (local_id == audio_count)
                        with state_lock:
                            is_active_mac = (mac_id == state.get("playback_id", "-1"))
                        if is_active_local and is_active_mac:
                            pi_log(f"play_audio: sending PLAYBACK_DONE callback for ID {mac_id} to Mac")
                            _send_callback(f"{CB_PLAYBACK_DONE}:{mac_id}")
                        else:
                            pi_log(f"play_audio: obsolete playback local:{local_id}/{audio_count} mac:{mac_id}/{state.get('playback_id')}, skipping callback")
                    threading.Thread(target=_done_monitor, daemon=True).start()
                    return
                except Exception as pe:
                    pi_log(f"play_audio: failed to launch {player_bin}: {pe}")
                    continue

        pi_log("play_audio: no mp3 player succeeded, falling back to wav/aplay")
        wav = filepath + ".wav"
        try:
            pi_log("play_audio: converting mp3 to wav via ffmpeg")
            subprocess.call(
                ["ffmpeg", "-y", "-i", filepath, wav],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        except Exception as fe:
            pi_log(f"play_audio: ffmpeg conversion failed: {fe}")

        if os.path.exists(wav):
            try:
                pi_log("play_audio: playing wav via aplay")
                audio_proc = subprocess.Popen(
                    ["aplay", wav], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
                last_audio_ts = time.time()
                # Clean up WAV after playback finishes + notify Mac
                def _cleanup_wav(proc, path, local_id=my_local_id, mac_id=my_mac_id):
                    pi_log(f"play_audio: wav monitor started for PID {proc.pid}")
                    try:
                        code = proc.wait(timeout=120)
                        pi_log(f"play_audio: aplay finished with exit code {code}")
                    except Exception as me:
                        pi_log(f"play_audio: aplay wait error: {me}")
                    try:
                        os.remove(path)
                        pi_log(f"play_audio: deleted temp wav {path}")
                    except Exception as re:
                        pi_log(f"play_audio: failed to delete wav: {re}")
                    with audio_lock:
                        is_active_local = (local_id == audio_count)
                    with state_lock:
                        is_active_mac = (mac_id == state.get("playback_id", "-1"))
                    if is_active_local and is_active_mac:
                        pi_log(f"play_audio: sending PLAYBACK_DONE callback for WAV ID {mac_id} to Mac")
                        _send_callback(f"{CB_PLAYBACK_DONE}:{mac_id}")
                    else:
                        pi_log(f"play_audio: obsolete WAV playback local:{local_id}/{audio_count} mac:{mac_id}/{state.get('playback_id')}, skipping callback")
                threading.Thread(target=_cleanup_wav, args=(audio_proc, wav), daemon=True).start()
                return
            except Exception as ae:
                pi_log(f"play_audio: aplay launch failed: {ae}")
        else:
            pi_log("play_audio: wav file not found after conversion")

def is_playing():
    with audio_lock:
        return audio_proc is not None and audio_proc.poll() is None

def _send_callback(msg):
    """Send a short message to the Mac's callback port."""
    mac = get_mac_ip()
    if not mac:
        return
    try:
        s = socket.socket()
        s.settimeout(3)
        s.connect((mac, CALLBACK_PORT))
        s.sendall((msg + "\n").encode())
    except Exception:
        pass
    finally:
        try: s.close()
        except: pass


def _gen_pulse_wav():
    """Generate a very soft, fast-decay pulse sound for the idle heartbeat."""
    wav_path = "/tmp/pulse.wav"
    if os.path.exists(wav_path):
        return
    sr = 44100
    dur_ms = 150
    n = int(sr * dur_ms / 1000)
    vol = 0.02
    freq = 1200
    samples = []
    for i in range(n):
        env = math.exp(-12.0 * i / n)
        val = math.sin(2 * math.pi * freq * i / sr)
        samples.append(int(vol * env * val * 32767))
    try:
        with wave.open(wav_path, "wb") as wf:
            wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(sr)
            wf.writeframes(struct.pack(f"<{len(samples)}h", *samples))
    except Exception as e:
        pi_log(f"Failed to generate pulse: {e}")


def _play_pulse():
    """Tries playing pulse.wav via Bluetooth-compatible players, falls back to aplay."""
    wav_path = "/tmp/pulse.wav"
    if not os.path.exists(wav_path):
        return
    
    # Try high-level players first to ensure Bluetooth routing
    players = [
        ["paplay", wav_path],
        ["pw-play", wav_path],
        ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", wav_path],
        ["mplayer", "-really-quiet", wav_path],
        ["aplay", "-q", wav_path],
    ]
    for cmd in players:
        if shutil.which(cmd[0]):
            try:
                subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return
            except:
                continue


def _ambient_heartbeat_thread():
    """Plays a soft pulse tone every 15 seconds if the system is idle."""
    _gen_pulse_wav()
    while True:
        time.sleep(15)
        try:
            with state_lock:
                status = state["status"]
                speaking = state["speaking"]
            if status in ("READY", "WAITING") and speaking == "NO" and not is_playing():
                _play_pulse()
        except Exception as e:
            pi_log(f"Ambient heartbeat error: {e}")


def _adjust_volume(action):
    """Adjust Pi audio volume using amixer and pactl as fallback."""
    pi_log(f"Adjusting volume: {action}")
    cmds = []
    if action == "UP":
        cmds = [
            ["pactl", "set-sink-volume", "@DEFAULT_SINK@", "+10%"],
            ["amixer", "-q", "sset", "Master", "10%+"],
            ["amixer", "-q", "sset", "Speaker", "10%+"],
            ["amixer", "-q", "sset", "Headphone", "10%+"]
        ]
    elif action == "DOWN":
        cmds = [
            ["pactl", "set-sink-volume", "@DEFAULT_SINK@", "-10%"],
            ["amixer", "-q", "sset", "Master", "10%-"],
            ["amixer", "-q", "sset", "Speaker", "10%-"],
            ["amixer", "-q", "sset", "Headphone", "10%-"]
        ]
    elif action == "MUTE":
        cmds = [
            ["pactl", "set-sink-mute", "@DEFAULT_SINK@", "1"],
            ["amixer", "-q", "sset", "Master", "mute"],
            ["amixer", "-q", "sset", "Speaker", "mute"],
            ["amixer", "-q", "sset", "Headphone", "mute"]
        ]
    elif action == "UNMUTE":
        cmds = [
            ["pactl", "set-sink-mute", "@DEFAULT_SINK@", "0"],
            ["amixer", "-q", "sset", "Master", "unmute"],
            ["amixer", "-q", "sset", "Speaker", "unmute"],
            ["amixer", "-q", "sset", "Headphone", "unmute"]
        ]
    elif action == "MAX":
        cmds = [
            ["pactl", "set-sink-volume", "@DEFAULT_SINK@", "100%"],
            ["amixer", "-q", "sset", "Master", "100%"],
            ["amixer", "-q", "sset", "Speaker", "100%"],
            ["amixer", "-q", "sset", "Headphone", "100%"]
        ]
    for c in cmds:
        if shutil.which(c[0]):
            try:
                subprocess.call(c, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except Exception as e:
                pi_log(f"Volume cmd failed: {c} -> {e}")

# ─── STATUS SERVER ────────────────────────────────────────────────────────────

def status_server():
    global route_steps
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("0.0.0.0", STATUS_PORT))
    srv.listen(10)

    while True:
        try:
            conn, addr_info = srv.accept()
            set_mac_ip(addr_info[0])
            conn.settimeout(5)
            data = b""
            try:
                while True:
                    chunk = conn.recv(4096)
                    if not chunk: break
                    data += chunk
            except Exception:
                pass
            finally:
                conn.close()

            text = data.decode(errors="ignore").strip()
            if not text:
                continue

            with state_lock:
                for raw_line in text.splitlines():
                    line = raw_line.strip()
                    if not line or "=" not in line:
                        continue
                    k, _, v = line.partition("=")
                    k = k.strip()
                    v = v.strip()

                    if k == "command" and v == "STOP_AUDIO":
                        stop_audio()
                        continue

                    if k == "command" and v.startswith("VOL_"):
                        vol_action = v.split("_")[1]
                        _adjust_volume(vol_action)
                        continue

                    if k == "steps":
                        if v:
                            route_steps = [s.strip() for s in v.split("|") if s.strip()]
                        else:
                            route_steps = []
                        continue

                    if k in state:
                        state[k] = v

                state_changed.set()

        except Exception as e:
            pi_log(f"Status server error: {e}")

# ─── AUDIO SERVER ─────────────────────────────────────────────────────────────

def audio_server():
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("0.0.0.0", AUDIO_PORT))
    srv.listen(10)

    while True:
        try:
            conn, addr_info = srv.accept()
            set_mac_ip(addr_info[0])
            conn.settimeout(30)
            data = b""
            try:
                while len(data) < MAX_AUDIO_BYTES:
                    chunk = conn.recv(8192)
                    if not chunk: break
                    data += chunk
            except Exception:
                pass
            finally:
                conn.close()

            if len(data) < 100:
                continue

            try:
                idx = data.index(b"\n")
                playback_id = data[:idx].decode().strip()
                audio_bytes = data[idx+1:]
            except Exception as e:
                pi_log(f"Failed to parse audio payload: {e}")
                continue

            stop_audio()
            time.sleep(0.05)

            with open(AUDIO_FILE, "wb") as f:
                f.write(audio_bytes)

            play_audio(AUDIO_FILE, playback_id)
            state_changed.set()

        except Exception as e:
            pi_log(f"Audio server error: {e}")

# ─── DRAWING ──────────────────────────────────────────────────────────────────


def uptime_str():
    e = int(time.time() - boot_time)
    return f"{e//3600:02d}:{(e%3600)//60:02d}:{e%60:02d}"

def fit(text, w):
    text = str(text)
    if len(text) > w:
        return text[:w-1] + "…"
    return text.ljust(w)

def padline(content, W):
    vis = vlen(content)
    pad = max(0, W - vis)
    return pc("BDR", "│") + content + " " * pad + pc("BDR", "│")

def draw():
    cols, rows = term_size()
    W = cols - 2
    if W < 40:
        W = 40

    with state_lock:
        s     = dict(state)
        steps = list(route_steps)

    playing   = is_playing()
    now_str   = time.strftime("%d %b %Y  %H:%M:%S")
    up        = uptime_str()
    s_idx     = int(s.get("step_index", -1))

    sym, col, label = STATUS_DISPLAY.get(s["status"], ("○", "STEEL", s["status"][:12]))
    stat_txt = pc(col, f" {sym} {label}")
    spk_txt  = pc("MINT", " ♫ Playing") if (playing or s["speaking"] == "YES") else pc("DKGREY", "   Silent")

    lines = []

    def top():    lines.append(pc("BDR", "╭" + "─" * W + "╮"))
    def bot():    lines.append(pc("BDR", "╰" + "─" * W + "╯"))
    def sep():    lines.append(pc("BDR", "├" + "─" * W + "┤"))
    def thin():   lines.append(pc("BDR", "│" + pc("DKGREY", "·" * W) + pc("BDR", "│")))
    def row(c):   lines.append(padline(c, W))
    def blank():  lines.append(padline("", W))

    top()
    title = "  ✦  SMART  NAVIGATION  KIOSK  ✦  "
    row(pc("BLD", "") + pc("LAVEN", title.center(W)))
    row(pc("DKGREY", "Chitkara University".center(W)))
    sep()

    left  = f" {stat_txt}   {spk_txt}"
    right = f"{pc('DKGREY', 'Req:')} {pc('CREAM', s['requests'].rjust(3))}  {pc('DKGREY', 'Up:')} {pc('CREAM', up)} "
    lv = vlen(left)
    rv = vlen(right)
    gap = max(1, W - lv - rv)
    row(left + " " * gap + right)
    sep()

    def info_row(lbl1, val1, lbl2, val2):
        h = W // 2
        l = f"  {pc('GREY', lbl1+':')} {pc('CREAM', fit(val1, h-len(lbl1)-5))}"
        r = f"  {pc('GREY', lbl2+':')} {pc('CREAM', fit(val2, h-len(lbl2)-5))}"
        lv2 = vlen(l)
        rv2 = vlen(r)
        g = max(0, W - lv2 - rv2)
        row(l + " " * g + r)

    info_row("Destination", s["destination"], "Mode",  s["mode"])
    info_row("Distance",    s["distance"],    "ETA",   s["eta"])
    info_row("Speed",       s["speed"],       "Audio", str(audio_count))
    sep()

    row(f"  {pc('GREY', 'Event:')}  {pc('PEACH', fit(s['last_event'], W-12))}")
    row(f"  {pc('GREY', 'Heard:')}  {pc('SKY',   fit(s['last_heard'], W-12))}")
    sep()

    row(pc("BLD", "") + pc("LAVEN", "  ✦ ROUTE DIRECTIONS".ljust(W)))
    thin()

    used = len(lines) + 5
    max_steps = max(3, rows - used)
    max_steps = min(max_steps, 14)

    if not steps:
        row(pc("DKGREY", "    No active route — say a destination to begin.".ljust(W)))
        row(pc("DKGREY", "".ljust(W)))
    else:
        total = len(steps)
        if s_idx < 0:
            win_start = 0
        else:
            win_start = max(0, s_idx - max_steps // 3)
        win_end = min(total, win_start + max_steps)

        for i in range(win_start, win_end):
            num = f"{i+1}."
            txt = fit(steps[i], W - 10)
            if i == s_idx:
                row(f"  {pc('MINT', '►')} {pc('MINT', num.rjust(3))} {pc('CREAM', txt)}")
            elif s_idx >= 0 and i < s_idx:
                row(f"    {pc('DKGREY', num.rjust(3))} {pc('DKGREY', txt)}")
            else:
                row(f"    {pc('GREY', num.rjust(3))} {pc('STEEL', txt)}")

        remaining = total - win_end
        if remaining > 0:
            row(pc("DKGREY", f"    ··· {remaining} more step{'s' if remaining>1 else ''} ···".ljust(W)))

    sep()

    h1 = "  Say: [destination] · repeat · full/short/fast · slowly · normal · cancel · help"
    row(pc("DKGREY", fit(h1, W)))
    row(pc("DKGREY", f"  {now_str}".ljust(W)))
    bot()

    go_home()
    output_lines = len(lines)
    blank_pad = max(0, rows - output_lines)
    full = "\n".join(lines) + "\n" + ("\n" * blank_pad)
    sys.stdout.write(full)
    sys.stdout.flush()

# ─── HEARTBEAT ────────────────────────────────────────────────────────────────

def _heartbeat_thread():
    """Send periodic heartbeat to Mac so it knows we're alive."""
    while True:
        _send_callback(CB_HEARTBEAT)
        time.sleep(10)

# ─── MAIN ─────────────────────────────────────────────────────────────────────

def _pi_shutdown(*_):
    show_cursor()
    clr()
    sys.stderr.write("Navigation kiosk stopped.\n")
    sys.exit(0)

signal.signal(signal.SIGTERM, _pi_shutdown)
signal.signal(signal.SIGINT, _pi_shutdown)

threading.Thread(target=audio_server,  daemon=True).start()
threading.Thread(target=status_server, daemon=True).start()
threading.Thread(target=_heartbeat_thread, daemon=True, name='heartbeat').start()
threading.Thread(target=_ambient_heartbeat_thread, daemon=True, name='ambient-heartbeat').start()

clr()
hide_cursor()

while True:
    try:
        draw()
    except Exception as e:
        pi_log(f"Draw error: {e}")
    state_changed.wait(timeout=1.0)  # redraw on change or every 1s for clock
    state_changed.clear()