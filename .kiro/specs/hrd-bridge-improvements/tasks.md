# Implementation Plan: HRD Bridge Improvements

## Overview

Incrementally improve `bridge.py` (and `test_udp.py`) across 11 areas: tray notifications, credential obfuscation, config locking, orderly shutdown, async HRD queue, safe listener restart, typed exceptions, dependency pinning, N1MM frequency extraction, `/api/clear` fix, and dynamic Julian Day in the test script. Each task builds on the previous ones; the final tasks wire everything together and ensure test coverage.

## Tasks

- [x] 1. Aggiornare `requirements.txt` con versioni pinned
  - Sostituire il contenuto attuale di `requirements.txt` con le dipendenze minime fissate: `requests>=2.31.0`, `pystray>=0.19.5`, `Pillow>=10.0.0`
  - _Requirements: 8.1, 8.2, 8.3_

- [x] 2. Implementare Credential_Store (offuscamento Base64)
  - [x] 2.1 Aggiungere helper `_encode_credential` e `_decode_credential` in `bridge.py`
    - Importare `base64` e `binascii` nella sezione import
    - Definire la costante `B64_PREFIX = "b64:"`
    - Implementare `_encode_credential(value: str) -> str` con prefisso `b64:`
    - Implementare `_decode_credential(value: str) -> str` con gestione `binascii.Error`
    - _Requirements: 2.1, 2.2, 2.3_

  - [x] 2.2 Aggiornare `load_config()` per decodificare le credenziali al caricamento
    - Dopo `cfg.update(saved)`, decodificare `cfg["hrd_user"]` e `cfg["hrd_pass"]` con `_decode_credential`
    - Gestire il caso di migrazione (valore senza prefisso `b64:` viene accettato in memoria)
    - _Requirements: 2.2, 2.4_

  - [x] 2.3 Aggiornare `save_config()` per encodare le credenziali prima della scrittura su disco
    - Prima di `json.dump`, creare una copia di `CONFIG` con `hrd_user` e `hrd_pass` encodati tramite `_encode_credential`
    - Non modificare `CONFIG` in memoria
    - _Requirements: 2.1, 2.5_

  - [ ]* 2.4 Scrivere property test per Credential_Store
    - **Property 3: Credential round-trip** — `_decode_credential(_encode_credential(s)) == s` per qualsiasi stringa non vuota
    - **Validates: Requirements 2.1, 2.2**
    - **Property 4: Credentials on disk always obfuscated** — dopo `save_config()`, i campi letti da `config.json` iniziano con `"b64:"`
    - **Validates: Requirements 2.5**

  - [ ]* 2.5 Scrivere unit test di esempio per Credential_Store
    - `test_invalid_b64_fallback`: decodifica di un valore `b64:` corrotto restituisce `""` e non propaga eccezioni
    - `test_migration_plaintext_reencoded`: un valore senza prefisso `b64:` viene accettato in memoria e re-salvato offuscato alla successiva `save_config()`
    - _Requirements: 2.3, 2.4_

- [x] 3. Implementare Config_Manager con `config_lock`
  - [x] 3.1 Aggiungere `config_lock = threading.Lock()` e le funzioni helper `get_config` / `update_config`
    - Definire `config_lock` subito dopo l'inizializzazione di `CONFIG`
    - Implementare `get_config(key)` che acquisisce `config_lock` e restituisce `CONFIG.get(key)`
    - Implementare `update_config(key, value)` che acquisisce `config_lock` e scrive `CONFIG[key] = value`
    - _Requirements: 3.1, 3.4, 3.5_

  - [x] 3.2 Proteggere `save_config()` con `config_lock`
    - All'interno di `save_config()`, acquisire `config_lock` prima di leggere `CONFIG` per la serializzazione
    - _Requirements: 3.6_

  - [x] 3.3 Sostituire gli accessi diretti a `CONFIG` nei componenti esistenti con `get_config` / `update_config`
    - In `forward_to_hrd()`, sostituire `CONFIG["hrd_user"]`, `CONFIG["hrd_pass"]`, `CONFIG["hrd_url"]` con `get_config(...)`
    - In `Handler.do_POST` (config endpoint), sostituire le scritture dirette con `update_config(...)`
    - In `restart_listeners()` e `wsjtx()` / `n1mm()`, sostituire le letture di porta/host con `get_config(...)`
    - _Requirements: 3.2, 3.3_

  - [ ]* 3.4 Scrivere property test per Config_Manager
    - **Property 5: get_config / update_config round-trip** — per qualsiasi chiave `k` e valore `v`, dopo `update_config(k, v)` la chiamata `get_config(k)` restituisce `v`
    - **Validates: Requirements 3.4, 3.5**

  - [ ]* 3.5 Scrivere unit / integration test per Config_Manager
    - `test_config_lock_exists`: verifica che `config_lock` sia istanza di `threading.Lock`
    - `test_concurrent_config_access`: più thread chiamano `get_config` / `update_config` in parallelo senza deadlock o race condition
    - _Requirements: 3.1, 3.2, 3.3_

