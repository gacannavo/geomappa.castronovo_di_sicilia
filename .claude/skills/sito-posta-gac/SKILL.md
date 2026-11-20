---
name: sito-posta-gac
description: Gestisce il sito internet di GAC Impianti (web.gacimpianti.com) e la posta del dominio @gacimpianti.com su Aruba. Usala quando Giuseppe chiede di rispondere a mail arrivate su info@ o g.cannavo@, scrivere o rivedere testi del sito, configurare una casella su telefono/PC, risolvere problemi di mail in spam o non consegnate, gestire DNS/sottodominio, o controllare rinnovi di dominio, hosting e caselle.
---

# Sito e posta GAC Impianti

## Contesto fisso
- Azienda: GAC Impianti, titolare Giuseppe Cannavò (impianti elettrici, videosorveglianza, reti, automazioni).
- Dominio: `gacimpianti.com`, registrato e ospitato su **Aruba**.
- Sito: pubblicato sul sottodominio **https://web.gacimpianti.com/**.
- Caselle note: `info@gacimpianti.com` (principale, richieste clienti e fornitori), `g.cannavo@gacimpianti.com` (personale).
- Gmail personale (`gcannavo20@…`) è collegato come connettore, ma la posta del dominio resta su Aruba: in Gmail si trovano solo le mail che Giuseppe ha scambiato con quegli indirizzi.
- Pannello di controllo: app «Sito e Posta GAC» → https://claude.ai/artifact/LaMb8ZcWRPptKqM9FtKJGR
  (database con le collezioni `caselle`, `scadenze`, `pagine`, `attivita`, `richieste`; leggile e scrivile con lo strumento `ArtifactData`).
- Collegato alla «Centrale GAC» (https://claude.ai/artifact/V2dBBEvxsTp2MLs2CvArhZ) come app `sito`: la sincronizzazione oraria scrive lo stato in `stato/sito` ed esegue le richieste confermate con `app: "sito"`.
- Le credenziali stanno nella «Cassaforte GAC» (https://claude.ai/artifact/Q6CPm9zDvnkVbBgkb5t7Nk), cifrata con la password principale di Giuseppe: non leggerla e non scriverci. Giuseppe ha deciso di non cambiare le password: non proporlo.

## Regole
1. **Mai inviare mail.** Prepara il testo o una bozza Gmail (`create_draft`) e lascia che sia Giuseppe a inviarla dalla casella giusta.
2. **Mai chiedere, scrivere o salvare password**, né in chat né nel pannello. Se ne vedi una in una mail, non ripeterla e ricorda che il posto giusto è la Cassaforte GAC.
3. Prima di scrivere nel database del pannello, mostra cosa cambierai e aspetta l'ok.
4. Non inventare prezzi, date di intervento o dati aziendali (P.IVA, indirizzo): chiedili o lasciali come `[da completare]`.
5. Rispondi in italiano, frasi brevi, passi numerati per le procedure.

## Rispondere a una richiesta cliente
1. Fatti incollare la mail (o cercala in Gmail se è passata di lì).
2. Capisci il tipo: preventivo, guasto/assistenza, sopralluogo, fornitore, amministrazione.
3. Scrivi la risposta: saluto, ringraziamento, risposta concreta o domande per le informazioni che mancano (indirizzo, tipo di impianto, foto, recapito telefonico), proposta di sentirsi.
4. Firma:
   ```
   Giuseppe Cannavò
   GAC Impianti
   info@gacimpianti.com · web.gacimpianti.com
   ```
5. Se è un preventivo, proponi di usare la skill dei preventivi GAC; se serve un appuntamento, quella dell'agenda installazioni.
6. Offri di salvare richiesta e bozza nella collezione `richieste` del pannello (campi `mittente`, `oggetto`, `testo`, `bozza`, `stato` = `nuova` | `bozza` | `chiusa`, `ts`).

## Configurare una casella Aruba
| | Server | Porta | Sicurezza |
|---|---|---|---|
| IMAP (consigliato) | imaps.aruba.it | 993 | SSL/TLS |
| POP3 | pop3s.aruba.it | 995 | SSL/TLS |
| SMTP | smtps.aruba.it | 465 | SSL/TLS, autenticazione obbligatoria |
| PEC IMAP | imaps.pec.aruba.it | 993 | SSL/TLS |
| PEC SMTP | smtps.pec.aruba.it | 465 | SSL/TLS |

Nome utente = indirizzo completo. Webmail: webmail.aruba.it (PEC: webmail.pec.it).
Su iPhone: Impostazioni → App → Mail → Account → Aggiungi account → Altro. Su Android/Gmail app: Aggiungi account → Altro → IMAP personale. Su Outlook: Aggiungi account → Opzioni avanzate → IMAP.

## Mail in spam o rifiutate
Controllare nel pannello DNS di Aruba:
- **SPF** sul dominio: `v=spf1 include:_spf.aruba.it ~all` (un solo record SPF; se ci sono altri servizi che inviano a nome del dominio vanno aggiunti nello stesso record).
- **DKIM**: attivarlo dal pannello Aruba della posta.
- **DMARC**: record TXT su `_dmarc.gacimpianti.com`, per iniziare `v=DMARC1; p=none; rua=mailto:info@gacimpianti.com`.
- Che la casella non sia piena e che l'IP non sia in blacklist (i messaggi di errore di ritorno di solito lo dicono).
Prima di dire a Giuseppe di modificare un record, fatti mandare quelli attuali e verifica: un SPF duplicato o sbagliato peggiora le cose.

## Sito web.gacimpianti.com
- Per testi nuovi o rivisti: chiedi la pagina, scrivi titolo, sottotitolo, paragrafi brevi orientati al cliente, invito all'azione (chiamare o scrivere a info@), e una meta description di massimo 155 caratteri.
- Controlli periodici: HTTPS attivo; piè di pagina con ragione sociale, P.IVA, sede e anno corrente; privacy e cookie policy; modulo contatti funzionante (fare una prova e verificare che arrivi su info@); recapiti uguali in tutte le pagine; foto di lavori reali.
- Il sottodominio `web` si gestisce dal DNS Aruba (record A o CNAME). Se il sito viene portato su `gacimpianti.com` o `www`, impostare redirect 301 da `web.gacimpianti.com` per non perdere i link.
- Annota le pagine e cosa cambiare nella collezione `pagine` del pannello (`titolo`, `url`, `stato` = `Aggiornata` | `Da aggiornare` | `Da creare`, `note`).

## Scadenze
- Leggi la collezione `scadenze` (campi `servizio`, `tipo`, `data` YYYY-MM-DD, `costo`, `rinnovoAuto`, `note`) e segnala quelle entro 60 giorni o senza data.
- Le date vere si trovano nell'Area Clienti Aruba o nelle mail di promemoria rinnovo di Aruba (cercale in Gmail con `from:aruba rinnovo OR scadenza`).
- Se Giuseppe vuole un promemoria, proponi un evento nel calendario 30 giorni prima, solo dopo il suo ok.
