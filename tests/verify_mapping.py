"""Cross-check the crossing map two independent ways against the real
id->title pairs captured live during reconnaissance (2026-06-14):

  1. the stable id map (ECHERHA_ID_TO_CANONICAL), and
  2. the Polish-name fallback parsed from the title,

must agree for every checkpoint. Disagreement = a typo in one of the maps.
Pure offline check; no network.
"""
import sys
from pathlib import Path

# Allow running from tests/ — put the repo root on the import path so
# `crossings` resolves whether invoked as `python tests/verify_mapping.py`,
# `python -m tests.verify_mapping`, or under pytest.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from crossings import (
    CANONICAL_NAMES,
    ECHERHA_ID_TO_CANONICAL,
    POLISH_NAME_TO_CANONICAL,
    classify_vehicle,
    map_canonical,
)

# (echerha_id, title, expected_canonical) — verbatim from recon output.
FIXTURE = [
    # trucks (carrierType 1)
    (88,  "Грушів – Будомєж (для вантажівок ≤ 7,5 тонн)", "budomierz"),
    (6,   "Краківець – Корчова (для вантажівок ≥ 7,5 тонн)", "korczowa"),
    (20,  "Краківець – Корчова. Товари 1-24 групи УКТЗЕД (≥ 7,5 тонн)", "korczowa"),
    (5,   "Рава-Руська – Хребенне (для вантажівок ≥ 7,5 тонн)", "hrebenne"),
    (19,  "Рава-Руська – Хребенне. Товари 1-24 групи УКТЗЕД (≥ 7,5 тонн)", "hrebenne"),
    (84,  "Смільниця – Кросьценко (для вантажівок ≤ 7,5 тонн)", "kroscienko"),
    (98,  "Угринів – Долгобичув (для вантажівок ≥ 3,5 тонн до ≤ 7,5 тонн)", "dolhobyczow"),
    (31,  "Угринів – Долгобичув (для порожніх вантажівок ≥ 7,5 тонн)", "dolhobyczow"),
    (80,  "Устилуг – Зосин (для вантажівок ≤ 7,5 тонн)", "zosin"),
    (7,   "Устилуг – Зосин (для порожніх вантажівок ≥ 7,5 тонн)", "zosin"),
    (8,   "Шегині – Медика (для вантажівок ≥ 7,5 тонн)", "medyka"),
    (91,  "Шегині – Медика (для порожніх вантажівок ≥ 3,5 тонн)", "medyka"),
    (1,   "Ягодин – Дорогуськ (для вантажівок ≥ 7,5 тонн)", "dorohusk"),
    (29,  "Ягодин – Дорогуськ (для порожніх вантажівок ≥ 7,5 тонн)", "dorohusk"),
    (2,   "Ягодин – Дорогуськ. Товари 1-24 групи УКТЗЕД (≥ 7,5 тонн)", "dorohusk"),
    # buses (carrierType 2) — note hyphen vs en-dash variation
    (104, "Грушів – Будомєж. Автобуси (за розкладом)", "budomierz"),
    (23,  "Краківець – Корчова. Автобуси", "korczowa"),
    (102, "Нижанковичі – Мальховичі. Автобуси (за розкладом)", "malhowice"),
    (78,  "Рава-Руська – Хребенне. Автобуси (за розкладом)", "hrebenne"),
    (75,  "Смільниця – Кросьценко. Автобуси (за розкладом)", "kroscienko"),
    (76,  "Угринів – Долгобичув. Автобуси (за розкладом)", "dolhobyczow"),
    (113, "Устилуг - Зосин. Автобуси (за розкладом)", "zosin"),
    (24,  "Шегині - Медика. Автобуси", "medyka"),
    (54,  "Ягодин – Дорогуськ. Автобуси (за розкладом)", "dorohusk"),
]


def name_only_canonical(title: str) -> str | None:
    """Resolve via the Polish-name fallback ALONE (ignore the id map)."""
    h = title.lower()
    for polish_name, canonical in POLISH_NAME_TO_CANONICAL.items():
        if polish_name in h:
            return canonical
    return None


failures = []
for echerha_id, title, expected in FIXTURE:
    id_map = ECHERHA_ID_TO_CANONICAL.get(echerha_id)
    name_map = name_only_canonical(title)
    combined = map_canonical(echerha_id, title)
    vclass = classify_vehicle(title, 2 if "Автобус" in title else 1)
    ok = id_map == expected and name_map == expected and combined == expected
    if not ok:
        failures.append((echerha_id, title, expected, id_map, name_map))
    print(f"{'ok ' if ok else 'FAIL'} id={echerha_id:>3} id_map={id_map} "
          f"name_map={name_map} class={vclass:<16} | {title}")

print()
# Every fixture id must be in the map, and vice-versa (no orphan map entries).
fixture_ids = {f[0] for f in FIXTURE}
orphans = set(ECHERHA_ID_TO_CANONICAL) - fixture_ids
print("map ids not covered by fixture:", sorted(orphans) or "none")
print("canonical targets all valid:",
      all(v in CANONICAL_NAMES for v in ECHERHA_ID_TO_CANONICAL.values())
      and all(v in CANONICAL_NAMES for v in POLISH_NAME_TO_CANONICAL.values()))
covered = {ECHERHA_ID_TO_CANONICAL[i] for i in fixture_ids}
print("all 9 canonical crossings covered:", covered == set(CANONICAL_NAMES),
      "(missing:", sorted(set(CANONICAL_NAMES) - covered) or "none", ")")
print()
print("RESULT:", "ALL CHECKS PASS" if not failures else f"{len(failures)} FAILURES: {failures}")

# Exit non-zero on failure so this is usable as a real test (CI / pytest).
if failures:
    sys.exit(1)
