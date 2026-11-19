import asyncio
import hashlib
import hmac
import json
import os
import tempfile
import time
from pathlib import Path

_tmp = tempfile.mkdtemp()
os.environ.update({
    "ENV_FILE": "/nonexistent", "DATA_DIR": _tmp, "WHATSAPP_APP_SECRET": "segreto",
    "WHATSAPP_VERIFY_TOKEN": "verifica", "DASHBOARD_PASSWORD": "pw", "CALENDAR_TOKEN": "cal",
    "ANALYSIS_DEBOUNCE_SECONDS": "0.05", "ANTHROPIC_API_KEY": "test",
})

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import analysis, db, main, transcribe, whatsapp  # noqa: E402
from app.calendar_ics import build_ics  # noqa: E402

NOW = int(time.time())


def payload(*messages, field="messages", name="Mario Rossi"):
    key = "messages" if field == "messages" else "message_echoes"
    return {"object": "whatsapp_business_account", "entry": [{"id": "1", "changes": [{
        "field": field,
        "value": {"messaging_product": "whatsapp",
                  "contacts": [{"wa_id": "393331112222", "profile": {"name": name}}],
                  key: list(messages)}}]}]}


def text_msg(mid, body, frm="393331112222"):
    return {"from": frm, "id": mid, "timestamp": str(NOW), "type": "text", "text": {"body": body}}


def audio_msg(mid):
    return {"from": "393331112222", "id": mid, "timestamp": str(NOW), "type": "audio",
            "audio": {"id": "MEDIA1", "mime_type": "audio/ogg; codecs=opus", "voice": True}}


def sign(body: bytes) -> str:
    return "sha256=" + hmac.new(b"segreto", body, hashlib.sha256).hexdigest()


@pytest.fixture()
def client(monkeypatch):
    for f in Path(_tmp).glob("assistente.db*"):
        f.unlink()
    calls = []

    async def fake_analyze(wa_id):
        calls.append(wa_id)
        res = analysis.AnalisiChat(
            sintesi="Chiede un sopralluogo per l'allarme.", importanza="alta",
            categoria="appuntamento", serve_risposta=True, prossima_azione="Conferma l'orario",
            appuntamenti=[])
        db.save_analysis(wa_id, res.sintesi, res.importanza, res.categoria, True, res.prossima_azione)
        return res, []

    monkeypatch.setattr(analysis, "analyze_conversation", fake_analyze)
    with TestClient(main.app) as c:
        c.calls = calls
        yield c


def post(client, data):
    body = json.dumps(data).encode()
    return client.post("/webhook", content=body, headers={"x-hub-signature-256": sign(body),
                                                          "content-type": "application/json"})


def test_parse_message_types():
    msgs = whatsapp.parse_webhook(payload(
        text_msg("a", "Buongiorno"), audio_msg("b"),
        {"from": "393331112222", "id": "c", "timestamp": "1", "type": "image",
         "image": {"id": "IMG", "caption": "il quadro elettrico"}},
        {"from": "393331112222", "id": "d", "timestamp": "1", "type": "reaction",
         "reaction": {"emoji": "👍"}}))
    assert [m["id"] for m in msgs] == ["a", "b", "c"]
    assert msgs[0]["text"] == "Buongiorno" and msgs[0]["name"] == "Mario Rossi"
    assert msgs[1]["status"] == "pending" and msgs[1]["media_id"] == "MEDIA1"
    assert msgs[2]["text"] == "[foto] il quadro elettrico"


def test_parse_echoes_are_outgoing():
    echo = {"from": "39091000000", "to": "393331112222", "id": "e1", "timestamp": "1",
            "type": "text", "text": {"body": "Va bene giovedì alle 9"}}
    [m] = whatsapp.parse_webhook(payload(echo, field="smb_message_echoes"))
    assert m["direction"] == "out" and m["wa_id"] == "393331112222"


def test_signature():
    assert whatsapp.verify_signature(b"x", sign(b"x"), "segreto")
    assert not whatsapp.verify_signature(b"x", sign(b"y"), "segreto")
    assert not whatsapp.verify_signature(b"x", None, "segreto")


def test_webhook_verification(client):
    ok = client.get("/webhook", params={"hub.mode": "subscribe", "hub.verify_token": "verifica",
                                        "hub.challenge": "123"})
    assert ok.status_code == 200 and ok.text == "123"
    assert client.get("/webhook", params={"hub.mode": "subscribe", "hub.verify_token": "no",
                                          "hub.challenge": "1"}).status_code == 403