- [x] 4. Implementare HRD_Worker e coda asincrona
  - [x] 4.1 Aggiungere `import queue` e inizializzare `hrd_queue = queue.Queue(maxsize=1000)`
    - Aggiungere l'import nella sezione import
    - Definire `hrd_queue` nella sezione state/globali
    - _Requirements: 5.1_

  - [x] 4.2 Implementare la funzione `hrd_worker(icon)`
    - Loop infinito su `hrd_queue.get()`; chiamare `forward_to_hrd(contact)` e `tray_notify(icon, contact)` (stub per ora)
    - Gestire eccezioni con `except Exception as e:` + log
    - Chiamare `hrd_queue.task_done()` nel blocco `finally`
    - Nessun `time.sleep()` nel corpo del loop
    - _Requirements: 5.2, 5.4, 5.5_

  - [x] 4.3 Modificare `store_contact()` per usare `hrd_queue` invece di chiamare `forward_to_hrd()` direttamente
    - Impostare `c["hrd_ok"] = None` prima dell'inserimento in coda
    - Chiamare `hrd_queue.put_nowait(c)` con gestione di `queue.Full` (log + scarta)
    - Rimuovere la chiamata diretta a `forward_to_hrd(c)` e l'assegnazione `c["hrd_ok"] = ok`
    - _Requirements: 5.3_

  - [x] 4.4 Avviare `HRD_Worker` in `main()`
    - Aggiungere `threading.Thread(target=hrd_worker, args=(tray,), daemon=True, name="HRD_Worker").start()` in `main()`, dopo l'inizializzazione della tray
    - _Requirements: 5.2_

  - [ ]* 4.5 Scrivere unit / integration test per HRD_Worker
    - `test_hrd_queue_maxsize`: verifica che `hrd_queue.maxsize == 1000`
    - `test_hrd_worker_calls_forward`: con mock di `forward_to_hrd`, verifica che il worker chiami il forward per ogni QSO
    - `test_store_contact_sets_hrd_ok_none`: verifica che `store_contact` imposti `hrd_ok = None` prima dell'enqueue
    - `test_hrd_worker_task_done_on_error`: verifica che `task_done()` venga chiamato anche quando `forward_to_hrd` solleva eccezione
    - _Requirements: 5.1, 5.2, 5.3, 5.5_

- [x] 5. Checkpoint — Verificare che `bridge.py` si avvii senza errori
  - Assicurarsi che tutti i test passino finora, chiedere all'utente se sorgono dubbi.

- [x] 6. Implementare Notification_Service e menu tray aggiornato
  - [x] 6.1 Aggiungere `tray_notify` a `DEFAULT_CONFIG` con valore di default `True`
    - Aggiungere `"tray_notify": True` al dizionario `DEFAULT_CONFIG`
    - _Requirements: 1.9_

  - [x] 6.2 Implementare la funzione `tray_notify(icon, contact)`
    - Restituire immediatamente se `not TRAY_AVAILABLE`
    - Restituire immediatamente se `not get_config("tray_notify")`
    - Comporre il corpo: `f"{contact['callsign']} • {contact.get('band','?')} • {contact.get('mode','?')}"`
    - Chiamare `icon.notify(body, "HRD Bridge")` con `except Exception as e:` + log
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5_

  - [x] 6.3 Aggiornare `create_tray_icon()` per aggiungere la voce toggle notifiche al menu
    - Aggiungere handler `on_toggle_notify(icon, item)` che inverte `tray_notify` via `update_config()` e chiama `save_config()`
    - Aggiungere `pystray.MenuItem` con testo dinamico `lambda item: "Notifiche QSO: Attive" if get_config("tray_notify") else "Notifiche QSO: Disattive"` e attributo `checked`
    - _Requirements: 1.6, 1.7, 1.8_

  - [ ]* 6.4 Scrivere property test per Notification_Service
    - **Property 1: Notification body format** — per qualsiasi QSO con `callsign`, `band`, `mode` non vuoti, il corpo della notifica è esattamente `"{callsign} • {band} • {mode}"`
    - **Validates: Requirements 1.1, 1.2**
    - **Property 2: tray_notify toggle is a complement** — dopo un'operazione di toggle, `tray_notify == not(original_value)`
    - **Validates: Requirements 1.7**

  - [ ]* 6.5 Scrivere unit test di esempio per Notification_Service
    - `test_tray_notify_unavailable`: quando `TRAY_AVAILABLE = False`, nessuna notifica, nessuna eccezione
    - `test_tray_notify_config_false`: quando `tray_notify = False`, nessuna chiamata a `icon.notify`
    - `test_tray_notify_exception_suppressed`: quando `icon.notify` solleva eccezione, l'errore viene loggato e non propagato
    - `test_menu_text_active` / `test_menu_text_inactive`: verifica testo voce menu in entrambi gli stati
    - `test_tray_notify_saves_config`: verifica che `on_toggle_notify` chiami `save_config()`
    - `test_default_config_has_tray_notify`: verifica che `DEFAULT_CONFIG["tray_notify"] == True`
    - _Requirements: 1.3, 1.4, 1.5, 1.6, 1.8, 1.9_

