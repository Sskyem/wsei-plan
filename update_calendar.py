#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
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
    """Pobiera CSV przez prawdziwy formularz strony.

    Sam endpoint WydrukTokuCsv nie honoruje arbitralnego zakresu dat, dopóki
    zakres nie zostanie ustawiony przez callback siatki DevExpress. Dlatego
    wykonujemy ten sam callback co przycisk "Szukaj" w przeglądarce, a potem
    pobieramy CSV z aktualnego linku eksportu w tej samej sesji.
    """
    try:
        from selenium import webdriver
        from selenium.webdriver.common.by import By
        from selenium.webdriver.chrome.options import Options
        from selenium.webdriver.support.ui import WebDriverWait
    except ImportError as exc:
        raise RuntimeError("Brak biblioteki selenium. Zainstaluj ją przed uruchomieniem.") from exc

    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1440,1200")

    driver = webdriver.Chrome(options=options)
    driver.set_page_load_timeout(45)
    driver.set_script_timeout(45)

    try:
        page_url = f"{BASE}/Plany/PlanyTokow/{course_id}"
        driver.get(page_url)

        wait = WebDriverWait(driver, 30)
        wait.until(
            lambda d: d.execute_script(
                "return typeof MVCxDataOd !== 'undefined' "
                "&& typeof MVCxDataDo !== 'undefined' "
                "&& typeof gridViewPlanyTokow !== 'undefined' "
                "&& typeof FiltrujDane === 'function';"
            )
        )

        selector = 'a[href*="WydrukTokuCsv"]'
        initial_href = driver.find_element(By.CSS_SELECTOR, selector).get_attribute("href") or ""

        driver.execute_script(
            """
            MVCxDataOd.SetDate(new Date(arguments[0], arguments[1], arguments[2]));
            MVCxDataDo.SetDate(new Date(arguments[3], arguments[4], arguments[5]));
            FiltrujDane(gridViewPlanyTokow, arguments[6]);
            """,
            start.year,
            start.month - 1,
            start.day,
            end.year,
            end.month - 1,
            end.day,
            course_id,
        )

        # Callback DevExpress działa asynchronicznie. Sam link eksportu
        # na tej stronie nie zawsze zmienia atrybut href, dlatego czekamy na
        # zakończenie callbacku siatki, a następnie pobieramy eksport w tej
        # samej sesji przeglądarki.
        wait.until(
            lambda d: not bool(
                d.execute_script(
                    "return (typeof gridViewPlanyTokow.InCallback === 'function') "
                    "? gridViewPlanyTokow.InCallback() : false;"
                )
            )
        )

        # Dajemy skryptom EndCallback krótki moment na aktualizację DOM.
        import time
        time.sleep(2)

        href = driver.find_element(By.CSS_SELECTOR, selector).get_attribute("href") or initial_href
        grid_text = driver.find_element(By.ID, "gridViewPlanyTokow").text
        print(f"Po callbacku: DataOd={driver.find_element(By.ID, 'DataOd_I').get_attribute('value')}, "
              f"DataDo={driver.find_element(By.ID, 'DataDo_I').get_attribute('value')}, href={href}")
        print("Fragment siatki:", grid_text[:1200].replace("\\n", " | "))

        # Pobieramy binarne CSV przez fetch() w tej samej sesji/przeglądarce,
        # żeby zachować stan callbacku/cookies. Wynik przekazujemy jako base64.
        encoded = driver.execute_async_script(
            """
            const url = arguments[0];
            const done = arguments[arguments.length - 1];
            fetch(url, { credentials: 'same-origin', cache: 'no-store' })
              .then(r => {
                if (!r.ok) throw new Error('HTTP ' + r.status);
                return r.arrayBuffer();
              })
              .then(buf => {
                const bytes = new Uint8Array(buf);
                let binary = '';
                const chunk = 0x8000;
                for (let i = 0; i < bytes.length; i += chunk) {
                  binary += String.fromCharCode(...bytes.subarray(i, i + chunk));
                }
                done('OK:' + btoa(binary));
              })
              .catch(e => done('ERR:' + String(e)));
            """,
            href,
        )

        if not isinstance(encoded, str) or encoded.startswith("ERR:"):
            raise RuntimeError(f"Nie udało się pobrać CSV po callbacku: {encoded}")

        raw = base64.b64decode(encoded.removeprefix("OK:"))
        text = decode_csv(raw)
        dates = extract_plan_dates(text)

        if not dates:
            raise RuntimeError(f"CSV nie zawiera żadnych dat zajęć. Link eksportu: {href}")
        if not any(start <= d <= end for d in dates):
            raise RuntimeError(
                f"CSV ma nieprawidłowy zakres {min(dates)}..{max(dates)}; "
                f"oczekiwano {start}..{end}. Link eksportu: {href}"
            )

        print(f"Pobrano CSV z zakresu {min(dates)}..{max(dates)} przez {href}")
        return text
    finally:
        driver.quit()


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
