# Requirements Document

## Introduction

HRD Bridge è un'applicazione Python che riceve QSO (contatti radio) via UDP da WSJT-X e N1MM Logger, li inoltra a HRDLog.net via HTTP POST in formato ADIF, e serve una dashboard web locale. Questo documento copre i miglioramenti da apportare: la nuova funzionalità di notifiche popup dalla tray icon, e la risoluzione delle criticità di sicurezza, robustezza e correttezza identificate nel codice esistente.

---

## Glossary

- **Bridge**: L'applicazione HRD Bridge (bridge.py) nel suo insieme.
- **Config_Manager**: Il componente responsabile del caricamento, salvataggio e accesso alla configurazione.
- **Credential_Store**: Il sottocomponente di Config_Manager che gestisce le credenziali HRDLog (user, password).
- **Config_Lock**: Il `threading.Lock` che protegge l'accesso concorrente alla struttura `CONFIG`.
- **State_Lock**: Il `threading.Lock` (`lock`) esistente che protegge l'accesso concorrente a `state`.
- **HRD_Worker**: Un thread dedicato che consuma dalla `hrd_queue` e invia i QSO a HRDLog.net.
- **HRD_Queue**: Una `queue.Queue` thread-safe usata per accodare i QSO destinati a HRDLog.net.
- **Tray_Manager**: Il componente che gestisce l'icona nella system tray (pystray).
- **Notification_Service**: Il sottocomponente di Tray_Manager che invia popup di notifica via `pystray.Icon.notify()`.
- **Shutdown_Manager**: Il componente che coordina l'arresto ordinato dell'applicazione.
- **Restart_Coordinator**: Il componente che gestisce il riavvio sicuro dei listener UDP.
- **WSJTX_Listener**: Il thread UDP che riceve pacchetti da WSJT-X.
- **N1MM_Listener**: Il thread UDP che riceve pacchetti da N1MM Logger.
- **N1MM_Decoder**: Il componente che decodifica il payload XML di N1MM.
- **QSO_Log**: Il file `qso_log.json` che persiste lo storico dei QSO.
- **QSO**: Un contatto radio, rappresentato come dizionario Python con chiavi standardizzate.
- **Dashboard**: La dashboard web locale servita su `127.0.0.1:8080`.

---

## Requirements

### Requirement 1: Notifica Popup per QSO Ricevuto

**User Story:** Come operatore radioamatore, voglio ricevere una notifica popup dalla tray icon ogni volta che un QSO viene inoltrato con successo a HRDLog, in modo da essere informato in tempo reale senza dover tenere la dashboard aperta.

#### Acceptance Criteria

1. WHEN un QSO viene inoltrato con successo a HRDLog.net, THE Notification_Service SHALL mostrare una notifica popup nella system tray con titolo `"HRD Bridge"` e corpo nel formato `"<CALLSIGN> • <BANDA> • <MODO>"`.
2. THE Notification_Service SHALL comporre il testo della notifica nel formato `"<CALLSIGN> • <BANDA> • <MODO>"`, dove i valori provengono dai campi `callsign`, `band` e `mode` del QSO.
3. IF `TRAY_AVAILABLE` è `False`, THE Notification_Service SHALL omettere l'invio della notifica senza generare errori.
4. IF l'opzione `tray_notify` nella configurazione è `False`, THE Notification_Service SHALL omettere l'invio della notifica; IF è `True`, THE Notification_Service SHALL inviare la notifica popup tramite `pystray.Icon.notify()`.
5. IF `pystray.Icon.notify()` genera un'eccezione, THE Notification_Service SHALL loggare l'errore e continuare l'esecuzione senza propagare l'eccezione.
6. THE Tray_Manager SHALL esporre una voce di menu con testo `"Notifiche QSO: Attive"` quando `tray_notify` è `True` e `"Notifiche QSO: Disattive"` quando è `False`.
7. WHEN l'utente seleziona la voce notifiche dal menu della tray, THE Tray_Manager SHALL invertire il valore di `tray_notify` e aggiornare immediatamente il testo della voce di menu allo stato corrente.
8. WHEN `tray_notify` viene modificato tramite il menu della tray, THE Config_Manager SHALL salvare il nuovo valore in `config.json`.
9. THE Config_Manager SHALL includere `tray_notify` con valore di default `True` nella struttura `DEFAULT_CONFIG`.

---

### Requirement 2: Protezione delle Credenziali

**User Story:** Come operatore radioamatore, voglio che le mie credenziali HRDLog non siano salvate in chiaro nel file di configurazione, in modo da ridurre il rischio di esposizione accidentale.

#### Acceptance Criteria

