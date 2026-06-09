# Design Document — HRD Bridge Improvements

## Overview

HRD Bridge è un'applicazione Python single-file (`bridge.py`) che riceve QSO via UDP da WSJT-X e N1MM Logger, li inoltra a HRDLog.net via HTTP POST ADIF, e serve una dashboard web locale su `127.0.0.1:8080`. Questo documento descrive l'architettura e le scelte di design per le 11 aree di miglioramento definite nei requisiti.

I miglioramenti si raggruppano in quattro macro-aree:

| Macro-area | Requisiti |
|---|---|
| UX / Notifiche | Req 1 (tray popup) |
| Sicurezza | Req 2 (credential obfuscation), Req 3 (config_lock) |
| Robustezza & Architettura | Req 4 (shutdown), Req 5 (async HRD), Req 6 (restart), Req 7 (exceptions) |
| Qualità del codice | Req 8 (requirements.txt), Req 9 (N1MM freq), Req 10 (api/clear), Req 11 (test_udp) |

La struttura single-file viene mantenuta; i cambiamenti sono addizioni e sostituzioni localizzate.

---

## Architecture

```mermaid
graph TD
    subgraph UDP Sources
        W[WSJT-X UDP :2237]
        N[N1MM Logger UDP :12060]
    end

    subgraph Bridge Process
        WL[WSJTX_Listener thread]
        NL[N1MM_Listener thread]
        SC[store_contact()]
        HQ[(hrd_queue\nmaxsize=1000)]
        HW[HRD_Worker thread]
        HRD[forward_to_hrd()]
        NS[Notification_Service]
        CM[Config_Manager\nget_config / update_config\nconfig_lock]
        SM[State_Lock\nlock]
        SH[Shutdown_Manager]
        WEB[HTTPServer :8080]
        TRAY[Tray_Manager\npystray]
    end

    subgraph Persistence
        CFG[config.json\nb64: credentials]
        LOG[qso_log.json]
    end

    W --> WL --> SC
    N --> NL --> SC
    SC --> HQ --> HW --> HRD
    HRD --> NS
    NS --> TRAY
    CM --> CFG
    SC --> SM
    WEB --> CM
    WEB --> SM
    SH --> WEB
    SH --> LOG
    TRAY --> SH
```

I thread principali e il loro ciclo di vita:

| Thread | Daemon | Avvio | Terminazione |
|---|---|---|---|
| WSJTX_Listener | sì | `main()` | chiusura socket (OSError) |
| N1MM_Listener | sì | `main()` | chiusura socket (OSError) |
| HRD_Worker | sì | `main()` | ciclo infinito su `hrd_queue.get()` |
| HTTPServer | sì | `main()` | `server.shutdown()` da Shutdown_Manager |
| Restart thread | sì | su port change | auto-termine dopo join+start |

---

## Components and Interfaces

### Config_Manager

Gestisce `CONFIG` (dict) con protezione tramite `config_lock`.

```python
config_lock = threading.Lock()

def get_config(key):
    """Ritorna CONFIG[key] oppure None se assente."""
    with config_lock:
        return CONFIG.get(key)

def update_config(key, value):
    """Scrive CONFIG[key] = value."""
    with config_lock:
        CONFIG[key] = value
```

`save_config()` acquisisce `config_lock` per leggere `CONFIG` prima di serializzarlo. Applica l'encoding Base64 ai soli campi `hrd_user` e `hrd_pass` prima di scrivere su disco.

`load_config()` decodifica i campi con prefisso `b64:` al caricamento.

### Credential_Store

Sottocomponente di Config_Manager che gestisce l'encoding/decoding.

```python
import base64, binascii

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
```

Al momento di `save_config()`, i campi `hrd_user` e `hrd_pass` vengono sempre encoded prima di essere scritti su disco, indipendentemente dal loro stato in memoria. Al momento di `load_config()`, vengono decoded prima di essere inseriti in `CONFIG`.

### Notification_Service

Funzione `tray_notify(icon, contact)` chiamata da `HRD_Worker` dopo un forward riuscito.

```python
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
```

`icon` è il riferimento a `pystray.Icon` passato al worker. Il menu della tray include una voce toggle:

```python
pystray.MenuItem(
    lambda item: "Notifiche QSO: Attive" if get_config("tray_notify") else "Notifiche QSO: Disattive",
    on_toggle_notify,
    checked=lambda item: get_config("tray_notify"),
)
```

`on_toggle_notify` inverte `tray_notify` tramite `update_config()` e chiama `save_config()`.

### HRD_Worker

Thread daemon che consuma dalla `hrd_queue`.

```python
hrd_queue = queue.Queue(maxsize=1000)

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
```

`store_contact()` non chiama più `forward_to_hrd()` direttamente; imposta `c["hrd_ok"] = None` e chiama `hrd_queue.put_nowait(c)`, con gestione di `queue.Full`.

### Restart_Coordinator

`restart_listeners()` riceve i riferimenti ai thread attivi e usa `join(timeout=2.0)` per attenderne la terminazione prima di avviarne di nuovi.

