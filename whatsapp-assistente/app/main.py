"""Server dell'assistente WhatsApp Business.

Flusso: Meta chiama POST /webhook -> il messaggio viene salvato -> i vocali vengono
scaricati e trascritti -> dopo qualche secondo di silenzio la chat viene analizzata da
Claude (sintesi, priorita', appuntamenti) -> tutto e' consultabile dal pannello web.
"""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel

from . import analysis, db, transcribe, whatsapp
from .calendar_ics import build_ics
from .config import settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("assistente")

STATIC = Path(__file__).parent / "static"


# --- elaborazione in background -----------------------------------------------

class Pipeline:
    """Coda dei vocali da trascrivere + analisi 'ritardata' per ogni chat.

    L'analisi parte solo dopo ANALYSIS_DEBOUNCE_SECONDS senza nuovi messaggi, cosi' una
    raffica di 5 messaggi produce una sola chiamata a Claude.
    """

    def __init__(self) -> None:
        self.audio_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._timers: dict[str, asyncio.Task] = {}
        self._running: set[str] = set()
        self._rerun: set[str] = set()
        self._tasks: list[asyncio.Task] = []

    def start(self) -> None:
        # Creati qui, dentro l'event loop del server.
        self.audio_queue = asyncio.Queue()
        for s in (self._timers, self._running, self._rerun):
            s.clear()
        self._tasks.clear()
        self._tasks.append(asyncio.create_task(self._audio_worker()))
        if settings.digest_time:
            self._tasks.append(asyncio.create_task(self._digest_scheduler()))
        for m in db.pending_audio():  # vocali rimasti in sospeso da un riavvio
            self.audio_queue.put_nowait(m)

    async def stop(self) -> None:
        for t in [*self._tasks, *self._timers.values()]:
            t.cancel()

    # vocali
    async def _audio_worker(self) -> None:
        while True:
            msg = await self.audio_queue.get()
            try:
                path = Path(msg["media_path"]) if msg.get("media_path") else None
                if path is None or not path.exists():
                    path = await whatsapp.download_media(msg["media_id"], settings.media_dir)
                    db.update_message(msg["id"], media_path=str(path))
                text = await transcribe.transcribe(path)
                db.update_message(msg["id"], transcript=text, status="ok", error=None)
                log.info("Vocale %s trascritto (%d caratteri)", msg["id"], len(text))
            except Exception as e:  # noqa: BLE001 - il worker non deve mai fermarsi
                log.exception("Trascrizione fallita per %s", msg["id"])
                db.update_message(msg["id"], status="error", error=str(e)[:500])
            finally:
                self.schedule_analysis(msg["wa_id"])
                self.audio_queue.task_done()

    # analisi
    def schedule_analysis(self, wa_id: str, delay: float | None = None) -> None:
        if wa_id in self._running:
            self._rerun.add(wa_id)
            return
        old = self._timers.get(wa_id)
        if old and not old.done():
            old.cancel()
        self._timers[wa_id] = asyncio.create_task(
            self._analyze_later(wa_id, settings.debounce_seconds if delay is None else delay))

    async def _analyze_later(self, wa_id: str, delay: float) -> None:
        await asyncio.sleep(delay)
        if any(m["wa_id"] == wa_id for m in db.pending_audio()):
            return  # ripartira' quando il vocale sara' trascritto
        self._running.add(wa_id)
        try:
            await self.analyze_now(wa_id)
        finally:
            self._running.discard(wa_id)
            if wa_id in self._rerun:
                self._rerun.discard(wa_id)
                self.schedule_analysis(wa_id)

    async def analyze_now(self, wa_id: str) -> dict[str, Any]:
        before = db.get_contact(wa_id) or {}
        try:
            result, changed = await analysis.analyze_conversation(wa_id)
        except Exception as e:  # noqa: BLE001
            log.exception("Analisi fallita per %s", wa_id)
            return {"ok": False, "error": str(e)}
        await notify_owner(wa_id, before, result, changed)
        return {"ok": True, "analysis": result.model_dump(), "changed": changed}

    async def _digest_scheduler(self) -> None:
        tz = ZoneInfo(settings.timezone)
        hh, mm = (int(x) for x in settings.digest_time.split(":"))
        while True:
            now = datetime.now(tz)
            target = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
            if target <= now:
                target += timedelta(days=1)
            await asyncio.sleep((target - now).total_seconds())
            try:
                text = await analysis.daily_digest(target)
                if settings.owner_whatsapp:
                    await whatsapp.send_text(settings.owner_whatsapp,
                                             f"📋 Riepilogo WhatsApp del {target:%d/%m}\n\n{text}")
            except Exception:  # noqa: BLE001
                log.exception("Riepilogo serale fallito")


