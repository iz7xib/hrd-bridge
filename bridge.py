"""
HRD Bridge - Full Working Version
WSJT-X / N1MM UDP -> ADIF -> HRDLog HTTP
"""

import socket
import threading
import json
import time
import struct
import queue
import xml.etree.ElementTree as ET
from datetime import datetime
from http.server import HTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import os
import sys
import webbrowser
import requests
import base64
import binascii

try:
    import pystray
    from PIL import Image, ImageDraw
    TRAY_AVAILABLE = True
except ImportError:
    TRAY_AVAILABLE = False

# ─── CREDENTIAL HELPERS ───────────────────────────────────────────────────────

B64_PREFIX = "b64:"

def _encode_credential(value: str) -> str:
    return B64_PREFIX + base64.b64encode(value.encode()).decode()

def _decode_credential(value: str) -> str:
    if value.startswith(B64_PREFIX):
        try:
            return base64.b64decode(value[len(B64_PREFIX):]).decode()
        except binascii.Error as e:
            print(f"[CREDENTIAL] Errore decodifica: {e}")
            return ""
    return value   # migrazione: valore in chiaro, sarà re-scritto alla prossima save

# ─── PERCORSI ─────────────────────────────────────────────────────────────────

def resource_path():
    """Percorso dei file inclusi nell'exe (index.html). In --onefile e' _MEIPASS."""
    if getattr(sys, 'frozen', False):
        return getattr(sys, '_MEIPASS', os.path.dirname(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))

def writable_path():
    """Percorso per i file modificabili (config.json, log). Accanto all'exe."""
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))

RES_PATH    = resource_path()
DATA_PATH   = writable_path()
HTML_FILE   = os.path.join(RES_PATH, "index.html")
CONFIG_FILE = os.path.join(DATA_PATH, "config.json")
LOG_FILE    = os.path.join(DATA_PATH, "qso_log.json")

# ─── CONFIG ───────────────────────────────────────────────────────────────────

DEFAULT_CONFIG = {
    "wsjtx_port": 2237,
    "n1mm_port": 12060,
    "bind_host": "0.0.0.0",
    "web_port": 8080,
    "hrd_url": "http://robot.hrdlog.net/newentry.aspx",
    "hrd_user": "",
    "hrd_pass": "",
    "tray_notify": True,
}

def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r") as f:
                saved = json.load(f)
            cfg = DEFAULT_CONFIG.copy()
            cfg.update(saved)
            # Decode credentials: b64:-prefixed values are decoded; plaintext values
            # (migration from older versions without the prefix) are kept as-is in
            # memory and will be re-encoded to b64: on the next save_config() call.
            cfg["hrd_user"] = _decode_credential(cfg["hrd_user"])
            cfg["hrd_pass"] = _decode_credential(cfg["hrd_pass"])
            print(f"[CONFIG] Caricata da {CONFIG_FILE}")
            return cfg
        except Exception as e:
            print(f"[CONFIG] Errore caricamento, uso defaults: {e}")
    return DEFAULT_CONFIG.copy()

def save_config():
    try:
        with config_lock:
            to_save = dict(CONFIG)
        to_save["hrd_user"] = _encode_credential(to_save["hrd_user"])
        to_save["hrd_pass"] = _encode_credential(to_save["hrd_pass"])
        with open(CONFIG_FILE, "w") as f:
            json.dump(to_save, f, indent=2)
        print(f"[CONFIG] Salvata in {CONFIG_FILE}")
    except Exception as e:
        print(f"[CONFIG] Errore salvataggio: {e}")

CONFIG = load_config()
config_lock = threading.Lock()

def get_config(key):
    """Ritorna CONFIG[key] oppure None se assente."""
    with config_lock:
        return CONFIG.get(key)

def update_config(key, value):
    """Scrive CONFIG[key] = value."""
    with config_lock:
        CONFIG[key] = value

# ─── STORICO QSO ──────────────────────────────────────────────────────────────

def load_qso_log():
    if os.path.exists(LOG_FILE):
        try:
            with open(LOG_FILE, "r") as f:
                return json.load(f)
        except (OSError, IOError, ValueError) as e:
            print(f"[LOG] Errore caricamento storico: {e}")
            return []
    return []

