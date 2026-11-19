"""Integrazione con la WhatsApp Business Cloud API di Meta."""

from __future__ import annotations

import hashlib
import hmac
import logging
import mimetypes
from pathlib import Path
from typing import Any

import httpx

from .config import settings

log = logging.getLogger(__name__)


def verify_signature(body: bytes, signature_header: str | None, app_secret: str) -> bool:
    """Controlla l'intestazione X-Hub-Signature-256 inviata da Meta."""
    if not app_secret:
        # Senza app secret non si puo' verificare: accettato solo per prove in locale.
        log.warning("WHATSAPP_APP_SECRET non impostato: firma del webhook NON verificata")
        return True
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = hmac.new(app_secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature_header.removeprefix("sha256="))


def _describe(msg: dict[str, Any]) -> tuple[str | None, str | None]:
    """Ritorna (testo leggibile, media_id) per ogni tipo di messaggio."""
    t = msg.get("type")
    body = msg.get(t) if isinstance(msg.get(t), dict) else {}
    if t == "text":
        return body.get("body"), None
    if t == "audio":
        return None, body.get("id")
    if t in ("image", "video", "sticker"):
        caption = body.get("caption")
        label = {"image": "foto", "video": "video", "sticker": "sticker"}[t]
        return f"[{label}]" + (f" {caption}" if caption else ""), body.get("id")
    if t == "document":
        name = body.get("filename") or "documento"
        caption = body.get("caption")
        return f"[documento: {name}]" + (f" {caption}" if caption else ""), body.get("id")
    if t == "location":
        parts = [body.get("name"), body.get("address"),
                 f"({body.get('latitude')}, {body.get('longitude')})"]
        return "[posizione] " + " ".join(p for p in parts if p), None
    if t == "contacts":
        names = [c.get("name", {}).get("formatted_name", "") for c in msg.get("contacts", [])]
        return "[contatto condiviso] " + ", ".join(n for n in names if n), None
    if t == "button":
        return body.get("text"), None
    if t == "interactive":
        reply = body.get("button_reply") or body.get("list_reply") or {}
        return reply.get("title"), None
    if t == "reaction":
        return f"[reazione {body.get('emoji', '')}]", None
    return f"[messaggio di tipo {t}]", None


def parse_webhook(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Trasforma il JSON del webhook in una lista di messaggi normalizzati.

    Gestisce sia i messaggi ricevuti (campo "messages") sia, se il numero e' usato anche
    dall'app WhatsApp Business (coexistence), le risposte che invii tu dal telefono
    (campo "smb_message_echoes"), cosi' l'analisi vede tutta la conversazione.
    """
    out: list[dict[str, Any]] = []
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            field = change.get("field")
            value = change.get("value", {})
            names = {c.get("wa_id"): c.get("profile", {}).get("name")
                     for c in value.get("contacts", [])}

            if field == "messages":
                items, direction = value.get("messages", []), "in"
            elif field == "smb_message_echoes":
                items, direction = value.get("message_echoes", []), "out"
            else:
                continue

            for m in items:
                if m.get("type") in ("reaction", "unsupported", "system"):
                    continue
                wa_id = m.get("from") if direction == "in" else m.get("to")
                if not wa_id or not m.get("id"):
                    continue
                text, media_id = _describe(m)
                out.append({
                    "id": m["id"],
                    "wa_id": wa_id,
                    "name": names.get(wa_id),
                    "direction": direction,
                    "type": m.get("type", "unknown"),
                    "text": text,
                    "media_id": media_id,
                    "status": "pending" if m.get("type") == "audio" else "ok",
                    "timestamp": int(m.get("timestamp", 0)),
                })
    return out


def _graph(path: str) -> str:
    return f"https://graph.facebook.com/{settings.graph_api_version}/{path}"


def _auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {settings.whatsapp_token}"}


async def download_media(media_id: str, dest_dir: Path) -> Path:
    """Scarica un allegato (es. un vocale .ogg) e ritorna il percorso del file."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    async with httpx.AsyncClient(timeout=60) as client:
        meta = await client.get(_graph(media_id), headers=_auth())
        meta.raise_for_status()
        info = meta.json()
        ext = mimetypes.guess_extension((info.get("mime_type") or "").split(";")[0].strip()) or ".ogg"
        if ext == ".oga":
            ext = ".ogg"
        resp = await client.get(info["url"], headers=_auth(), follow_redirects=True)
        resp.raise_for_status()
    path = dest_dir / f"{media_id}{ext}"
    path.write_bytes(resp.content)
    return path


async def send_text(to: str, text: str) -> bool:
    """Invia un messaggio di testo. Funziona solo se il destinatario ti ha scritto
    nelle ultime 24 ore (regola di WhatsApp per i messaggi non-template)."""
    if not (settings.whatsapp_phone_number_id and settings.whatsapp_token):
        return False
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            _graph(f"{settings.whatsapp_phone_number_id}/messages"),
            headers=_auth(),
            json={"messaging_product": "whatsapp", "to": to, "type": "text",
                  "text": {"body": text[:4000]}},
        )
    if resp.status_code >= 400:
        log.warning("Invio WhatsApp a %s fallito: %s %s", to, resp.status_code, resp.text[:300])
        return False
    return True
