"""Offline checks for the DPSU logger (no network):

  1. DPSU_NAME_TO_CANONICAL covers all 9 canonical crossings, targets valid,
     and never overlaps the drop-set.
  2. The real crossing names captured during recon (2026-06-17) map correctly.
  3. parse_state_of_busy() extracts cars/rate/trucks and yields None (never
     crashes) on sentence values.
  4. kyiv_to_utc() converts naive Kyiv local -> UTC, DST-aware (summer +3, winter +2).
  5. scrape_all() end-to-end on an HTML fixture: poland+car filtering, drop-set,
     closure_flag vs parse_miss_flag (FIX 6), and a hard-fail on an unknown name
     (FIX 5).

Pure offline; safe to run anywhere (also under pytest).
"""
import datetime
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from crossings import CANONICAL_NAMES
from dpsu_scraper import (
    DPSU_NAME_TO_CANONICAL,
    DPSU_NAMES_TO_DROP,
    UnknownCrossingError,
    kyiv_to_utc,
    parse_state_of_busy,
    scrape_all,
)

failures: list[str] = []


def check(label: str, cond: bool, detail: str = "") -> None:
    print(f"{'ok  ' if cond else 'FAIL'} {label}{(' — ' + detail) if detail and not cond else ''}")
    if not cond:
        failures.append(label)


# --- 1. mapping coverage -----------------------------------------------------
covered = set(DPSU_NAME_TO_CANONICAL.values())
check("all 9 canonical crossings covered", covered == set(CANONICAL_NAMES),
      f"missing {sorted(set(CANONICAL_NAMES) - covered)}")
check("all mapping targets are valid canonical ids",
      all(v in CANONICAL_NAMES for v in DPSU_NAME_TO_CANONICAL.values()))
check("name-map and drop-set do not overlap",
      not (set(DPSU_NAME_TO_CANONICAL) & DPSU_NAMES_TO_DROP))

# --- 2. real recon names map as expected -------------------------------------
RECON_NAMES = {
    "Ягодин - Дорогуськ": "dorohusk",
    "Устилуг - Зосін": "zosin",
    "Угринів - Долгобичув": "dolhobyczow",
    "Рава-Руська автомобільний": "hrebenne",
    "Грушів - Будомєж": "budomierz",
    "Краківець - Корчова": "korczowa",
    "Шегині - Медика": "medyka",
    "Смільниця - Кросьценко": "kroscienko",
    "Нижанковичі-Мальховіце": "malhowice",
}
for name, expected in RECON_NAMES.items():
    check(f"map {name!r} -> {expected}", DPSU_NAME_TO_CANONICAL.get(name) == expected,
          f"got {DPSU_NAME_TO_CANONICAL.get(name)}")

# --- 3. parse_state_of_busy --------------------------------------------------
YAHODYN_SOB = ("Кількість легкових авто перед ППр: Пропуск легкових автомобілів "
               "тимчасово не здійснюється <br> Швидкість оформлення легкових авто: "
               "58 авто/год <br> Кількість вантажних авто перед ППр: 2251")
KRAK_SOB = ("Кількість легкових авто перед ППр: 45 <br> Швидкість оформлення "
            "легкових авто: 73 авто/год <br> Кількість вантажних авто перед ППр: 414")
p = parse_state_of_busy(YAHODYN_SOB)
check("Ягодин: cars suspended -> None", p["cars_waiting"] is None, str(p))
check("Ягодин: car rate 58", p["cars_per_hour"] == 58, str(p))
check("Ягодин: trucks 2251", p["trucks_waiting"] == 2251, str(p))
p = parse_state_of_busy(KRAK_SOB)
check("Краківець: cars 45 / rate 73 / trucks 414",
      (p["cars_waiting"], p["cars_per_hour"], p["trucks_waiting"]) == (45, 73, 414), str(p))
p = parse_state_of_busy(None)
check("None blob -> all None", all(v is None for v in p.values()), str(p))

