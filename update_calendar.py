#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

BASE = "https://harmonogram.krakow.ideis.pl"
CSV_PATH = "/Plany/WydrukTokuCsv/{course_id}"


@dataclass(frozen=True)
class Event:
    day: date
    start: str
    end: str
    group: str
    subject: str
    location: str
    passing: str = ""
    notes: str = ""


def normalize(value: str) -> str:
    value = (value or "").translate(str.maketrans({"ł": "l", "Ł": "L"}))
    value = unicodedata.normalize("NFKD", value)
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = value.lower().strip()
    value = re.sub(r"\s+", " ", value)
    return value


def decode_csv(data: bytes) -> str:
    for encoding in ("utf-8-sig", "cp1250", "iso-8859-2"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            pass
    raise RuntimeError("Nie udało się rozpoznać kodowania CSV.")


def extract_plan_dates(text: str) -> list[date]:
    result: list[date] = []
    for match in re.finditer(r"Data Zaj(?:e|ę)c:\s*(\d{4})\.(\d{2})\.(\d{2})", text, flags=re.IGNORECASE):
        y, m, d = map(int, match.groups())
        result.append(date(y, m, d))
    return result


def download_csv(course_id: int, start: date, end: date) -> str:
    # Wirtualny Dziekanat bywa czuły na kulturę/format DateTime.
    # Próbujemy kilka formatów i akceptujemy dopiero odpowiedź,
    # która rzeczywiście zawiera daty z żądanego semestru.
    formats = [
        "%d.%m.%Y %H:%M:%S",
        "%d.%m.%Y",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
        "%d/%m/%Y %H:%M:%S",
        "%d/%m/%Y",
        "%m/%d/%Y %H:%M:%S",
        "%m/%d/%Y",
    ]
    attempts: list[dict[str, str]] = []
    for fmt in formats:
        attempts.append({"dO": start.strftime(fmt), "dD": end.strftime(fmt)})
    # Ostateczny fallback na wypadek odwróconych nazw parametrów.
    for fmt in formats:
        attempts.append({"dO": end.strftime(fmt), "dD": start.strftime(fmt)})

    last_error: Exception | None = None
    seen_ranges: list[str] = []

    for params in attempts:
        url = f"{BASE}{CSV_PATH.format(course_id=course_id)}?{urlencode(params)}"
        req = Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (compatible; WSEI-calendar/1.1)",
                "Accept": "text/csv,application/csv,text/plain,*/*;q=0.8",
            },
        )
        try:
            with urlopen(req, timeout=30) as response:
                raw = response.read()
            text = decode_csv(raw)
            dates = extract_plan_dates(text)
            if dates:
                seen_ranges.append(f"{params} -> {min(dates)}..{max(dates)}")
                if any(start <= d <= end for d in dates):
                    print(f"Wybrany format dat: {params}; zakres odpowiedzi {min(dates)}..{max(dates)}")
                    return text
        except Exception as exc:
            last_error = exc

    if seen_ranges:
        raise RuntimeError(
            "Serwer odpowiada, ale żaden format nie zwrócił dat z żądanego semestru. "
            + " | ".join(seen_ranges[:8])
        )
    if last_error:
        raise RuntimeError(f"Nie udało się pobrać planu: {last_error}") from last_error
    raise RuntimeError("Serwer zwrócił CSV, ale bez danych planu.")


def canonical_subject(raw: str) -> str:
    n = normalize(raw)
    if "glebokie sieci neuronowe" in n:
        return "Głębokie sieci neuronowe (Deep Learning) – projekt"
    if n.startswith("ai w biznesie"):
        return "AI w biznesie"
    if "firma symulacyjna" in n:
        return "Firma symulacyjna"
    if "kryptologia" in n:
        return "Kryptologia"
    if "zaawansowane uslugi systemow linuxowych" in n:
        return "Zaawansowane usługi systemów linuxowych"
    return raw.strip()


