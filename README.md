# HRD Bridge

Bridge software che riceve i contatti (QSO) inviati via **UDP** da **WSJT-X**, **JS8Call** e **N1MM Logger**, e li inoltra automaticamente al tuo logbook online su **[HRDLog.net](https://www.hrdlog.net)**.

Include una dashboard web con monitoraggio in tempo reale, inserimento manuale dei QSO e gestione della configurazione.

---

## Caratteristiche

- Ricezione automatica dei QSO da WSJT-X / JS8Call (protocollo binario UDP)
- Ricezione automatica dei QSO da N1MM Logger (protocollo XML UDP)
- Inoltro automatico a HRDLog.net in formato ADIF
- Dashboard web con statistiche in tempo reale
- Inserimento manuale dei QSO con selezione della banda
- Storico locale dei QSO della sessione (persistente)
- Configurazione di credenziali e porte direttamente dall'interfaccia
- Icona nella system tray per gestione e chiusura
- Eseguibile standalone per Windows (non richiede Python installato)

---

## Supporta il progetto

Se HRD Bridge ti è utile, puoi offrirmi un caffè per supportarne lo sviluppo:

<a href="https://www.paypal.com/paypalme/FabioBratta" target="_blank"><img src="https://img.shields.io/badge/PayPal-Dona-blue?logo=paypal&style=for-the-badge" alt="Dona con PayPal" height="40"></a>

---

## Per gli utenti: installazione rapida (Windows)

Se hai scaricato la versione già compilata (`.exe`):

1. Scarica l'archivio dalla sezione **[Releases](../../releases)** e scompattalo in una cartella a tua scelta
2. Avvia **`HRDBridge.exe`**
3. Il browser si aprirà automaticamente sulla dashboard (`http://127.0.0.1:8080`)
4. Apri il menu (icona in alto a sinistra) e vai su **Configurazione**
5. Inserisci il tuo **Callsign** e il **Code** di HRDLog.net (vedi sotto come ottenerlo), poi salva

L'app gira in background: la trovi come icona nell'area di notifica (system tray), vicino all'orologio. Da lì puoi riaprire la dashboard o chiudere l'applicazione.

### Dove trovo il "Code" di HRDLog?

Il Code (o "Upload Code") è un codice personale diverso dalla password del sito. Lo trovi nel tuo account su HRDLog.net, nella sezione delle impostazioni del profilo/upload.

---

## Configurazione dei software sorgente

Perché i QSO arrivino al bridge, devi abilitare l'invio UDP nei tuoi programmi.

### WSJT-X / JS8Call

Vai su **File → Settings → Reporting** e imposta:

- UDP Server: `127.0.0.1`
- UDP Server port: `2237`
- Spunta **Accept UDP requests**

I QSO vengono inviati quando premi **Log QSO**.

### N1MM Logger+

Vai su **Config → Configure Ports, Mode Control, Audio, Other → Broadcast Data** e aggiungi:

- `127.0.0.1:12060`

Spunta l'invio dei **Contact** (ContactInfo).

> Le porte 2237 e 12060 sono i valori di default e si possono modificare dalla tab Configurazione dell'app.

---

## Le pagine della dashboard

- **Dashboard** — statistiche live e tabella dei contatti ricevuti in tempo reale
- **Log HRDLog** — storico dei QSO inviati durante le sessioni
- **QSO Manuale** — inserimento manuale di un contatto con selezione della banda
- **Configurazione** — credenziali HRDLog e porte UDP

---

## Per gli sviluppatori: avvio da sorgente

### Requisiti

- Python 3.8 o superiore
- Le dipendenze elencate in `requirements.txt`

### Installazione

```bash
git clone https://github.com/iz7xib/hrd-bridge.git
cd hrd-bridge
pip install -r requirements.txt
```

### Avvio

```bash
python bridge.py
```

La dashboard sarà disponibile su `http://127.0.0.1:8080` e si aprirà automaticamente nel browser.

`bridge.py` e `index.html` devono trovarsi nella stessa cartella.

---

## Compilare l'eseguibile (Windows)

Per generare un `.exe` distribuibile che non richiede Python sul PC dell'utente:

```bash
pip install pyinstaller
pyinstaller HRDBridge.spec
```

L'eseguibile verrà creato in `dist/HRDBridge.exe`.

Lo spec file include già `index.html` all'interno dell'eseguibile e gli import necessari per la system tray.

### Cosa distribuire agli utenti

È sufficiente il singolo file **`dist/HRDBridge.exe`**. Non includere `config.json`: l'app lo crea automaticamente vuoto al primo avvio, così ogni utente inserisce le proprie credenziali. In questo modo non condividi mai le tue.

---

## File generati a runtime

Questi file vengono creati automaticamente accanto all'eseguibile (o allo script) e **non** vanno versionati né distribuiti:

- `config.json` — credenziali e porte (contiene dati sensibili)
- `qso_log.json` — storico locale dei QSO

---

## Note tecniche

- Il bridge ascolta su tutte le interfacce (`0.0.0.0`) sulle porte configurate
- La dashboard web è servita in locale su `127.0.0.1:8080`
- I QSO vengono inviati a HRDLog tramite richiesta HTTP POST in formato ADIF
- Lo storico locale conserva fino agli ultimi 2000 QSO

---

## Licenza

Questo progetto è distribuito sotto licenza **MIT**. Vedi il file [LICENSE](LICENSE) per i dettagli.

## Autore

IZ7XIB

Se il progetto ti è piaciuto, lascia una stella ⭐ al repository e considera di [offrirmi un caffè via PayPal](https://www.paypal.com/paypalme/FabioBratta)!

73!