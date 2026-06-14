"""
Shared canonical crossing identifiers for the PL-UA border loggers.

The Polish logger (scraper.py / granica.gov.pl) and the Ukrainian eCherga
logger (echerha_scraper.py) both key their data on these canonical IDs so the
two datasets — Polish PHYSICAL wait times vs Ukrainian VIRTUAL queue metrics —
join cleanly on (crossing_id, timestamp).

eCherga renames every queue after its Polish counterpart and splits each
crossing by tonnage / empty / goods-group / bus. The right-hand side of each
eCherga title (e.g. "Ягодин – Дорогуськ") is the Polish crossing name, which is
what we map on.
"""

# Canonical ID -> Polish display name (same set the granica logger uses).
CANONICAL_NAMES: dict[str, str] = {
    "dorohusk":    "Dorohusk",
    "zosin":       "Zosin",
    "dolhobyczow": "Dołhobyczów",
    "hrebenne":    "Hrebenne",
    "budomierz":   "Budomierz",
    "korczowa":    "Korczowa",
    "medyka":      "Medyka",
    "malhowice":   "Małhowice",
    "kroscienko":  "Krościenko",
}

# eCherga checkpoint id -> canonical crossing id.
# Captured during reconnaissance on 2026-06-14 from
# https://back.echerha.gov.ua/api/v4/workload/{1=trucks,2=buses}
# (Poland is country_id=133). IDs are stable; this is the primary lookup.
ECHERHA_ID_TO_CANONICAL: dict[int, str] = {
    # --- trucks (carrierType 1) ---
    88:  "budomierz",    # Грушів – Будомєж (≤ 7,5 т)
    6:   "korczowa",     # Краківець – Корчова (≥ 7,5 т)
    20:  "korczowa",     # Краківець – Корчова. Товари 1-24 групи УКТЗЕД
    5:   "hrebenne",     # Рава-Руська – Хребенне (≥ 7,5 т)
    19:  "hrebenne",     # Рава-Руська – Хребенне. Товари 1-24 групи УКТЗЕД
    84:  "kroscienko",   # Смільниця – Кросьценко (≤ 7,5 т)
    98:  "dolhobyczow",  # Угринів – Долгобичув (≥ 3,5 т до ≤ 7,5 т)
    31:  "dolhobyczow",  # Угринів – Долгобичув (порожні ≥ 7,5 т)
    80:  "zosin",        # Устилуг – Зосин (≤ 7,5 т)
    7:   "zosin",        # Устилуг – Зосин (порожні ≥ 7,5 т)
    8:   "medyka",       # Шегині – Медика (≥ 7,5 т)
    91:  "medyka",       # Шегині – Медика (порожні ≥ 3,5 т)
    1:   "dorohusk",     # Ягодин – Дорогуськ (≥ 7,5 т)
    29:  "dorohusk",     # Ягодин – Дорогуськ (порожні ≥ 7,5 т)
    2:   "dorohusk",     # Ягодин – Дорогуськ. Товари 1-24 групи УКТЗЕД
    # --- buses (carrierType 2) ---
    104: "budomierz",    # Грушів – Будомєж. Автобуси
    23:  "korczowa",     # Краківець – Корчова. Автобуси
    102: "malhowice",    # Нижанковичі – Мальховичі. Автобуси
    78:  "hrebenne",     # Рава-Руська – Хребенне. Автобуси
    75:  "kroscienko",   # Смільниця – Кросьценко. Автобуси
    76:  "dolhobyczow",  # Угринів – Долгобичув. Автобуси
    113: "zosin",        # Устилуг – Зосин. Автобуси
    24:  "medyka",       # Шегині – Медика. Автобуси
    54:  "dorohusk",     # Ягодин – Дорогуськ. Автобуси
}

# Fallback: Polish crossing name (as it appears on the right of the eCherga
# title's en-dash) -> canonical id. Used when an unknown eCherga checkpoint id
# shows up so a renamed/added Poland queue is still mapped rather than dropped.
POLISH_NAME_TO_CANONICAL: dict[str, str] = {
    "дорогуськ":  "dorohusk",
    "зосин":      "zosin",
    "долгобичув": "dolhobyczow",
    "хребенне":   "hrebenne",
    "будомєж":    "budomierz",
    "корчова":    "korczowa",
    "медика":     "medyka",
    "мальховичі": "malhowice",
    "кросьценко": "kroscienko",
}

# eCherga's country_id for Poland (from the workload "filters.countries" list).
POLAND_COUNTRY_ID = 133


def map_canonical(echerha_id: int, title: str) -> str | None:
    """Resolve an eCherga checkpoint to a canonical crossing id.

    Tries the stable id map first, then falls back to matching the Polish
    crossing name embedded in the title. Returns None if neither matches
    (caller should log it — likely a new Poland queue worth adding above).
    """
    canonical = ECHERHA_ID_TO_CANONICAL.get(echerha_id)
    if canonical:
        return canonical

    haystack = (title or "").lower()
    for polish_name, canonical in POLISH_NAME_TO_CANONICAL.items():
        if polish_name in haystack:
            return canonical
    return None


def classify_vehicle(title: str, for_vehicle_type: int | None) -> str:
    """Coarse vehicle-class label parsed from the eCherga title.

    eCherga splits each crossing into several queues; this keeps them
    distinguishable in storage without hard-coding every id.
    """
    t = (title or "").lower()
    if for_vehicle_type == 2 or "автобус" in t:
        return "bus"
    if "товари 1-24" in t or "уктзед" in t:
        return "truck_goods_1_24"
    if "порожн" in t:  # порожні = empty
        return "truck_empty"
    if "≤ 7,5" in t or "до ≤ 7,5" in t or "≥ 3,5 т до" in t:
        return "truck_le_7_5t"
    if "≥ 7,5" in t:
        return "truck_ge_7_5t"
    if "≥ 3,5" in t:
        return "truck_ge_3_5t"
    return "truck"