async def notify_owner(wa_id: str, before: dict[str, Any], result: analysis.AnalisiChat,
                       changed: list[dict[str, Any]]) -> None:
    """Avvisa il titolare su WhatsApp per chat che diventano urgenti o appuntamenti nuovi."""
    if not settings.owner_whatsapp or wa_id == settings.owner_whatsapp:
        return
    contact = db.get_contact(wa_id) or {}
    name = contact.get("name") or f"+{wa_id}"
    lines: list[str] = []
    if result.importanza == "alta" and before.get("importance") != "alta":
        lines.append(f"🔴 Chat importante: {name}\n{result.sintesi}")
        if result.prossima_azione:
            lines.append(f"👉 {result.prossima_azione}")
    for a in changed:
        when = a.get("start") or "data da definire"
        lines.append(f"📅 Appuntamento {a['novita']} ({a.get('status')}): {a.get('title')} - "
                     f"{when.replace('T', ' ore ')}" + (f" - {a['location']}" if a.get("location") else ""))
    if lines:
        await whatsapp.send_text(settings.owner_whatsapp, "\n".join(lines))


pipeline = Pipeline()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    db.init_db()
    pipeline.start()
    yield
    await pipeline.stop()


app = FastAPI(title="Assistente WhatsApp Business", lifespan=lifespan)


# --- webhook Meta ---------------------------------------------------------------

@app.get("/webhook")
async def verify_webhook(
    mode: str = Query("", alias="hub.mode"),
    token: str = Query("", alias="hub.verify_token"),
    challenge: str = Query("", alias="hub.challenge"),
):
    if mode == "subscribe" and settings.whatsapp_verify_token and secrets.compare_digest(
            token, settings.whatsapp_verify_token):
        return PlainTextResponse(challenge)
    raise HTTPException(403, "verify token errato")


@app.post("/webhook")
async def receive_webhook(request: Request):
    body = await request.body()
    if not whatsapp.verify_signature(body, request.headers.get("x-hub-signature-256"),
                                     settings.whatsapp_app_secret):
        raise HTTPException(401, "firma non valida")
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        raise HTTPException(400, "JSON non valido")
    handle_payload(payload)
    return {"ok": True}  # rispondere subito: Meta riprova se tardiamo


def handle_payload(payload: dict[str, Any]) -> int:
    count = 0
    for msg in whatsapp.parse_webhook(payload):
        db.upsert_contact(msg["wa_id"], msg["name"] if msg["direction"] == "in" else None,
                          msg["timestamp"])
        if not db.insert_message(msg):
            continue  # duplicato
        count += 1
        if msg["type"] == "audio" and msg["media_id"]:
            pipeline.audio_queue.put_nowait(msg)
        else:
            pipeline.schedule_analysis(msg["wa_id"])
    return count


# --- pannello web ---------------------------------------------------------------

security = HTTPBasic()


def require_login(creds: HTTPBasicCredentials = Depends(security)) -> None:
    if not settings.dashboard_password:
        raise HTTPException(503, "Imposta DASHBOARD_PASSWORD nel file .env")
    ok = secrets.compare_digest(creds.username, settings.dashboard_user) and \
        secrets.compare_digest(creds.password, settings.dashboard_password)
    if not ok:
        raise HTTPException(401, "Credenziali errate", headers={"WWW-Authenticate": "Basic"})


@app.get("/health")
async def health():
    return {"ok": True, "vocali_in_coda": pipeline.audio_queue.qsize()}


@app.get("/", response_class=HTMLResponse, dependencies=[Depends(require_login)])
async def dashboard():
    return HTMLResponse((STATIC / "index.html").read_text(encoding="utf-8"))


@app.get("/api/contacts", dependencies=[Depends(require_login)])
async def api_contacts(archiviati: bool = False):
    return db.list_contacts(include_archived=archiviati)