```python
# Riferimenti globali ai thread listener
listener_threads = {"wsjtx": None, "n1mm": None}

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
```

### Shutdown_Manager

La funzione `on_quit` nel tray segue questa sequenza ordinata:

```python
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
```

`os._exit(0)` garantisce la terminazione immediata dopo il flush, rispettando i requisiti di ordine.

### N1MM_Decoder (aggiornato)

Aggiunge estrazione di `<Freq>` e gestione eccezioni tipizzate:

```python
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
```

### Dashboard — /api/clear (fix)

```python
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
```

### test_udp.py (fix)

```python
from datetime import datetime, timezone

def pack_datetime():
    now = datetime.now(timezone.utc)
    # Julian Day Number dalla data corrente
    Y, M, D = now.year, now.month, now.day
    a = (14 - M) // 12
    y = Y + 4800 - a
    m = M + 12 * a - 3
    jd = D + (153 * m + 2) // 5 + 365 * y + y // 4 - y // 100 + y // 400 - 32045
    # Millisecondi dall'inizio del giorno UTC
    ms = (now.hour * 3600 + now.minute * 60 + now.second) * 1000
    spec = 1  # UTC
    return struct.pack(">QIB", jd, ms, spec)
```

---

## Data Models

### CONFIG (in memoria)

```python
DEFAULT_CONFIG = {
    "wsjtx_port":  2237,
    "n1mm_port":   12060,
    "bind_host":   "0.0.0.0",
    "web_port":    8080,
    "hrd_url":     "http://robot.hrdlog.net/newentry.aspx",
    "hrd_user":    "",    # in memoria: testo chiaro; su disco: b64:...
    "hrd_pass":    "",    # in memoria: testo chiaro; su disco: b64:...
    "tray_notify": True,  # NUOVO
}
```

### config.json (su disco)

```json
{
  "wsjtx_port": 2237,
  "n1mm_port": 12060,
  "hrd_user": "b64:dXNlcm5hbWU=",
  "hrd_pass": "b64:cGFzc3dvcmQ=",
  "tray_notify": true
}
```

### QSO contact dict

```python
{
    "source":        "WSJT-X" | "N1MM",
    "callsign":      str,          # uppercase
    "grid":          str,
    "freq_mhz":      float,        # in MHz
    "mode":          str,          # uppercase
    "band":          str,          # es. "20m"
    "rst_sent":      str,
    "rst_rcvd":      str,
    "comments":      str,
    "name":          str,
    "datetime":      str,          # "YYYY-MM-DD HH:MM:SS"
    "received_at":   str,          # "HH:MM:SS"
    "received_date": str,          # "YYYY-MM-DD"
    "id":            int,          # timestamp ms
    "hrd_ok":        bool | None,  # None finché HRD_Worker non processa
}
```

### state dict

Invariato rispetto alla versione corrente; la protezione tramite `lock` (State_Lock) rimane per tutti gli accessi.

---

## Correctness Properties

*A property is a characteristic or behavior that should hold true across all valid executions of a system — essentially, a formal statement about what the system should do. Properties serve as the bridge between human-readable specifications and machine-verifiable correctness guarantees.*

### Property 1: Notification body format

*For any* QSO contact dict with non-empty `callsign`, `band` and `mode` fields, the notification body string produced by `Notification_Service` SHALL equal `"{callsign} • {band} • {mode}"`.

**Validates: Requirements 1.1, 1.2**

---

### Property 2: tray_notify toggle is a complement

*For any* boolean value of `tray_notify` in CONFIG, after one toggle operation, the value SHALL equal `not(original_value)`.

**Validates: Requirements 1.7**

---

### Property 3: Credential round-trip

*For any* non-empty credential string `s`, encoding it with `_encode_credential(s)` and then decoding with `_decode_credential()` SHALL return `s` unchanged.

**Validates: Requirements 2.1, 2.2**

---

### Property 4: Credentials on disk always obfuscated

*For any* string value assigned to `hrd_user` or `hrd_pass` in CONFIG (whether plaintext or already b64-encoded), after `save_config()`, the corresponding field read from `config.json` on disk SHALL start with the prefix `"b64:"`.

**Validates: Requirements 2.5**

---

### Property 5: get_config / update_config round-trip

*For any* key string `k` and value `v`, after `update_config(k, v)`, calling `get_config(k)` SHALL return `v`.

**Validates: Requirements 3.4, 3.5**

---

### Property 6: N1MM frequency extraction

*For any* valid N1MM XML packet containing a numeric `<Freq>` value in kHz, the `freq_mhz` field of the decoded QSO SHALL equal `round(freq_khz / 1000.0, 4)`.

**Validates: Requirements 9.1, 9.2**

---

### Property 7: decode_n1mm never raises on malformed input

*For any* byte sequence (including empty, non-UTF8, and malformed XML), `decode_n1mm()` SHALL return `None` without raising an exception.

**Validates: Requirements 7.1**

---

### Property 8: /api/clear produces empty state

*For any* prior state containing an arbitrary number of contacts and history entries, after a POST to `/api/clear`, both `state["contacts"]` and `state["qso_history"]` SHALL be empty lists, and `qso_log.json` SHALL contain `[]`.