# --- 4. kyiv_to_utc (DST-aware) ----------------------------------------------
check("summer 18:42:36 Kyiv -> 15:42:36Z (UTC+3)",
      kyiv_to_utc("2026-06-17 18:42:36") == "2026-06-17T15:42:36Z",
      kyiv_to_utc("2026-06-17 18:42:36"))
check("winter 10:00:00 Kyiv -> 08:00:00Z (UTC+2)",
      kyiv_to_utc("2026-01-15 10:00:00") == "2026-01-15T08:00:00Z",
      kyiv_to_utc("2026-01-15 10:00:00"))
check("None -> None", kyiv_to_utc(None) is None)
check("garbage -> None", kyiv_to_utc("not a date") is None)

# --- 5. scrape_all end-to-end on a fixture -----------------------------------
def _opt(name, country="poland", typ="car", color="green", state="відкритий",
         created="2026-06-17 21:06:02", sob="Кількість вантажних авто перед ППр: 100"):
    return (f'<option data-country="{country}" '
            f'data-type="{typ}" data-color="{color}" data-state="{state}" '
            f'data-created_at="{created}" data-latitute="50.0" data-longitute="23.0" '
            f'data-state_of_busy="{sob}" value="{name}">{name}</option>')


FIXTURE_HTML = "<select id='by_name'>" + "".join([
    _opt("Ягодин - Дорогуськ", created="2026-06-17 18:42:36", color="green", sob=YAHODYN_SOB),
    _opt("Краківець - Корчова", color="blue", sob=KRAK_SOB),
    # priority crossing closed with no truck digits -> closure_flag
    _opt("Шегині - Медика", state="зачинений",
         sob="Пропуск тимчасово не здійснюється"),
    # open but unparseable -> parse_miss_flag
    _opt("Грушів - Будомєж", state="відкритий", sob="дані відсутні"),
    # dropped point
    _opt("Лудин (пункт контролю)"),
    # filtered: not poland
    _opt("Будапешт", country="hungary"),
    # filtered: not a car (rail)
    _opt("Ягодин залізничний", typ="train"),
]) + "</select>"

NOW = datetime.datetime(2026, 6, 17, 20, 30, 0, tzinfo=datetime.timezone.utc)
recs = scrape_all(FIXTURE_HTML, NOW)
by_id = {r["crossing_id"]: r for r in recs}

check("fixture yields 4 logged records (drop + non-PL + rail excluded)", len(recs) == 4,
      f"got {len(recs)}: {sorted(by_id)}")
check("Лудин dropped", "Лудин (пункт контролю)" not in {r['dpsu_name'] for r in recs})
check("Ягодин trucks=2251, cars=None", by_id.get("dorohusk", {}).get("trucks_waiting") == 2251
      and by_id.get("dorohusk", {}).get("cars_waiting") is None)
check("Ягодин source_updated_utc converted",
      by_id.get("dorohusk", {}).get("source_updated_utc") == "2026-06-17T15:42:36Z",
      by_id.get("dorohusk", {}).get("source_updated_utc"))
check("Ягодин reading_age = 4h47m24s (20:30:00Z - 15:42:36Z)",
      by_id.get("dorohusk", {}).get("reading_age_seconds") == 17244,
      str(by_id.get("dorohusk", {}).get("reading_age_seconds")))
check("closed crossing: closure_flag=1, parse_miss_flag=0, trucks None",
      by_id.get("medyka", {}).get("closure_flag") == 1
      and by_id.get("medyka", {}).get("parse_miss_flag") == 0
      and by_id.get("medyka", {}).get("trucks_waiting") is None)
check("open+unparseable: parse_miss_flag=1, closure_flag=0",
      by_id.get("budomierz", {}).get("parse_miss_flag") == 1
      and by_id.get("budomierz", {}).get("closure_flag") == 0)

# unknown name must hard-fail
try:
    scrape_all("<select id='by_name'>" + _opt("Нова Невідома") + "</select>", NOW)
    check("unknown crossing name hard-fails", False, "no exception raised")
except UnknownCrossingError:
    check("unknown crossing name hard-fails", True)

print()
print("RESULT:", "ALL CHECKS PASS" if not failures else f"{len(failures)} FAILURES: {failures}")
if failures:
    sys.exit(1)