1. THE Credential_Store SHALL offuscare `hrd_user` e `hrd_pass` prima di salvarli in `config.json`, usando la codifica Base64 con prefisso `b64:` (es. `"b64:aGVsbG8="`).
2. WHEN `config.json` viene letto e un campo credenziale inizia con il prefisso `b64:`, THE Credential_Store SHALL decodificare la parte dopo il prefisso da Base64 e memorizzare il valore testuale in `CONFIG` in memoria.
3. IF la decodifica Base64 di un campo credenziale produce un'eccezione (`binascii.Error`), THE Credential_Store SHALL loggare l'errore, impostare il campo a stringa vuota e continuare il caricamento.
4. WHEN `config.json` contiene credenziali senza il prefisso `b64:` (migrazione da versione precedente), THE Credential_Store SHALL accettarle in memoria e ri-salvarle in formato offuscato con prefisso `b64:` alla successiva operazione di `save_config()`.
5. THE Credential_Store SHALL memorizzare i valori decodificati solo nella struttura `CONFIG` in memoria e MAI scrivere valori privi del prefisso `b64:` su disco per i campi `hrd_user` e `hrd_pass`.

---

### Requirement 3: Protezione con Lock dell'Accesso a CONFIG

**User Story:** Come sviluppatore, voglio che la struttura `CONFIG` sia protetta da un lock in lettura e scrittura, in modo da eliminare le race condition negli accessi concorrenti da thread multipli.

#### Acceptance Criteria

1. THE Bridge SHALL inizializzare un `threading.Lock()` dedicato denominato `config_lock` per proteggere la struttura `CONFIG`.
2. WHEN un thread legge uno o più campi da `CONFIG`, THE Config_Manager SHALL acquisire `config_lock` per l'intera durata della lettura e rilasciarlo al completamento.
3. WHEN un thread scrive uno o più campi in `CONFIG`, THE Config_Manager SHALL acquisire `config_lock` per l'intera durata della scrittura e rilasciarlo al completamento.
4. THE Config_Manager SHALL esporre una funzione helper `get_config(key)` che acquisisce `config_lock` internamente, legge il valore di `CONFIG[key]` e lo restituisce; IF la chiave non esiste, SHALL restituire `None`.
5. THE Config_Manager SHALL esporre una funzione helper `update_config(key, value)` che acquisisce `config_lock` internamente e scrive `CONFIG[key] = value`; IF la chiave non esiste, SHALL inserirla.
6. WHEN `save_config()` serializza `CONFIG` su disco, THE Config_Manager SHALL acquisire `config_lock` per l'intera durata della lettura di `CONFIG`, in modo da prevenire race condition con scritture concorrenti.

---

### Requirement 4: Arresto Ordinato dell'Applicazione

**User Story:** Come operatore, voglio che la chiusura dell'applicazione dalla tray avvenga in modo ordinato, in modo che il file `qso_log.json` non venga mai corrotto.

#### Acceptance Criteria

1. WHEN l'utente seleziona "Chiudi" dalla tray, THE Shutdown_Manager SHALL eseguire le seguenti operazioni nell'ordine: (a) chiamare `server.shutdown()`, (b) eseguire il flush finale di `state["qso_history"]` su `qso_log.json`, (c) terminare il processo.
2. WHEN `save_qso_log()` viene chiamato durante lo shutdown, IF la scrittura su disco non si completa entro 5 secondi o genera un'eccezione, THE Shutdown_Manager SHALL loggare l'errore e procedere comunque alla terminazione.
3. WHEN l'applicazione termina tramite il percorso di chiusura dalla tray, THE Bridge SHALL garantire che tutti gli handler registrati con `atexit` vengano eseguiti prima della terminazione del processo.
4. WHEN l'utente seleziona "Chiudi" dalla tray, THE Bridge SHALL completare il flush su disco di `qso_log.json` prima che il processo termini.

---

### Requirement 5: Inoltro HRD Asincrono tramite Coda

**User Story:** Come sviluppatore, voglio che l'inoltro HTTP a HRDLog avvenga in un thread dedicato separato dal listener UDP, in modo che un timeout della rete non blocchi la ricezione di QSO successivi.

#### Acceptance Criteria

1. THE Bridge SHALL inizializzare una `queue.Queue(maxsize=1000)` denominata `hrd_queue` all'avvio.
2. THE Bridge SHALL avviare un thread daemon denominato `HRD_Worker` che consuma QSO dalla `hrd_queue` e chiama `forward_to_hrd()` per ciascuno.
3. WHEN `store_contact()` prepara un QSO, THE Bridge SHALL impostare `c["hrd_ok"] = None` e inserire il QSO nella `hrd_queue` tramite `hrd_queue.put_nowait()`; IF la coda è piena (`queue.Full`), THE Bridge SHALL loggare un avviso e scartare il QSO.
4. WHILE `hrd_queue` contiene elementi, THE HRD_Worker SHALL estrarre ogni QSO con `hrd_queue.get()` e processarlo senza eseguire `time.sleep()` nel corpo del loop.
5. IF `forward_to_hrd()` genera un'eccezione per un QSO, THE HRD_Worker SHALL loggare l'errore, chiamare `hrd_queue.task_done()` e continuare a processare il QSO successivo senza terminare; WHEN il forward ha successo, THE HRD_Worker SHALL ugualmente chiamare `hrd_queue.task_done()`.

