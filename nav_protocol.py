# ═══════════════════════════════════════════════════════════════════════════════
#  NAV PROTOCOL  —  shared constants between Mac brain and Pi kiosk
# ═══════════════════════════════════════════════════════════════════════════════

# ─── NETWORK PORTS ────────────────────────────────────────────────────────────

AUDIO_PORT      = 5000       # Mac → Pi: TTS audio file
STATUS_PORT     = 5050       # Mac → Pi: status updates
CALLBACK_PORT   = 5060       # Pi → Mac: playback-done, heartbeat
LISTEN_PORT     = 12345      # ESP → Mac: raw audio from button press

# ─── LIMITS ───────────────────────────────────────────────────────────────────

MAX_AUDIO_BYTES = 10 * 1024 * 1024   # 10 MB max audio transfer

# ─── STATUS VALUES ────────────────────────────────────────────────────────────

STATUS_READY         = "READY"
STATUS_RECEIVING     = "RECEIVING"
STATUS_PROCESSING    = "PROCESSING"
STATUS_NAVIGATING    = "NAVIGATING"
STATUS_REPLAYING     = "REPLAYING"
STATUS_NOT_FOUND     = "NOT FOUND"
STATUS_HELP          = "HELP"
STATUS_WAITING       = "WAITING"
STATUS_AUTO_NAVIGATE = "AUTO-NAVIGATE"
STATUS_CANCELLED     = "CANCELLED"
STATUS_OFFLINE       = "OFFLINE"

# ─── CALLBACK MESSAGES ───────────────────────────────────────────────────────

CB_PLAYBACK_DONE = "PLAYBACK_DONE"
CB_HEARTBEAT     = "HEARTBEAT"

# ─── STATE KEYS ───────────────────────────────────────────────────────────────

STATE_KEYS = [
    "status", "destination", "distance", "eta", "mode",
    "speed", "requests", "last_event", "last_heard",
    "speaking", "step_index",
]

# ─── ANSI PASTEL PALETTE ─────────────────────────────────────────────────────

PALETTE = {
    "RST":    "\033[0m",
    "BLD":    "\033[1m",
    "DIM":    "\033[2m",
    "PINK":   "\033[38;5;218m",
    "MINT":   "\033[38;5;158m",
    "SKY":    "\033[38;5;117m",
    "LAVEN":  "\033[38;5;183m",
    "PEACH":  "\033[38;5;216m",
    "CREAM":  "\033[38;5;230m",
    "SAGE":   "\033[38;5;151m",
    "CORAL":  "\033[38;5;210m",
    "STEEL":  "\033[38;5;249m",
    "FROST":  "\033[38;5;153m",
    "GREY":   "\033[38;5;245m",
    "DKGREY": "\033[38;5;240m",
    "WHITE":  "\033[38;5;255m",
    "WARN":   "\033[38;5;222m",
    "ERR":    "\033[38;5;174m",
    "OK":     "\033[38;5;157m",
    "BDR":    "\033[38;5;104m",
}

# ─── STATUS → DISPLAY MAP ────────────────────────────────────────────────────
# (symbol, palette_color, label)

STATUS_DISPLAY = {
    STATUS_READY:         ("●", "OK",    "READY"),
    STATUS_RECEIVING:     ("↓", "FROST", "RECEIVING"),
    STATUS_PROCESSING:    ("◌", "WARN",  "PROCESSING"),
    STATUS_NAVIGATING:    ("►", "MINT",  "NAVIGATING"),
    STATUS_REPLAYING:     ("↺", "SKY",   "REPLAYING"),
    STATUS_NOT_FOUND:     ("✕", "CORAL", "NOT FOUND"),
    STATUS_HELP:          ("?", "LAVEN", "HELP"),
    STATUS_WAITING:       ("…", "PEACH", "WAITING"),
    STATUS_AUTO_NAVIGATE: ("►", "MINT",  "AUTO-NAV"),
    STATUS_CANCELLED:     ("■", "ERR",   "CANCELLED"),
    STATUS_OFFLINE:       ("⊘", "ERR",   "OFFLINE"),
}

# ─── HELPER ───────────────────────────────────────────────────────────────────

def pc(name, text):
    """Colorize text using the palette."""
    return f"{PALETTE.get(name, '')}{text}{PALETTE['RST']}"