def pretty_location(raw: str) -> str:
    value = (raw or "").strip()
    if value.startswith("F "):
        value = value[2:].strip()
    n = normalize(value)
    if "teams" in n:
        return "Teams (online)"
    value = value.replace("Paryz", "Paryż").replace("Zajecia", "Zajęcia")
    return value


def subject_allowed(raw: str, subjects: list[str]) -> bool:
    if not subjects:
        return True
    n = normalize(raw)
    return any(normalize(s) in n or n in normalize(s) for s in subjects)


def group_allowed(raw: str, groups: list[str]) -> bool:
    n = normalize(raw)
    return any(normalize(g) in n for g in groups)


def parse_plan(text: str, groups: list[str], subjects: list[str]) -> list[Event]:
    events: list[Event] = []
    current_date: date | None = None

    for row in csv.reader(text.splitlines(), delimiter=";"):
        if not row:
            continue
        first = (row[0] or "").strip()
        first_n = normalize(first)

        if first_n.startswith("data zajec:"):
            match = re.search(r"(\d{4})\.(\d{2})\.(\d{2})", first)
            if match:
                y, m, d = map(int, match.groups())
                current_date = date(y, m, d)
            continue

        if current_date is None or len(row) < 7:
            continue

        start = (row[1] if len(row) > 1 else "").strip()
        end = (row[2] if len(row) > 2 else "").strip()
        group = (row[4] if len(row) > 4 else "").strip()
        subject = (row[5] if len(row) > 5 else "").strip()
        location = (row[6] if len(row) > 6 else "").strip()
        passing = (row[7] if len(row) > 7 else "").strip()
        notes = (row[8] if len(row) > 8 else "").strip()

        if not re.fullmatch(r"\d{1,2}:\d{2}", start) or not re.fullmatch(r"\d{1,2}:\d{2}", end):
            continue
        if not group_allowed(group, groups):
            continue
        if not subject_allowed(subject, subjects):
            continue

        events.append(
            Event(
                day=current_date,
                start=start,
                end=end,
                group=group,
                subject=canonical_subject(subject),
                location=pretty_location(location),
                passing=passing,
                notes=notes,
            )
        )

    return sorted(
        events,
        key=lambda e: (e.day, datetime.strptime(e.start, "%H:%M").time(), e.subject, e.group),
    )


def ical_escape(value: str) -> str:
    return (
        (value or "")
        .replace("\\", "\\\\")
        .replace("\r\n", "\\n")
        .replace("\n", "\\n")
        .replace(";", "\\;")
        .replace(",", "\\,")
    )


def fold_line(line: str, first_limit: int = 75) -> list[str]:
    result: list[str] = []
    current = ""
    limit = first_limit
    for ch in line:
        candidate = current + ch
        if len(candidate.encode("utf-8")) > limit and current:
            result.append(current)
            current = " " + ch
            limit = 75
        else:
            current = candidate
    result.append(current)
    return result