**Validates: Requirements 10.1, 10.2**

---

### Property 9: Julian Day correctness in test_udp

*For any* UTC date, the Julian Day Number computed by `pack_datetime()` in `test_udp.py` SHALL equal the value derived from the standard JDN formula applied to that same date, and the millisecond value SHALL equal `(hour * 3600 + minute * 60 + second) * 1000` for the current UTC time.

**Validates: Requirements 11.1, 11.2**

---

## Error Handling

| Scenario | Componente | Comportamento |
|---|---|---|
| `pystray.notify()` raises | Notification_Service | log + continua, non propaga |
| Base64 decode fallisce | Credential_Store | log + campo = `""`, caricamento continua |
| `hrd_queue` piena (`queue.Full`) | store_contact | log avviso + QSO scartato, no eccezione |
| `forward_to_hrd()` raises | HRD_Worker | log + `task_done()` + continua |
| `save_qso_log()` timeout >5s | Shutdown_Manager | log + `os._exit(0)` comunque |
| `save_qso_log()` raises `(OSError, IOError, TypeError)` | QSO_Log | log + non propaga |
| XML malformato da N1MM | N1MM_Decoder | `except (ET.ParseError, ValueError, AttributeError)` + return `None` |
| Thread listener non termina in 2s | Restart_Coordinator | log avviso + avvia comunque i nuovi listener |
| `config.json` corrotto | Config_Manager | log + usa `DEFAULT_CONFIG` |
| Bind porta occupata | WSJTX/N1MM Listener | log errore + thread termina, sockets["key"] = None |

Le clausole `except:` nude rimanenti (fuori dai casi sopra elencati) vengono sostituite con `except Exception as e:` con logging del dettaglio.

---

## Testing Strategy

### Approccio generale

Si usa un approccio duale:

- **Unit / example tests**: verificano comportamenti specifici, casi limite, integrazioni tra componenti.
- **Property-based tests**: verificano proprietà universali su un ampio spazio di input generato automaticamente.

La libreria scelta per PBT è **[Hypothesis](https://hypothesis.readthedocs.io/)** (Python), standard de-facto per PBT in Python. Ogni property test è configurato per eseguire almeno 100 iterazioni (`settings(max_examples=100)`).

### Tag format

Ogni property test deve essere annotato con un commento:

```
# Feature: hrd-bridge-improvements, Property N: <testo della proprietà>
```

### Property tests (Hypothesis)

| Test | Property | Requisiti |
|---|---|---|
| `test_notification_body_format` | Property 1 | 1.1, 1.2 |
| `test_tray_notify_toggle` | Property 2 | 1.7 |
| `test_credential_round_trip` | Property 3 | 2.1, 2.2 |
| `test_credentials_obfuscated_on_disk` | Property 4 | 2.5 |
| `test_get_update_config_round_trip` | Property 5 | 3.4, 3.5 |
| `test_n1mm_freq_extraction` | Property 6 | 9.1, 9.2 |
| `test_decode_n1mm_never_raises` | Property 7 | 7.1 |
| `test_api_clear_empties_state` | Property 8 | 10.1, 10.2 |
| `test_julian_day_correctness` | Property 9 | 11.1, 11.2 |

### Unit / example tests

| Test | Tipo | Requisiti |
|---|---|---|
| `test_tray_notify_unavailable` | example | 1.3 |
| `test_tray_notify_config_false` | example | 1.4 |
| `test_tray_notify_exception_suppressed` | example | 1.5 |
| `test_menu_text_active` / `test_menu_text_inactive` | example | 1.6 |
| `test_tray_notify_saves_config` | example | 1.8 |
| `test_default_config_has_tray_notify` | example | 1.9 |
| `test_invalid_b64_fallback` | example | 2.3 |
| `test_migration_plaintext_reencoded` | example | 2.4 |
| `test_config_lock_exists` | smoke | 3.1 |
| `test_concurrent_config_access` | integration | 3.2, 3.3 |
| `test_shutdown_order` | integration | 4.1 |
| `test_shutdown_flush_timeout` | example | 4.2 |
| `test_hrd_queue_maxsize` | smoke | 5.1 |
| `test_hrd_worker_calls_forward` | integration | 5.2 |
| `test_store_contact_sets_hrd_ok_none` | example | 5.3 |
| `test_hrd_worker_task_done_on_error` | example | 5.5 |
| `test_restart_uses_join_not_sleep` | smoke | 6.1 |
| `test_restart_join_timeout` | example | 6.2 |
| `test_listener_thread_refs_exist` | smoke | 6.3 |
| `test_save_qso_log_oserror` | example | 7.2 |
| `test_requirements_txt_content` | smoke | 8.1–8.3 |
| `test_n1mm_freq_missing` | example | 9.2 |

### Coverage target

- Tutte le 11 aree di requisiti devono avere almeno un test.
- I 9 property test coprono i percorsi logici più critici.
- I test di integrazione usano mock per evitare chiamate HTTP reali a HRDLog.net.