@app.get("/api/contacts/{wa_id}", dependencies=[Depends(require_login)])
async def api_contact(wa_id: str):
    contact = db.get_contact(wa_id)
    if not contact:
        raise HTTPException(404)
    return {"contact": contact, "messages": db.get_messages(wa_id, limit=500),
            "appointments": db.list_appointments(wa_id)}


class ContactPatch(BaseModel):
    archived: Optional[bool] = None
    needs_reply: Optional[bool] = None


@app.patch("/api/contacts/{wa_id}", dependencies=[Depends(require_login)])
async def api_contact_patch(wa_id: str, patch: ContactPatch):
    db.set_contact_flags(wa_id, archived=patch.archived, needs_reply=patch.needs_reply)
    return db.get_contact(wa_id)


@app.post("/api/contacts/{wa_id}/analizza", dependencies=[Depends(require_login)])
async def api_analyze(wa_id: str):
    if not db.get_contact(wa_id):
        raise HTTPException(404)
    return await pipeline.analyze_now(wa_id)


@app.get("/media/{msg_id}", dependencies=[Depends(require_login)])
async def media(msg_id: str):
    with db.connect() as c:
        r = c.execute("SELECT media_path FROM messages WHERE id = ?", (msg_id,)).fetchone()
    if not r or not r["media_path"] or not Path(r["media_path"]).exists():
        raise HTTPException(404)
    return FileResponse(r["media_path"])


class AppointmentIn(BaseModel):
    wa_id: Optional[str] = None
    title: Optional[str] = None
    start: Optional[str] = None
    end: Optional[str] = None
    location: Optional[str] = None
    notes: Optional[str] = None
    status: Optional[str] = None


@app.get("/api/appointments", dependencies=[Depends(require_login)])
async def api_appointments(tutti: bool = False):
    return db.list_appointments(include_closed=tutti)


@app.post("/api/appointments", dependencies=[Depends(require_login)])
async def api_appointment_create(a: AppointmentIn):
    if not a.wa_id or not a.title:
        raise HTTPException(400, "servono wa_id e title")
    db.upsert_contact(a.wa_id, None, 0)
    new_id = db.create_appointment(a.wa_id, a.model_dump(exclude_none=True), manual=True)
    return {"id": new_id}


@app.patch("/api/appointments/{appt_id}", dependencies=[Depends(require_login)])
async def api_appointment_update(appt_id: int, a: AppointmentIn):
    if not db.update_appointment(appt_id, a.model_dump(exclude_none=True, exclude={"wa_id"}),
                                 manual=True):
        raise HTTPException(404)
    return {"ok": True}


@app.delete("/api/appointments/{appt_id}", dependencies=[Depends(require_login)])
async def api_appointment_delete(appt_id: int):
    db.delete_appointment(appt_id)
    return {"ok": True}


def _parse_day(day: str | None) -> date:
    if not day:
        return datetime.now(ZoneInfo(settings.timezone)).date()
    try:
        return date.fromisoformat(day)
    except ValueError:
        raise HTTPException(400, "data nel formato AAAA-MM-GG")


@app.get("/api/digest", dependencies=[Depends(require_login)])
async def api_digest(day: Optional[str] = None):
    d = _parse_day(day)
    return db.get_digest(d.isoformat()) or {"day": d.isoformat(), "text": None}


@app.post("/api/digest", dependencies=[Depends(require_login)])
async def api_digest_create(day: Optional[str] = None, invia: bool = False):
    d = _parse_day(day)
    try:
        text = await analysis.daily_digest(datetime(d.year, d.month, d.day))
    except Exception as e:  # noqa: BLE001
        log.exception("Riepilogo fallito")
        raise HTTPException(502, f"Riepilogo non riuscito: {e}")
    sent = False
    if invia and settings.owner_whatsapp:
        sent = await whatsapp.send_text(settings.owner_whatsapp,
                                        f"📋 Riepilogo WhatsApp del {d:%d/%m}\n\n{text}")
    return {"day": d.isoformat(), "text": text, "inviato": sent}


# --- calendario da abbonare (Google Calendar, iPhone, Outlook) -----------------

@app.get("/calendario.ics")
async def calendar_feed(token: str = ""):
    if not settings.calendar_token or not secrets.compare_digest(token, settings.calendar_token):
        raise HTTPException(403)
    ics = build_ics(db.list_appointments(include_closed=True), settings.timezone)
    return Response(ics, media_type="text/calendar; charset=utf-8")
