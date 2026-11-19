"""Analisi delle chat con Claude: sintesi, priorita', appuntamenti, riepilogo del giorno."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Literal, Optional
from zoneinfo import ZoneInfo

import anthropic
from pydantic import BaseModel, Field

from . import db
from .config import settings

log = logging.getLogger(__name__)

# Modelli che accettano il fallback lato server in caso di rifiuto per policy.
_FALLBACK_MODELS = {"claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5",
                    "claude-fable-5-2", "claude-fable-5-1"}
_FALLBACK_BETA = "server-side-fallback-2026-07-01"

_client: anthropic.AsyncAnthropic | None = None


def client() -> anthropic.AsyncAnthropic:
    global _client
    if _client is None:
        _client = anthropic.AsyncAnthropic()  # legge ANTHROPIC_API_KEY
    return _client


def _common_params() -> dict[str, Any]:
    params: dict[str, Any] = {
        "model": settings.claude_model,
        "output_config": {"effort": settings.claude_effort},
    }
    if settings.claude_model in _FALLBACK_MODELS:
        params["betas"] = [_FALLBACK_BETA]
        params["fallbacks"] = "default"
    return params


# --- schema della risposta ----------------------------------------------------

class Appuntamento(BaseModel):
    id_esistente: Optional[int] = Field(
        description="ID di un appuntamento gia' in elenco se questo lo aggiorna, altrimenti null")
    titolo: str = Field(description="Breve: cosa si fa e per chi, es. 'Sopralluogo impianto allarme - Rossi'")
    inizio: Optional[str] = Field(
        description="Data e ora locali 'AAAA-MM-GGTHH:MM', solo data 'AAAA-MM-GG' se l'ora non e' "
                    "stata detta, null se nemmeno il giorno e' chiaro")
    fine: Optional[str] = Field(description="Stesso formato di inizio, null se non indicata")
    luogo: Optional[str] = Field(description="Indirizzo o luogo, null se non indicato")
    note: Optional[str] = Field(description="Dettagli utili (cosa portare, citofono, referente...)")
    stato: Literal["proposto", "confermato", "annullato", "fatto"]


class AnalisiChat(BaseModel):
    sintesi: str = Field(description="2-5 frasi: chi e' il cliente, cosa chiede, a che punto siamo")
    importanza: Literal["alta", "media", "bassa"]
    categoria: Literal["preventivo", "assistenza/guasto", "appuntamento", "pagamento",
                       "fornitore", "informazioni", "personale", "altro"]
    serve_risposta: bool = Field(description="True se il cliente attende ancora una risposta dal titolare")
    prossima_azione: Optional[str] = Field(
        description="Cosa deve fare il titolare, in una frase imperativa; null se nulla")
    appuntamenti: list[Appuntamento] = Field(
        description="Tutti gli appuntamenti emersi nella chat, compresi quelli gia' in elenco "
                    "(aggiornati) e quelli annullati")


SYSTEM_PROMPT = """Sei l'assistente WhatsApp Business del titolare di {business}.
Ricevi la trascrizione di una conversazione WhatsApp con un cliente (i vocali sono gia' \
trascritti automaticamente e possono contenere piccoli errori di riconoscimento).

Il tuo compito e' aiutare il titolare a non perdere nulla:
- riassumi la conversazione in italiano semplice e concreto, mettendo in evidenza richieste, \
importi, scadenze, indirizzi e problemi;
- valuta l'importanza: "alta" per guasti urgenti, clienti che aspettano una risposta da tempo, \
preventivi o pagamenti da gestire, appuntamenti da confermare a breve; "media" per richieste \
normali; "bassa" per saluti, ringraziamenti, messaggi gia' chiusi;
- estrai ogni appuntamento (sopralluoghi, interventi, consegne, chiamate fissate). Converti le \
date relative ("domani", "giovedi' prossimo") usando la data di oggi indicata. Se un appuntamento \
e' gia' nell'elenco fornito, riporta il suo id in id_esistente e aggiornane i dati invece di \
crearne uno nuovo. Segna "confermato" solo se entrambe le parti hanno detto si'.

Non inventare dati che non compaiono nella chat."""


_WEEKDAYS = ["lunedi'", "martedi'", "mercoledi'", "giovedi'", "venerdi'", "sabato", "domenica"]


def _now() -> datetime:
    return datetime.now(ZoneInfo(settings.timezone))


def _fmt_ts(ts: int) -> str:
    d = datetime.fromtimestamp(ts, ZoneInfo(settings.timezone))
    return f"{_WEEKDAYS[d.weekday()][:3]} {d:%d/%m %H:%M}"


def format_conversation(messages: list[dict[str, Any]], contact_name: str | None) -> str:
    cliente = contact_name or "Cliente"
    lines = []
    for m in messages:
        who = cliente if m["direction"] == "in" else "Titolare"
        if m["type"] == "audio":
            body = m.get("transcript") or "(vocale non ancora trascritto)"
            lines.append(f"[{_fmt_ts(m['timestamp'])}] {who} (vocale): {body}")
        else:
            lines.append(f"[{_fmt_ts(m['timestamp'])}] {who}: {m.get('text') or ''}")
    return "\n".join(lines)


def _today_line() -> str:
    now = _now()
    return f"Oggi e' {_WEEKDAYS[now.weekday()]} {now:%d/%m/%Y}, ore {now:%H:%M} ({settings.timezone})."


async def analyze_conversation(wa_id: str) -> tuple[AnalisiChat, list[dict[str, Any]]]:
    """Analizza la chat e aggiorna l'archivio. Ritorna l'analisi e gli appuntamenti
    nuovi o cambiati (per le notifiche)."""
    contact = db.get_contact(wa_id) or {"wa_id": wa_id, "name": None}
    since = int((_now() - timedelta(days=30)).timestamp())
    messages = db.get_messages(wa_id, limit=150, since=since)
    if not messages:
        raise ValueError("nessun messaggio da analizzare")

    existing = db.list_appointments(wa_id)
    if existing:
        elenco = "\n".join(
            f"- id {a['id']}: {a['title']} | {a['start'] or 'data da definire'} | "
            f"{a['location'] or '-'} | {a['status']}" for a in existing)
    else:
        elenco = "(nessuno)"

    user_content = (
        f"{_today_line()}\n\n"
        f"Cliente: {contact.get('name') or 'sconosciuto'} (+{wa_id})\n\n"
        f"Appuntamenti gia' registrati con questo cliente:\n{elenco}\n\n"
        f"<conversazione>\n{format_conversation(messages, contact.get('name'))}\n</conversazione>"
    )

    response = await client().beta.messages.parse(
        max_tokens=16000,
        system=SYSTEM_PROMPT.format(business=settings.business_description),
        messages=[{"role": "user", "content": user_content}],
        output_format=AnalisiChat,
        **_common_params(),
    )
    if response.stop_reason == "refusal":
        raise RuntimeError("Claude ha rifiutato l'analisi di questa chat")
    analysis = response.parsed_output
    if analysis is None:
        raise RuntimeError(f"risposta non valida (stop_reason={response.stop_reason})")

    db.save_analysis(wa_id, analysis.sintesi, analysis.importanza, analysis.categoria,
                     analysis.serve_risposta, analysis.prossima_azione)
    changed = apply_appointments(wa_id, analysis.appuntamenti, existing)
    return analysis, changed


def apply_appointments(wa_id: str, found: list[Appuntamento],
                       existing: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Crea/aggiorna gli appuntamenti. Quelli modificati a mano non vengono toccati."""
    by_id = {a["id"]: a for a in existing}
    by_start = {a["start"]: a for a in existing if a["start"]}
    changed: list[dict[str, Any]] = []
    for ap in found:
        data = {"title": ap.titolo, "start": ap.inizio, "end": ap.fine,
                "location": ap.luogo, "notes": ap.note, "status": ap.stato}
        old = by_id.get(ap.id_esistente) if ap.id_esistente else by_start.get(ap.inizio)
        if old is None:
            if ap.stato == "annullato":
                continue  # annullato prima ancora di essere registrato
            new_id = db.create_appointment(wa_id, data)
            changed.append({**data, "id": new_id, "novita": "nuovo"})
            continue
        if old["manual"]:
            continue
        diff = {k: v for k, v in data.items() if v is not None and v != old.get(k)}
        if diff:
            db.update_appointment(old["id"], diff)
            changed.append({**old, **diff, "novita": "aggiornato"})
    return changed


# --- riepilogo del giorno -----------------------------------------------------

DIGEST_PROMPT = """Sei l'assistente WhatsApp Business del titolare di {business}.
Scrivi il riepilogo della giornata in italiano, pensato per essere letto sul telefono in un minuto:
1. "Da fare subito": clienti che aspettano risposta o hanno urgenze, con la cosa da fare.
2. "Appuntamenti": quelli fissati, spostati o annullati oggi, con data, ora e luogo.
3. "Altre chat": una riga per ogni altra conversazione rilevante.
Ignora saluti e messaggi senza contenuto. Usa elenchi puntati brevi, niente preamboli."""


async def daily_digest(day: datetime) -> str:
    tz = ZoneInfo(settings.timezone)
    start = datetime(day.year, day.month, day.day, tzinfo=tz)
    end = start + timedelta(days=1)
    msgs = db.messages_between(int(start.timestamp()), int(end.timestamp()))
    if not msgs:
        return "Nessun messaggio ricevuto in questa giornata."

    blocks: list[str] = []
    by_contact: dict[str, list[dict[str, Any]]] = {}
    for m in msgs:
        by_contact.setdefault(m["wa_id"], []).append(m)
    for wa_id, items in by_contact.items():
        name = items[0].get("name")
        contact = db.get_contact(wa_id) or {}
        blocks.append(
            f"<chat cliente=\"{name or wa_id}\" importanza=\"{contact.get('importance') or '?'}\">\n"
            f"{format_conversation(items, name)}\n</chat>")
    appts = [a for a in db.list_appointments(include_closed=True)
             if a["updated_at"] and start.timestamp() <= a["updated_at"] < end.timestamp()]
    appt_text = "\n".join(
        f"- {a['contact_name'] or a['wa_id']}: {a['title']} | {a['start'] or 'da definire'} | "
        f"{a['location'] or '-'} | {a['status']}" for a in appts) or "(nessuno)"

    async with client().beta.messages.stream(
        max_tokens=16000,
        system=DIGEST_PROMPT.format(business=settings.business_description),
        messages=[{"role": "user", "content":
                   f"Giornata: {_WEEKDAYS[start.weekday()]} {start:%d/%m/%Y}\n\nAppuntamenti toccati oggi:\n{appt_text}\n\n"
                   + "\n\n".join(blocks)}],
        **_common_params(),
    ) as stream:
        response = await stream.get_final_message()
    if response.stop_reason == "refusal":
        raise RuntimeError("Claude ha rifiutato di generare il riepilogo")
    text = "".join(b.text for b in response.content if b.type == "text").strip()
    db.save_digest(f"{start:%Y-%m-%d}", text)
    return text