def test_webhook_rejects_bad_signature(client):
    r = client.post("/webhook", content=b"{}", headers={"x-hub-signature-256": "sha256=00"})
    assert r.status_code == 401


def test_text_message_is_stored_and_analyzed_once(client):
    assert post(client, payload(text_msg("m1", "Salve"), text_msg("m2", "posso avere un preventivo?"))).status_code == 200
    assert post(client, payload(text_msg("m1", "Salve"))).status_code == 200  # duplicato
    time.sleep(0.3)
    assert len(db.get_messages("393331112222")) == 2
    assert client.calls == ["393331112222"]  # debounce: una sola analisi
    contacts = client.get("/api/contacts", auth=("admin", "pw")).json()
    assert contacts[0]["importance"] == "alta" and contacts[0]["name"] == "Mario Rossi"


def test_voice_message_transcribed(client, monkeypatch):
    async def fake_download(media_id, dest):
        p = Path(dest) / f"{media_id}.ogg"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"OggS")
        return p

    async def fake_transcribe(path):
        return "Volevo sapere se giovedì mattina potete passare"

    monkeypatch.setattr(whatsapp, "download_media", fake_download)
    monkeypatch.setattr(transcribe, "transcribe", fake_transcribe)
    post(client, payload(audio_msg("v1")))
    time.sleep(0.4)
    [m] = db.get_messages("393331112222")
    assert m["status"] == "ok" and m["transcript"].startswith("Volevo sapere")
    assert client.calls == ["393331112222"]
    assert client.get("/media/v1", auth=("admin", "pw")).status_code == 200


def test_dashboard_requires_login(client):
    assert client.get("/").status_code == 401
    assert client.get("/", auth=("admin", "sbagliata")).status_code == 401
    assert "Assistente" in client.get("/", auth=("admin", "pw")).text


def test_apply_appointments_create_update_and_manual(client):
    db.upsert_contact("39111", "Anna", NOW)
    A = analysis.Appuntamento
    changed = analysis.apply_appointments("39111", [A(
        id_esistente=None, titolo="Sopralluogo", inizio="2026-11-26T09:00", fine=None,
        luogo="Via Roma 1", note=None, stato="proposto")], [])
    assert changed[0]["novita"] == "nuovo"
    [appt] = db.list_appointments("39111")

    changed = analysis.apply_appointments("39111", [A(
        id_esistente=appt["id"], titolo="Sopralluogo", inizio="2026-11-26T09:00", fine=None,
        luogo="Via Roma 1", note=None, stato="confermato")], db.list_appointments("39111"))
    assert changed[0]["status"] == "confermato"

    db.update_appointment(appt["id"], {"start": "2026-11-27T10:00"}, manual=True)
    changed = analysis.apply_appointments("39111", [A(
        id_esistente=appt["id"], titolo="Altro", inizio="2026-11-26T09:00", fine=None,
        luogo=None, note=None, stato="annullato")], db.list_appointments("39111"))
    assert changed == [] and db.list_appointments("39111")[0]["start"] == "2026-11-27T10:00"


def test_ics_feed(client):
    db.upsert_contact("39222", "Luca", NOW)
    db.create_appointment("39222", {"title": "Installazione telecamere, piano 2", "start": "2026-12-01T08:30",
                                    "location": "Castronovo", "status": "confermato"})
    db.create_appointment("39222", {"title": "Consegna", "start": "2026-12-02"})
    assert client.get("/calendario.ics?token=no").status_code == 403
    ics = client.get("/calendario.ics?token=cal").text
    assert "DTSTART;TZID=Europe/Rome:20261201T083000" in ics
    assert "SUMMARY:Installazione telecamere\\, piano 2" in ics
    assert "DTSTART;VALUE=DATE:20261202" in ics and "[proposto] Consegna" in ics


def test_ics_folding():
    ics = build_ics([{"id": 1, "wa_id": "1", "title": "x" * 200, "start": "2026-01-01",
                      "status": "confermato"}], "Europe/Rome")
    assert all(len(line.encode()) <= 75 for line in ics.split("\r\n"))


def test_format_conversation():
    text = analysis.format_conversation([
        {"direction": "in", "type": "audio", "transcript": "ciao", "timestamp": NOW},
        {"direction": "out", "type": "text", "text": "Buongiorno", "timestamp": NOW}], "Anna")
    assert "Anna (vocale): ciao" in text and "Titolare: Buongiorno" in text