def save_qso_log():
    try:
        with open(LOG_FILE, "w") as f:
            json.dump(state["qso_history"], f, indent=2)
    except (OSError, IOError, TypeError) as e:
        print(f"[LOG] Errore salvataggio storico: {e}")

# ─── STATE ────────────────────────────────────────────────────────────────────

state = {
    "contacts": [],
    "qso_history": [],
    "stats": {
        "wsjtx_received": 0,
        "n1mm_received": 0,
        "hrd_forwarded": 0,
        "hrd_errors": 0,
        "last_contact": None,
    },
    "hrd_connected": False,
    "running": True,
}

lock = threading.Lock()
state["qso_history"] = load_qso_log()

hrd_queue = queue.Queue(maxsize=1000)

# ─── NORMALIZE ────────────────────────────────────────────────────────────────

def normalize(c):
    try:
        c["freq_mhz"] = float(c.get("freq_mhz") or 0)
    except (TypeError, ValueError) as e:
        print(f"[NORMALIZE] Errore conversione freq_mhz: {e}")
        c["freq_mhz"] = 0.0
    c["callsign"] = (c.get("callsign") or "").upper()
    c["mode"]     = (c.get("mode") or "").upper()
    c["grid"]     = (c.get("grid") or "").upper()
    return c

# ─── BANDA DA FREQUENZA ───────────────────────────────────────────────────────

def freq_to_band(freq_mhz):
    bands = [
        (1.8,1.999,"160m"),(3.5,3.999,"80m"),(5.0,5.45,"60m"),
        (7.0,7.3,"40m"),(10.1,10.15,"30m"),(14.0,14.35,"20m"),
        (18.068,18.168,"17m"),(21.0,21.45,"15m"),(24.89,24.99,"12m"),
        (28.0,29.7,"10m"),(50.0,54.0,"6m"),(144.0,148.0,"2m"),(430.0,440.0,"70cm"),
    ]
    for low, high, band in bands:
        if low <= freq_mhz <= high:
            return band
    return ""

# ─── ADIF BUILDER ─────────────────────────────────────────────────────────────

def adif_field(tag, value):
    if value in (None, ""):
        return ""
    v = str(value).strip()
    return f"<{tag}:{len(v)}>{v} "

def build_adif(c):
    dt = c.get("datetime", "")
    date = dt[:10].replace("-", "") if len(dt) >= 10 else ""
    t    = dt[11:16].replace(":", "") if len(dt) >= 16 else ""
    return (
        adif_field("CALL",     c.get("callsign"))
        + adif_field("QSO_DATE", date)
        + adif_field("TIME_ON",  t)
        + adif_field("BAND",     c.get("band", ""))
        + adif_field("MODE",     c.get("mode", ""))
        + adif_field("RST_SENT", c.get("rst_sent", ""))
        + adif_field("RST_RCVD", c.get("rst_rcvd", ""))
        + adif_field("FREQ",     c.get("freq_mhz", ""))
        + adif_field("COMMENT",  c.get("comments", ""))
        + adif_field("NAME",     c.get("name", ""))
        + "<EOR>"
    )

# ─── HRD FORWARD ──────────────────────────────────────────────────────────────

