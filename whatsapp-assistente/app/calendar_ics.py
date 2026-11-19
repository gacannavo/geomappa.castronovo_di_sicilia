"""Genera un feed iCalendar (.ics) degli appuntamenti, da aggiungere a Google Calendar."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any


def _esc(text: str | None) -> str:
    return (text or "").replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def _fold(line: str) -> str:
    """Le righe iCalendar non devono superare 75 byte."""
    out, cur = [], ""
    for ch in line:
        if len((cur + ch).encode()) > 74:
            out.append(cur)
            cur = " " + ch
        else:
            cur += ch
    out.append(cur)
    return "\r\n".join(out)


def _dt_lines(start: str, end: str | None, tz: str) -> list[str] | None:
    try:
        if "T" in start:
            s = datetime.fromisoformat(start)
            e = datetime.fromisoformat(end) if end and "T" in end else s + timedelta(hours=1)
            if e <= s:
                e = s + timedelta(hours=1)
            return [f"DTSTART;TZID={tz}:{s:%Y%m%dT%H%M%S}", f"DTEND;TZID={tz}:{e:%Y%m%dT%H%M%S}"]
        d = date.fromisoformat(start[:10])
        return [f"DTSTART;VALUE=DATE:{d:%Y%m%d}", f"DTEND;VALUE=DATE:{d + timedelta(days=1):%Y%m%d}"]
    except ValueError:
        return None


def build_ics(appointments: list[dict[str, Any]], tz: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Assistente WhatsApp//IT",
             "CALSCALE:GREGORIAN", "X-WR-CALNAME:Appuntamenti WhatsApp", f"X-WR-TIMEZONE:{tz}"]
    for a in appointments:
        if not a.get("start"):
            continue
        when = _dt_lines(a["start"], a.get("end"), tz)
        if when is None:
            continue
        who = a.get("contact_name") or f"+{a['wa_id']}"
        status = {"confermato": "CONFIRMED", "annullato": "CANCELLED"}.get(a.get("status"), "TENTATIVE")
        title = a["title"] if a.get("status") == "confermato" else f"[{a.get('status')}] {a['title']}"
        desc = f"Cliente: {who} (wa.me/{a['wa_id']})"
        if a.get("notes"):
            desc += f"\n{a['notes']}"
        lines += ["BEGIN:VEVENT", f"UID:appuntamento-{a['id']}@assistente-whatsapp",
                  f"DTSTAMP:{stamp}", *when, f"SUMMARY:{_esc(title)}",
                  f"DESCRIPTION:{_esc(desc)}", f"STATUS:{status}"]
        if a.get("location"):
            lines.append(f"LOCATION:{_esc(a['location'])}")
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return "\r\n".join(_fold(l) for l in lines) + "\r\n"