- [x] 7. Implementare Restart_Coordinator con `thread.join`
  - [x] 7.1 Aggiungere il dizionario `listener_threads = {"wsjtx": None, "n1mm": None}` e aggiornare `main()` per salvare i riferimenti ai thread listener
    - Definire `listener_threads` nella sezione globali
    - In `main()`, assegnare i thread avviati a `listener_threads["wsjtx"]` e `listener_threads["n1mm"]`
    - _Requirements: 6.3_

  - [x] 7.2 Riscrivere `restart_listeners()` per usare `thread.join(timeout=2.0)` al posto dei `time.sleep` fissi
    - Chiudere i socket via `sockets[key].close()`
    - Per ogni thread in `listener_threads`, chiamare `t.join(timeout=2.0)` e loggare avviso se il thread non termina
    - Creare e avviare i nuovi thread, salvando i riferimenti in `listener_threads`
    - Rimuovere tutti i `time.sleep()` dalla funzione
    - Usare `get_config(...)` per leggere le porte nel log finale
    - _Requirements: 6.1, 6.2_

  - [ ]* 7.3 Scrivere unit test per Restart_Coordinator
    - `test_restart_uses_join_not_sleep`: verifica che `restart_listeners` non chiami `time.sleep`
    - `test_restart_join_timeout`: simula un thread che non termina, verifica che venga loggato l'avviso e si proceda comunque
    - `test_listener_thread_refs_exist`: verifica che `listener_threads` abbia chiavi `"wsjtx"` e `"n1mm"`
    - _Requirements: 6.1, 6.2, 6.3_

- [x] 8. Implementare Shutdown_Manager ordinato
  - [x] 8.1 Riscrivere `on_quit()` con sequenza ordinata: `icon.stop()` → `server.shutdown()` → flush con timeout → `os._exit(0)`
    - Sostituire il corpo di `on_quit` con la sequenza definita nel design
    - Usare `threading.Event` e timeout di 5 secondi per il flush
    - Loggare l'errore se il timeout scade ma procedere comunque con `os._exit(0)`
    - _Requirements: 4.1, 4.2, 4.3, 4.4_

  - [ ]* 8.2 Scrivere integration test per Shutdown_Manager
    - `test_shutdown_order`: con mock di `server.shutdown` e `save_qso_log`, verifica che le chiamate avvengano nell'ordine corretto
    - `test_shutdown_flush_timeout`: simula `save_qso_log` che impiega >5s, verifica che il programma prosegua alla terminazione
    - _Requirements: 4.1, 4.2_

- [x] 9. Correggere le eccezioni nude (`except:`) rimanenti
  - [x] 9.1 Sostituire il `except:` generico in `decode_n1mm()` con `except (ET.ParseError, ValueError, AttributeError) as e:`
    - Aggiungere il log dell'errore: `print(f"[N1MM] Errore decode: {e}")`
    - _Requirements: 7.1_

  - [x] 9.2 Sostituire il `except:` generico in `save_qso_log()` con `except (OSError, IOError, TypeError) as e:`
    - _Requirements: 7.2_

  - [x] 9.3 Sostituire ogni altra occorrenza di `except:` senza tipo in `bridge.py` con `except Exception as e:` con logging del dettaglio
    - Controllare `decode_wsjtx`, `normalize`, `load_config`, e qualsiasi altro blocco residuo
    - _Requirements: 7.3_

  - [ ]* 9.4 Scrivere unit test per la gestione delle eccezioni tipizzate
    - `test_save_qso_log_oserror`: simula un `OSError` durante la scrittura, verifica log e nessuna propagazione
    - **Property 7: decode_n1mm never raises on malformed input** — per qualsiasi sequenza di byte, `decode_n1mm()` non solleva mai eccezioni
    - **Validates: Requirements 7.1**