def forward_to_hrd(c, retry_count=0, max_retries=2):
    # Verifica credenziali configurate
    if not get_config("hrd_user") or not get_config("hrd_pass"):
        with lock:
            state["stats"]["hrd_errors"] += 1
            state["hrd_connected"] = False
        print(f"[HRD] ERRORE: Credenziali HRDLog non configurate! Apri la Dashboard per configurarle.")
        return False
    
    try:
        adif = build_adif(c)
        payload = {
            "Callsign": get_config("hrd_user"),
            "Code":     get_config("hrd_pass"),
            "App":      "HRD-Python-Bridge",
            "ADIFData": adif,
        }
        r = requests.post(get_config("hrd_url"), data=payload, timeout=10)
        
        # Verifica errori nella risposta XML
        if "error" in r.text.lower():
            if "unknown user" in r.text.lower():
                raise Exception("Credenziali HRDLog non valide - Verifica Username/Password nella configurazione")
            elif "invalid code" in r.text.lower():
                raise Exception("Password HRDLog non valida - Verifica il Code nella configurazione")
            else:
                raise Exception(r.text)
        with lock:
            state["stats"]["hrd_forwarded"] += 1
            state["hrd_connected"] = True
        print("[HRD] OK", c["callsign"])
        return True
    except requests.exceptions.ConnectionError as e:
        # Errore di connessione DNS o rete
        if "getaddrinfo failed" in str(e) or "Failed to resolve" in str(e):
            error_msg = f"Impossibile risolvere 'robot.hrdlog.net' - Verifica connessione Internet/DNS"
        else:
            error_msg = f"Errore di connessione: {e}"
        
        with lock:
            state["stats"]["hrd_errors"] += 1
            state["hrd_connected"] = False
        
        # Retry con backoff esponenziale
        if retry_count < max_retries:
            wait_time = 2 ** retry_count  # 1s, 2s, 4s
            print(f"[HRD] {error_msg} - Retry {retry_count + 1}/{max_retries} tra {wait_time}s per {c['callsign']}")
            time.sleep(wait_time)
            return forward_to_hrd(c, retry_count + 1, max_retries)
        else:
            print(f"[HRD] ERROR dopo {max_retries} tentativi: {error_msg} per {c['callsign']}")
            return False
    
    except requests.exceptions.Timeout:
        with lock:
            state["stats"]["hrd_errors"] += 1
            state["hrd_connected"] = False
        print(f"[HRD] TIMEOUT dopo 10s per {c['callsign']}")
        return False
    
    except Exception as e:
        with lock:
            state["stats"]["hrd_errors"] += 1
            state["hrd_connected"] = False
        print(f"[HRD] ERROR generico: {e} per {c['callsign']}")
        return False

# ─── TRAY NOTIFY ─────────────────────────────────────────────────────────────

def tray_notify(icon, contact):
    if not TRAY_AVAILABLE:
        return
    if not get_config("tray_notify"):
        return
    try:
        body = f"{contact['callsign']} • {contact.get('band','?')} • {contact.get('mode','?')}"
        icon.notify(body, "HRD Bridge")
    except Exception as e:
        print(f"[NOTIFY] Errore notifica: {e}")

# ─── HRD WORKER ───────────────────────────────────────────────────────────────

def hrd_worker(icon):
    while True:
        contact = hrd_queue.get()
        try:
            ok = forward_to_hrd(contact)
            if ok:
                tray_notify(icon, contact)
        except Exception as e:
            print(f"[HRD_WORKER] Errore: {e}")
        finally:
            hrd_queue.task_done()

# ─── STORE CONTACT ────────────────────────────────────────────────────────────

def store_contact(c):
    c = normalize(c)
    c["received_at"]   = datetime.utcnow().strftime("%H:%M:%S")
    c["received_date"] = datetime.utcnow().strftime("%Y-%m-%d")
    c["id"]            = int(time.time() * 1000)
    if not c.get("band"):
        c["band"] = freq_to_band(c.get("freq_mhz") or 0)

    c["hrd_ok"] = None
    try:
        hrd_queue.put_nowait(c)
    except queue.Full:
        print(f"[STORE] Avviso: hrd_queue piena, QSO {c.get('callsign', '?')} scartato")

    with lock:
        state["contacts"].insert(0, c)
        state["contacts"] = state["contacts"][:200]
        state["stats"]["last_contact"] = c["callsign"]
        state["qso_history"].insert(0, c)
        state["qso_history"] = state["qso_history"][:2000]

    save_qso_log()

# ─── WSJT-X DECODER ───────────────────────────────────────────────────────────

