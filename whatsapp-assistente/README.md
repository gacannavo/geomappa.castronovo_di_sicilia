# Assistente WhatsApp Business

Un piccolo servizio che si collega al tuo numero WhatsApp Business e lavora da solo:

- **Trascrive i messaggi vocali** dei clienti (in locale con Whisper: l'audio non lascia il tuo server e non costa nulla).
- **Riassume ogni chat** con Claude: chi è il cliente, cosa chiede, a che punto siete, cosa devi fare tu.
- **Dà una priorità** (alta / media / bassa) e segnala le chat che **aspettano una tua risposta**.
- **Riconosce gli appuntamenti** ("passate giovedì alle 9?", "confermo per lunedì") e li mette in un calendario che puoi **aggiungere a Google Calendar** o al calendario dell'iPhone.
- **Riepilogo del giorno**: a richiesta o in automatico la sera, anche inviato sul tuo WhatsApp personale.
- **Pannello web** (funziona bene anche sul telefono) con chat, vocali riascoltabili con trascrizione, appuntamenti modificabili.

```
Cliente ──WhatsApp──▶ Meta (Cloud API) ──webhook──▶ questo server
                                                     ├─ vocale → Whisper → testo
                                                     ├─ chat → Claude → sintesi, priorità, appuntamenti
                                                     ├─ pannello web  (https://tuo-dominio/)
                                                     ├─ calendario    (https://tuo-dominio/calendario.ics?token=…)
                                                     └─ avvisi sul tuo WhatsApp personale (facoltativi)
```

L'assistente **non risponde mai ai clienti al posto tuo**: legge e organizza, rispondi sempre tu.

---

## Cosa serve

| Cosa | Dove | Costo indicativo |
|---|---|---|
| Account Meta Business + app sviluppatore con WhatsApp | business.facebook.com, developers.facebook.com | gratis (i messaggi ricevuti e le risposte entro 24 ore non si pagano) |
| Un piccolo server Linux con dominio e HTTPS (2 vCPU, 4 GB RAM) | qualsiasi VPS | 5–10 €/mese |
| Chiave API Anthropic | console.anthropic.com | pochi centesimi per chat analizzata |

## 1. Collegare il numero a WhatsApp Cloud API

1. Su **developers.facebook.com** crea un'app di tipo **Business** e aggiungi il prodotto **WhatsApp**, collegandolo al tuo account Meta Business.
2. In **WhatsApp → Configurazione API** aggiungi il tuo numero.
   - **Vuoi continuare a usare l'app WhatsApp Business sul telefono?** Scegli l'opzione per collegare il numero già usato con l'app WhatsApp Business (*coexistence*): continui a scrivere dal telefono come sempre e il server riceve una copia dei messaggi. Se la procedura non te lo propone per il tuo numero, usa un secondo numero dedicato ai clienti.
3. Annota il **Phone number ID** (stessa pagina).
4. Crea un **token permanente**: Impostazioni business → Utenti di sistema → aggiungi un utente *Admin* → *Genera token* per la tua app con i permessi `whatsapp_business_messaging` e `whatsapp_business_management`.
5. Annota l'**App secret**: nell'app sviluppatore → Impostazioni app → Di base.

Il webhook lo configuri al punto 4, quando il server è online.

## 2. Chiave Claude

Su **console.anthropic.com** → API Keys → crea una chiave e ricarica un po' di credito.
Il modello predefinito è `claude-opus-5-5`; si cambia con `CLAUDE_MODEL` nel file `.env`.

## 3. Installare il server

Sul VPS (con Docker installato):

```bash
git clone <questo repository>
cd whatsapp-assistente
cp .env.example .env
nano .env          # compila token, segreti, password del pannello, chiave Anthropic
docker compose up -d --build
docker compose logs -f   # per vedere cosa succede
```

Il servizio ascolta su `127.0.0.1:8000`. Meta richiede **HTTPS**: il modo più semplice è [Caddy](https://caddyserver.com), che fa il certificato da solo. File `/etc/caddy/Caddyfile`:

```
assistente.tuodominio.it {
    reverse_proxy 127.0.0.1:8000
}
```

Senza Docker: `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt && .venv/bin/uvicorn app.main:app --port 8000`.

Al primo vocale il server scarica il modello di trascrizione (`WHISPER_MODEL=small`, circa 500 MB). Su un server lento usa `base`; se hai CPU in abbondanza `medium` è più preciso con dialetto e rumore.

## 4. Collegare il webhook

Nell'app sviluppatore Meta → **WhatsApp → Configurazione → Webhook**:

- **URL di callback**: `https://assistente.tuodominio.it/webhook`
- **Token di verifica**: lo stesso valore di `WHATSAPP_VERIFY_TOKEN` nel `.env`
- Clicca *Verifica e salva*, poi in **Campi webhook** attiva:
  - `messages` (messaggi dei clienti)
  - `smb_message_echoes` (le risposte che dai dall'app sul telefono, solo con coexistence: servono a Claude per capire se hai già risposto o confermato)

Manda un messaggio di prova al numero aziendale: dopo circa un minuto e mezzo (`ANALYSIS_DEBOUNCE_SECONDS`) la chat compare nel pannello con la sintesi.

## 5. Usarlo ogni giorno

- **Pannello**: `https://assistente.tuodominio.it/` (utente e password da `DASHBOARD_USER` / `DASHBOARD_PASSWORD`). Le chat sono ordinate per importanza; tocca una chat per vedere sintesi, cosa fare, appuntamenti e conversazione con i vocali trascritti.
- **Appuntamenti su Google Calendar**: in Google Calendar (da computer) → *Altri calendari* → **+** → *Da URL* → incolla
  `https://assistente.tuodominio.it/calendario.ics?token=IL_TUO_CALENDAR_TOKEN`.
  Google lo aggiorna da solo (di solito ogni poche ore). Gli appuntamenti non ancora confermati compaiono con `[proposto]` davanti.
  Se correggi un appuntamento dal pannello, l'assistente non lo modificherà più.
- **Avvisi sul tuo WhatsApp** (`OWNER_WHATSAPP`): ricevi un messaggio quando una chat diventa importante o compare/cambia un appuntamento. Con `DIGEST_TIME=19:30` ricevi anche il riepilogo serale.
  ⚠️ Regola di WhatsApp: un'azienda può scrivere testo libero solo a chi le ha scritto nelle ultime 24 ore. Perché gli avvisi arrivino, **manda ogni mattina un messaggio qualsiasi** (es. "buongiorno") dal tuo numero personale al numero aziendale. Se la finestra è scaduta l'avviso non parte (resta comunque tutto nel pannello).

## Costi di Claude

Ogni analisi rilegge gli ultimi 30 giorni della chat (al massimo 150 messaggi). Più messaggi ravvicinati dello stesso cliente producono una sola analisi. Con `claude-opus-5-5` una chat normale costa indicativamente qualche centesimo; per spendere meno puoi impostare `CLAUDE_MODEL=claude-sonnet-5-5` o `CLAUDE_EFFORT=low`. Controlla la spesa reale su console.anthropic.com.

## Privacy

- I vocali vengono trascritti sul tuo server e salvati in `data/media/`.
- Il testo delle chat (con le trascrizioni) viene inviato all'API di Anthropic per l'analisi.
- Tutto è salvato in `data/assistente.db` (SQLite). Fai un backup della cartella `data/`.
- Aggiorna l'informativa privacy indicando che i messaggi sono trattati con strumenti automatici per la gestione delle richieste.

## Struttura

| File | Cosa fa |
|---|---|
| `app/main.py` | server web: webhook, coda dei vocali, analisi ritardata, API del pannello, calendario |
| `app/whatsapp.py` | lettura dei webhook Meta, verifica firma, download vocali, invio avvisi |
| `app/transcribe.py` | trascrizione con faster-whisper |
| `app/analysis.py` | prompt e chiamate a Claude (sintesi, appuntamenti, riepilogo del giorno) |
| `app/calendar_ics.py` | feed `.ics` per Google Calendar |
| `app/db.py` | archivio SQLite |
| `app/static/index.html` | pannello web |

Test: `pip install pytest && pytest -q`