---

### Requirement 6: Sincronizzazione Sicura del Riavvio dei Listener

**User Story:** Come sviluppatore, voglio che il riavvio dei listener UDP avvenga tramite un evento di sincronizzazione, in modo da eliminare i `time.sleep` fissi inaffidabili.

#### Acceptance Criteria

1. THE Restart_Coordinator SHALL sostituire i `time.sleep()` fissi in `restart_listeners()` con la chiamata a `thread.join(timeout=2.0)` sui thread WSJTX_Listener e N1MM_Listener esistenti, attendendo la loro terminazione effettiva dopo la chiusura dei socket.
2. WHEN i socket WSJTX_Listener e N1MM_Listener vengono chiusi e `thread.join(timeout=2.0)` scade senza che il thread sia terminato, THE Restart_Coordinator SHALL loggare un avviso e procedere comunque all'avvio dei nuovi listener.
3. THE Bridge SHALL memorizzare i riferimenti agli oggetti `threading.Thread` dei listener attivi in variabili accessibili a `Restart_Coordinator`, in modo che possa invocare `join()` su di essi.

---

### Requirement 7: Gestione Tipizzata delle Eccezioni

**User Story:** Come sviluppatore, voglio che tutte le clausole `except` catturino tipi di eccezione specifici, in modo da non mascherare errori di programmazione inattesi.

#### Acceptance Criteria

1. THE N1MM_Decoder SHALL sostituire il `except:` generico con `except (ET.ParseError, ValueError, AttributeError) as e:` e loggare il messaggio di errore.
2. THE QSO_Log SHALL sostituire il `except:` generico in `save_qso_log()` con `except (OSError, IOError, TypeError) as e:`.
3. THE Bridge SHALL sostituire ogni altra occorrenza di `except:` senza tipo con una clausola che catturi almeno `Exception as e` e loggi il dettaglio dell'errore.

---

### Requirement 8: Dipendenze Dichiarate in requirements.txt

**User Story:** Come sviluppatore, voglio che tutte le dipendenze Python del progetto siano elencate con versioni fissate in `requirements.txt`, in modo da garantire build riproducibili.

#### Acceptance Criteria

1. THE Bridge SHALL dichiarare `requests`, `pystray` e `Pillow` in `requirements.txt` con versioni minime fissate.
2. THE `requirements.txt` SHALL seguire il formato `package>=X.Y.Z` per ogni dipendenza.
3. THE `requirements.txt` SHALL includere almeno: `requests>=2.31.0`, `pystray>=0.19.5`, `Pillow>=10.0.0`.

---

### Requirement 9: Estrazione Frequenza in N1MM_Decoder

**User Story:** Come operatore radioamatore, voglio che i QSO ricevuti da N1MM includano la frequenza in MHz, in modo che la banda venga calcolata correttamente come per i QSO WSJT-X.

#### Acceptance Criteria

1. WHEN N1MM_Decoder elabora un pacchetto XML, THE N1MM_Decoder SHALL estrarre il campo `<Freq>` (o `<freq>`) e convertirlo in MHz nella chiave `freq_mhz` del QSO risultante.
2. IF il campo frequenza è assente o non numerico nel pacchetto XML, THE N1MM_Decoder SHALL impostare `freq_mhz` a `0.0` senza generare eccezioni.
3. WHEN `freq_mhz` è disponibile e `band` non è specificata, THE Bridge SHALL calcolare la banda tramite `freq_to_band()` come già avviene per i QSO WSJT-X.

---

### Requirement 10: Correzione di /api/clear

**User Story:** Come operatore radioamatore, voglio che la chiamata a `/api/clear` azzeri anche lo storico QSO visualizzato in dashboard, in modo che i dati mostrati siano coerenti.

#### Acceptance Criteria

1. WHEN viene ricevuta una richiesta POST a `/api/clear`, THE Dashboard SHALL azzerare sia `state["contacts"]` sia `state["qso_history"]`.
2. WHEN viene ricevuta una richiesta POST a `/api/clear`, THE QSO_Log SHALL sovrascrivere `qso_log.json` con una lista vuota `[]`.

---

### Requirement 11: Correzione del Test UDP

**User Story:** Come sviluppatore, voglio che `test_udp.py` utilizzi una data dinamica invece di un Julian Day hardcodato, in modo che il test produca output temporali realistici indipendentemente dalla data di esecuzione.

#### Acceptance Criteria

1. THE `test_udp.py` SHALL calcolare il Julian Day corrente a partire da `datetime.utcnow()` invece di usare un valore costante.
2. THE `test_udp.py` SHALL calcolare i millisecondi dall'inizio del giorno UTC corrente invece di usare un valore costante.