def decode_wsjtx(data):
    try:
        if len(data) < 12:
            return None
        magic, schema, msg_type = struct.unpack_from(">III", data, 0)
        if magic != 0xADBCCBDA:
            return None
        if msg_type != 5:
            return None

        offset = 12

        def read_str():
            nonlocal offset
            if offset + 4 > len(data):
                return ""
            length = struct.unpack_from(">I", data, offset)[0]
            offset += 4
            if length == 0 or length == 0xFFFFFFFF:
                return ""
            if offset + length > len(data):
                return ""
            v = data[offset:offset+length].decode("utf-8", errors="ignore")
            offset += length
            return v

        def read_u32():
            nonlocal offset
            if offset + 4 > len(data):
                return 0
            v = struct.unpack_from(">I", data, offset)[0]
            offset += 4
            return v

        def read_u64():
            nonlocal offset
            if offset + 8 > len(data):
                return 0
            v = struct.unpack_from(">Q", data, offset)[0]
            offset += 8
            return v

        def read_u8():
            nonlocal offset
            if offset + 1 > len(data):
                return 0
            v = struct.unpack_from("B", data, offset)[0]
            offset += 1
            return v

        client_id = read_str()
        jd        = read_u64()
        ms        = read_u32()
        spec      = read_u8()
        if spec == 2:
            offset += 4

        dx_call = read_str()
        dx_grid = read_str()
        freq_hz = read_u64()
        mode    = read_str()
        rst_s   = read_str()
        rst_r   = read_str()
        tx_pwr  = read_str()
        comment = read_str()
        name    = read_str()

        try:
            a = jd + 32044
            b = (4*a + 3) // 146097
            c = a - (146097*b)//4
            d = (4*c + 3) // 1461
            e = c - (1461*d)//4
            m = (5*e + 2) // 153
            day   = e - (153*m + 2)//5 + 1
            month = m + 3 - 12*(m//10)
            year  = 100*b + d - 4800 + m//10
            h  = ms // 3600000
            mn = (ms % 3600000) // 60000
            s  = (ms % 60000) // 1000
            dt = f"{year:04d}-{month:02d}-{day:02d} {h:02d}:{mn:02d}:{s:02d}"
        except (struct.error, ValueError, OverflowError, ZeroDivisionError) as e:
            print(f"[WSJT-X] Errore calcolo datetime: {e}")
            dt = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

        if not dx_call:
            return None

        print(f"[WSJT-X] Decodificato: call={dx_call} grid={dx_grid} freq={freq_hz} mode={mode}")

        return {
            "source":   "WSJT-X",
            "callsign": dx_call,
            "grid":     dx_grid,
            "freq_mhz": round(freq_hz / 1e6, 4) if freq_hz else 0.0,
            "mode":     mode,
            "rst_sent": rst_s,
            "rst_rcvd": rst_r,
            "comments": comment,
            "name":     name,
            "datetime": dt,
            "band":     "",
        }
    except Exception as e:
        print(f"[WSJT-X] Errore decode: {e}")
        return None

# ─── N1MM DECODER ─────────────────────────────────────────────────────────────

def decode_n1mm(data):
    try:
        text = data.decode(errors="ignore")
        if "<" not in text:
            return None
        root = ET.fromstring(text)

        def g(tag):
            e = root.find(tag)
            return e.text if e is not None else ""

        call = g("call")
        if not call:
            return None

        # Estrazione frequenza: N1MM invia kHz, convertiamo in MHz
        freq_khz_str = g("Freq") or g("freq")
        try:
            freq_mhz = round(float(freq_khz_str) / 1000.0, 4) if freq_khz_str else 0.0
        except ValueError:
            freq_mhz = 0.0

        return {
            "source":   "N1MM",
            "callsign": call,
            "mode":     g("mode"),
            "band":     g("band"),
            "freq_mhz": freq_mhz,
            "rst_sent": g("snt"),
            "rst_rcvd": g("rcv"),
            "name":     g("name"),
            "grid":     g("gridsquare"),
            "datetime": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
        }
    except (ET.ParseError, ValueError, AttributeError) as e:
        print(f"[N1MM] Errore decode: {e}")
        return None

# ─── SOCKET GLOBALI ───────────────────────────────────────────────────────────

sockets = {"wsjtx": None, "n1mm": None}

# Riferimenti globali ai thread listener
listener_threads = {"wsjtx": None, "n1mm": None}

# ─── UDP LISTENER WSJT-X ──────────────────────────────────────────────────────

def wsjtx():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sockets["wsjtx"] = s
    try:
        s.bind((get_config("bind_host"), get_config("wsjtx_port")))
        print(f"[WSJT] In ascolto sulla porta {get_config('wsjtx_port')}")
    except Exception as e:
        print(f"[WSJT] ERRORE bind porta {get_config('wsjtx_port')}: {e}")
        return

    TIPI_IGNORATI = {0, 1, 2, 3, 4, 6, 7, 8, 9, 10, 11}

    while True:
        try:
            d, addr = s.recvfrom(65535)
            testo = d[:5].decode('utf-8', errors='ignore')
            if testo.startswith("<"):
                c = decode_n1mm(d)
            else:
                if len(d) >= 12:
                    magic, schema, msg_type = struct.unpack_from(">III", d, 0)
                    if msg_type in TIPI_IGNORATI:
                        continue
                c = decode_wsjtx(d)
            if c:
                with lock:
                    if c["source"] == "WSJT-X":
                        state["stats"]["wsjtx_received"] += 1
                    else:
                        state["stats"]["n1mm_received"] += 1
                store_contact(c)
        except OSError:
            print("[WSJT] Socket chiuso, uscita thread.")
            break
        except Exception as e:
            print(f"[WSJT] Errore: {e}")

# ─── UDP LISTENER N1MM ────────────────────────────────────────────────────────

def n1mm():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sockets["n1mm"] = s
    try:
        s.bind((get_config("bind_host"), get_config("n1mm_port")))
        print(f"[N1MM] In ascolto sulla porta {get_config('n1mm_port')}")
    except Exception as e:
        print(f"[N1MM] ERRORE bind porta {get_config('n1mm_port')}: {e}")
        return

    while True:
        try:
            d, addr = s.recvfrom(65535)
            c = decode_n1mm(d)
            if c:
                with lock:
                    state["stats"]["n1mm_received"] += 1
                store_contact(c)
        except OSError:
            print("[N1MM] Socket chiuso, uscita thread.")
            break
        except Exception as e:
            print(f"[N1MM] Errore: {e}")

# ─── RESTART LISTENERS ────────────────────────────────────────────────────────

def restart_listeners():
    for key in ("wsjtx", "n1mm"):
        sock = sockets.get(key)
        if sock:
            try:
                sock.close()
            except OSError:
                pass
        t = listener_threads.get(key)
        if t and t.is_alive():
            t.join(timeout=2.0)
            if t.is_alive():
                print(f"[RESTART] Avviso: thread {key} non terminato dopo 2s, procedo comunque")

    t_w = threading.Thread(target=wsjtx, daemon=True)
    t_n = threading.Thread(target=n1mm,  daemon=True)
    listener_threads["wsjtx"] = t_w
    listener_threads["n1mm"]  = t_n
    t_w.start()
    t_n.start()
    print(f"[RESTART] Listener riavviati -> WSJT:{get_config('wsjtx_port')} N1MM:{get_config('n1mm_port')}")

# ─── WEB HANDLER ──────────────────────────────────────────────────────────────

class Handler(SimpleHTTPRequestHandler):

    def _json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/api/state":
            with lock:
                data = {
                    "contacts":      state["contacts"][:50],
                    "stats":         state["stats"],
                    "hrd_connected": state["hrd_connected"],
                }
            self._json(data)

        elif self.path == "/api/config":
            self._json(CONFIG)

        elif self.path.startswith("/api/hrdlog"):
            qs = parse_qs(urlparse(self.path).query)
            limit = int(qs.get("limit", ["50"])[0])
            with lock:
                history = state["qso_history"][:limit]
            records = [{
                "date":     c.get("datetime", "")[:10],
                "time":     c.get("datetime", "")[11:16],
                "call":     c.get("callsign", ""),
                "band":     c.get("band", "") or freq_to_band(float(c.get("freq_mhz") or 0)),
                "mode":     c.get("mode", ""),
                "rst_sent": c.get("rst_sent", ""),
                "rst_rcvd": c.get("rst_rcvd", ""),
                "name":     c.get("name", ""),
            } for c in history]
            self._json({"records": records})

        elif self.path == "/" or self.path == "/index.html":
            try:
                with open(HTML_FILE, "rb") as f:
                    body = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", len(body))
                self.end_headers()
                self.wfile.write(body)
            except FileNotFoundError:
                self.send_error(404, "index.html non trovato")
        else:
            self.send_error(404)

    def do_POST(self):
        if self.path == "/api/config":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            try:
                updates = json.loads(body)
                port_changed = False
                old_wsjtx = get_config("wsjtx_port")
                old_n1mm  = get_config("n1mm_port")

                for key in ("hrd_url", "hrd_user", "hrd_pass", "wsjtx_port", "n1mm_port"):
                    if key in updates and updates[key] != "":
                        if key in ("wsjtx_port", "n1mm_port"):
                            new_val = int(updates[key])
                            if new_val != get_config(key):
                                update_config(key, new_val)
                                port_changed = True
                        else:
                            update_config(key, str(updates[key]))

                save_config()

                if port_changed:
                    print("[CONFIG] Porte cambiate, riavvio listener UDP...")
                    threading.Thread(target=restart_listeners, daemon=True).start()

                self._json({"ok": True})
            except Exception as e:
                self._json({"error": str(e)}, code=400)

        elif self.path == "/api/qso":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            try:
                qso = json.loads(body)
                if not qso.get("callsign"):
                    raise Exception("Callsign mancante")
                qso["band"] = freq_to_band(float(qso.get("freq_mhz") or 0))
                store_contact(qso)
                self._json({"ok": True})
            except Exception as e:
                self._json({"error": str(e)}, code=400)

        elif self.path == "/api/clear":
            with lock:
                state["contacts"].clear()
                state["qso_history"].clear()
            # Sovrascrive qso_log.json con lista vuota
            try:
                with open(LOG_FILE, "w") as f:
                    json.dump([], f)
            except (OSError, IOError) as e:
                print(f"[CLEAR] Errore pulizia log: {e}")
            self._json({"ok": True})

        else:
            self.send_error(404)

    def log_message(self, format, *args):
        pass

# ─── TRAY ICON ────────────────────────────────────────────────────────────────

def create_tray_icon(server):
    def create_image():
        img = Image.new("RGB", (64, 64), color=(30, 30, 30))
        d = ImageDraw.Draw(img)
        d.ellipse([8, 8, 56, 56], fill=(59, 130, 246))
        d.ellipse([20, 20, 44, 44], fill=(16, 185, 129))
        return img

    def on_open(icon, item):
        webbrowser.open(f"http://127.0.0.1:{get_config('web_port')}")

    def on_toggle_notify(icon, item):
        update_config("tray_notify", not get_config("tray_notify"))
        save_config()

    def on_quit(icon, item):
        print("[SHUTDOWN] Avvio chiusura ordinata...")
        icon.stop()
        server.shutdown()
        done = threading.Event()
        def _flush():
            try:
                save_qso_log()
            except Exception as e:
                print(f"[SHUTDOWN] Errore flush log: {e}")
            finally:
                done.set()
        threading.Thread(target=_flush, daemon=True).start()
        if not done.wait(timeout=5.0):
            print("[SHUTDOWN] Timeout flush log (5s), procedo comunque")
        os._exit(0)

    menu = pystray.Menu(
        pystray.MenuItem("Apri Dashboard", on_open, default=True),
        pystray.MenuItem(f"HRD Bridge - porta {get_config('web_port')}", None, enabled=False),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem(
            lambda item: "Notifiche QSO: Attive" if get_config("tray_notify") else "Notifiche QSO: Disattive",
            on_toggle_notify,
            checked=lambda item: get_config("tray_notify"),
        ),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Chiudi", on_quit),
    )
    return pystray.Icon("HRDBridge", create_image(), "HRD Bridge - In esecuzione", menu)

# ─── MAIN ─────────────────────────────────────────────────────────────────────

def main():
    print("=" * 50)
    print("  HRD Bridge v1.0")
    print("=" * 50)
    print(f"[DEBUG] index.html: {HTML_FILE} (esiste: {os.path.exists(HTML_FILE)})")

    t_wsjtx = threading.Thread(target=wsjtx, daemon=True)
    t_n1mm  = threading.Thread(target=n1mm,  daemon=True)
    listener_threads["wsjtx"] = t_wsjtx
    listener_threads["n1mm"]  = t_n1mm
    t_wsjtx.start()
    t_n1mm.start()

    server = HTTPServer(("127.0.0.1", get_config("web_port")), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    threading.Timer(1.0, lambda: webbrowser.open(f"http://127.0.0.1:{get_config('web_port')}")).start()

    print(f"Dashboard: http://127.0.0.1:{get_config('web_port')}")

    if TRAY_AVAILABLE:
        print("Usa l'icona nella tray per chiudere.\n")
        tray = create_tray_icon(server)
        threading.Thread(target=hrd_worker, args=(tray,), daemon=True, name="HRD_Worker").start()
        tray.run()
    else:
        print("Premi Ctrl+C per fermare.\n")
        threading.Thread(target=hrd_worker, args=(None,), daemon=True, name="HRD_Worker").start()
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print("\nChiusura in corso...")
            server.shutdown()

if __name__ == "__main__":
    main()