- [x] 10. Aggiungere estrazione frequenza in N1MM_Decoder
  - [x] 10.1 Aggiornare `decode_n1mm()` per estrarre `<Freq>` (o `<freq>`) e convertirlo in MHz
    - Estrarre `g("Freq") or g("freq")`
    - Convertire da kHz a MHz: `round(float(freq_khz_str) / 1000.0, 4)`
    - Gestire `ValueError` impostando `freq_mhz = 0.0`
    - Aggiungere la chiave `"freq_mhz": freq_mhz` nel dict restituito
    - _Requirements: 9.1, 9.2_

  - [x] 10.2 Verificare che `store_contact()` calcoli `band` da `freq_mhz` quando `band` non è specificata
    - La logica `if not c.get("band"): c["band"] = freq_to_band(...)` è già presente; verificare che sia compatibile con il nuovo campo
    - _Requirements: 9.3_

  - [ ]* 10.3 Scrivere property test per N1MM_Decoder
    - **Property 6: N1MM frequency extraction** — per qualsiasi pacchetto XML N1MM con `<Freq>` numerico, `freq_mhz == round(freq_khz / 1000.0, 4)`
    - **Validates: Requirements 9.1, 9.2**

  - [ ]* 10.4 Scrivere unit test di esempio per N1MM_Decoder
    - `test_n1mm_freq_missing`: XML senza `<Freq>` produce `freq_mhz = 0.0` senza eccezioni
    - _Requirements: 9.2_

- [x] 11. Correggere `/api/clear`
  - [x] 11.1 Aggiornare il handler `POST /api/clear` in `Handler.do_POST` per azzerare anche `state["qso_history"]` e sovrascrivere `qso_log.json` con `[]`
    - Aggiungere `state["qso_history"].clear()` dentro il blocco `with lock:`
    - Aprire `LOG_FILE` in scrittura e fare `json.dump([], f)` con `except (OSError, IOError) as e:` + log
    - _Requirements: 10.1, 10.2_

  - [ ]* 11.2 Scrivere property test per `/api/clear`
    - **Property 8: /api/clear produces empty state** — per qualsiasi stato iniziale con N contatti, dopo POST `/api/clear` sia `state["contacts"]` che `state["qso_history"]` sono liste vuote e `qso_log.json` contiene `[]`
    - **Validates: Requirements 10.1, 10.2**

- [-] 12. Correggere `test_udp.py` con data dinamica
  - [ ] 12.1 Aggiungere `from datetime import datetime, timezone` in `test_udp.py`
    - _Requirements: 11.1, 11.2_

  - [ ] 12.2 Implementare la funzione `pack_datetime()` con calcolo dinamico del Julian Day e dei millisecondi
    - Calcolare JDN dalla data UTC corrente tramite la formula standard
    - Calcolare i millisecondi dall'inizio del giorno: `(now.hour * 3600 + now.minute * 60 + now.second) * 1000`
    - Sostituire il valore hardcodato di JD e ms usati nel pacchetto WSJT-X
    - _Requirements: 11.1, 11.2_

  - [ ]* 12.3 Scrivere property test per `pack_datetime`
    - **Property 9: Julian Day correctness** — per qualsiasi data UTC, il JDN calcolato da `pack_datetime()` coincide con la formula standard JDN e i millisecondi corrispondono a `(H*3600 + M*60 + S)*1000`
    - **Validates: Requirements 11.1, 11.2**

- [ ] 13. Checkpoint finale — Assicurarsi che tutti i test passino
  - Eseguire l'intera suite di test (unit, property, integration)
  - Verificare che `bridge.py` si avvii correttamente senza errori di import o runtime
  - Chiedere all'utente se sorgono dubbi prima di chiudere il task.

## Notes

- I task contrassegnati con `*` sono opzionali e possono essere saltati per un MVP più rapido
- Ogni task fa riferimento ai requisiti specifici per la tracciabilità
- I checkpoint garantiscono validazione incrementale
- I property test (con Hypothesis) validano proprietà universali di correttezza
- I test di integrazione usano mock per evitare chiamate HTTP reali a HRDLog.net
- L'applicazione mantiene la struttura single-file (`bridge.py`); tutti i miglioramenti sono addizioni e sostituzioni localizzate

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1", "2.1", "3.1", "4.1", "6.1", "7.1", "9.1", "9.2", "9.3", "12.1"] },
    { "id": 1, "tasks": ["2.2", "2.3", "3.2", "4.2", "10.1"] },
    { "id": 2, "tasks": ["2.4", "2.5", "3.3", "4.3", "10.2", "10.3", "10.4"] },
    { "id": 3, "tasks": ["3.4", "3.5", "4.4", "6.2", "6.3", "11.1", "12.2"] },
    { "id": 4, "tasks": ["4.5", "6.4", "6.5", "7.2", "9.4", "11.2", "12.3"] },
    { "id": 5, "tasks": ["7.3", "8.1"] },
    { "id": 6, "tasks": ["8.2"] }
  ]
}
```