def render_ics(events: list[Event], cfg: dict) -> str:
    tzid = cfg.get("timezone", "Europe/Warsaw")
    calname = cfg.get("calendar_name", "WSEI — plan zajęć")
    source = cfg.get("course_page", "")

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//WSEI Calendar Sync//PL",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{ical_escape(calname)}",
        f"X-WR-TIMEZONE:{tzid}",
        "BEGIN:VTIMEZONE",
        f"TZID:{tzid}",
        f"X-LIC-LOCATION:{tzid}",
        "BEGIN:DAYLIGHT",
        "TZOFFSETFROM:+0100",
        "TZOFFSETTO:+0200",
        "TZNAME:CEST",
        "DTSTART:19700329T020000",
        "RRULE:FREQ=YEARLY;BYMONTH=3;BYDAY=-1SU",
        "END:DAYLIGHT",
        "BEGIN:STANDARD",
        "TZOFFSETFROM:+0200",
        "TZOFFSETTO:+0100",
        "TZNAME:CET",
        "DTSTART:19701025T030000",
        "RRULE:FREQ=YEARLY;BYMONTH=10;BYDAY=-1SU",
        "END:STANDARD",
        "END:VTIMEZONE",
    ]

    occurrence_counter: dict[str, int] = {}
    for event in events:
        base = f"{event.day.isoformat()}|{normalize(event.subject)}|{normalize(event.group)}"
        occurrence_counter[base] = occurrence_counter.get(base, 0) + 1
        occurrence = occurrence_counter[base]
        uid_seed = f"{base}|{occurrence}"
        uid = hashlib.sha1(uid_seed.encode("utf-8")).hexdigest()[:24] + "@wsei-calendar"

        start_dt = datetime.combine(event.day, datetime.strptime(event.start, "%H:%M").time())
        end_dt = datetime.combine(event.day, datetime.strptime(event.end, "%H:%M").time())
        description = f"Grupa: {event.group}"
        if event.passing:
            description += f"\nZaliczenie: {event.passing}"
        if event.notes:
            description += f"\nUwagi: {event.notes}"
        if source:
            description += f"\nŹródło: {source}"

        dtstamp = event.day.strftime("%Y%m%d") + "T000000Z"

        lines.extend(
            [
                "BEGIN:VEVENT",
                f"UID:{uid}",
                f"DTSTAMP:{dtstamp}",
                f"DTSTART;TZID={tzid}:{start_dt.strftime('%Y%m%dT%H%M%S')}",
                f"DTEND;TZID={tzid}:{end_dt.strftime('%Y%m%dT%H%M%S')}",
                f"SUMMARY:{ical_escape(event.subject)}",
                f"LOCATION:{ical_escape(event.location)}",
                f"DESCRIPTION:{ical_escape(description)}",
                "STATUS:CONFIRMED",
                "TRANSP:OPAQUE",
                "END:VEVENT",
            ]
        )

    lines.append("END:VCALENDAR")

    folded: list[str] = []
    for line in lines:
        folded.extend(fold_line(line))
    return "\r\n".join(folded) + "\r\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Pobiera plan WSEI i generuje subskrybowalny plik ICS.")
    parser.add_argument("--config", default="config.json")
    parser.add_argument("--input-file", help="Lokalny CSV zamiast pobierania z uczelni (do testów).")
    parser.add_argument("--output", help="Nadpisuje ścieżkę output z config.json.")
    args = parser.parse_args()

    cfg = json.loads(Path(args.config).read_text(encoding="utf-8"))
    start = date.fromisoformat(cfg["semester_start"])
    end = date.fromisoformat(cfg["semester_end"])

    if args.input_file:
        text = decode_csv(Path(args.input_file).read_bytes())
    else:
        text = download_csv(int(cfg["course_id"]), start, end)

    events = parse_plan(text, cfg.get("groups", []), cfg.get("subjects", []))
    if not events:
        print("--- DEBUG: pierwsze linie pobranego CSV ---", file=sys.stderr)
        for line in text.splitlines()[:40]:
            print(line, file=sys.stderr)
        print("--- KONIEC DEBUG ---", file=sys.stderr)
        raise RuntimeError(
            "Po odfiltrowaniu nie znaleziono żadnych Twoich zajęć. "
            "Nie nadpisuję istniejącego kalendarza."
        )

    ics = render_ics(events, cfg)
    output = Path(args.output or cfg.get("output", "docs/plan.ics"))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(ics, encoding="utf-8", newline="")

    print(f"OK: {len(events)} zajęć -> {output}")
    print(f"Zakres znalezionych zajęć: {events[0].day} — {events[-1].day}")
    for e in events:
        print(f"  {e.day} {e.start}-{e.end} | {e.subject} | {e.location} | {e.group}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"BŁĄD: {exc}", file=sys.stderr)
        raise
