"""Tests de la source EDF (estuaire de la Rance)."""

from __future__ import annotations

import itertools
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from custom_components.api_maree.edf import (
    LOCK_NO_SAS,
    LOCK_SAS,
    LOCK_UNCERTAIN,
    EdfError,
    EdfNotFoundError,
    build_lock_periods,
    local_midnight,
    monday_of,
    parse_levels,
    parse_lock_day,
)
from custom_components.api_maree.tide import HIGH, LOW, TideData, find_extrema

from .conftest import load_fixture

PARIS = ZoneInfo("Europe/Paris")
LEVELS = "edf/NiveauxRance_20261123.json"
LOCKS = "edf/eclusage_1795734000.json"

# Tableau « Niveaux de l'estuaire » publié par EDF (page du 25/11/2026).
EDF_TABLE = [
    (HIGH, 23, "06:55", "08:40", 10.76),
    (HIGH, 23, "20:00", "20:55", 11.32),
    (HIGH, 24, "08:15", "09:10", 11.81),
    (HIGH, 24, "20:40", "21:35", 11.88),
    (HIGH, 25, "09:00", "09:55", 12.02),
    (HIGH, 25, "21:25", "22:20", 11.97),
    (HIGH, 26, "09:45", "10:30", 12.12),
    (HIGH, 26, "22:10", "23:05", 12.21),
    (HIGH, 27, "10:25", "11:30", 12.28),
    (HIGH, 27, "22:50", "23:55", 12.29),
    (LOW, 23, "02:10", "02:45", 4.78),
    (LOW, 23, "14:25", "15:00", 4.43),
    (LOW, 24, "03:30", "03:50", 5.64),
    (LOW, 24, "16:00", "16:15", 5.74),
    (LOW, 25, "04:20", "04:40", 6.02),
    (LOW, 25, "16:55", "17:10", 6.08),
    (LOW, 26, "05:10", "05:30", 6.26),
    (LOW, 26, "17:40", "18:00", 6.26),
    (LOW, 27, "06:00", "06:20", 6.34),
]


def test_parse_levels() -> None:
    levels = parse_levels([load_fixture(LEVELS)])
    assert levels.start == datetime(2026, 11, 22, 23, 0, tzinfo=UTC)  # lundi 23, 00:00 Paris
    assert levels.step == timedelta(minutes=5)
    assert len(levels.estuary) == len(levels.sea) == 7 * 288
    # Vendredi 27/11 à 00:00 (Paris) : mer 6,05 m, estuaire 11,74 m.
    idx = int((datetime(2026, 11, 27, tzinfo=PARIS) - levels.start) / levels.step)
    assert levels.sea[idx] == 6.05
    assert levels.estuary[idx] == 11.74
    assert levels.updated == datetime(2026, 11, 25, 14, 3, 41, tzinfo=UTC)


def test_parse_levels_rejects_empty() -> None:
    with pytest.raises(EdfError):
        parse_levels([{}])


@pytest.mark.parametrize(("kind", "day", "start", "end", "height"), EDF_TABLE)
def test_extrema_match_edf_table(kind: str, day: int, start: str, end: str, height: float) -> None:
    """Les niveaux hauts/bas calculés reproduisent le tableau d'EDF."""
    levels = parse_levels([load_fixture(LEVELS)])
    extrema = find_extrema(levels.start, levels.step, levels.estuary)
    at = lambda hhmm: datetime(  # noqa: E731
        2026, 11, day, *map(int, hhmm.split(":")), tzinfo=PARIS
    )
    t_start, t_end = at(start), at(end)
    middle = t_start + (t_end - t_start) / 2
    ext = min((e for e in extrema if e.kind == kind), key=lambda e: abs(e.time - middle))
    assert ext.height == pytest.approx(height, abs=0.005)
    first = ext.start or ext.time
    last = ext.end or ext.time
    assert abs(first - t_start) <= timedelta(minutes=5)
    if end == "23:55":
        # EDF coupe le palier à minuit ; le calcul le prolonge sur le jour suivant.
        assert last >= t_end
    else:
        assert abs(last - t_end) <= timedelta(minutes=5)


def test_extrema_alternate() -> None:
    levels = parse_levels([load_fixture(LEVELS)])
    kinds = [e.kind for e in find_extrema(levels.start, levels.step, levels.estuary)]
    assert all(a != b for a, b in itertools.pairwise(kinds))
    assert len(kinds) == 27


def test_parse_lock_day() -> None:
    statuses = parse_lock_day(date(2026, 11, 27), load_fixture(LOCKS))
    assert statuses[0] == LOCK_SAS
    assert statuses[1] == LOCK_UNCERTAIN
    assert statuses[2:5] == [LOCK_NO_SAS] * 3
    assert statuses[5:14] == [LOCK_SAS] * 9
    assert statuses[14:18] == [LOCK_NO_SAS] * 4
    assert statuses[18:] == [LOCK_SAS] * 6


def test_parse_lock_day_errors() -> None:
    with pytest.raises(EdfNotFoundError):
        parse_lock_day(date(2026, 11, 27), {"status": False})
    with pytest.raises(EdfError):
        parse_lock_day(date(2026, 11, 27), {"status": True, "content": "<table></table>"})


def test_lock_periods_and_windows() -> None:
    day = date(2026, 11, 27)
    periods = build_lock_periods({day: parse_lock_day(day, load_fixture(LOCKS))})
    assert [(p.status, p.start.astimezone(PARIS).hour) for p in periods] == [
        (LOCK_SAS, 0),
        (LOCK_UNCERTAIN, 1),
        (LOCK_NO_SAS, 2),
        (LOCK_SAS, 5),
        (LOCK_NO_SAS, 14),
        (LOCK_SAS, 18),
    ]
    assert periods[-1].end == datetime(2026, 11, 28, tzinfo=PARIS)
    data = TideData(site_id="edf_rance", site_name="Rance", extrema=[], locks=periods)
    windows = data.lock_windows(frozenset({LOCK_SAS}))
    assert [w[0].astimezone(PARIS).hour for w in windows] == [0, 5, 18]


def test_lock_periods_dst() -> None:
    """Jour du passage à l'heure d'été : 23 h réelles, pas de chevauchement."""
    day = date(2027, 3, 28)
    periods = build_lock_periods(
        {day: [LOCK_SAS] * 24, day + timedelta(days=1): [LOCK_NO_SAS] * 24}
    )
    assert len(periods) == 2
    assert periods[0].end - periods[0].start == timedelta(hours=23)
    assert periods[0].end == periods[1].start


def test_week_and_midnight_helpers() -> None:
    assert monday_of(date(2026, 11, 27)) == date(2026, 11, 23)
    assert monday_of(date(2026, 11, 23)) == date(2026, 11, 23)
    assert int(local_midnight(date(2026, 11, 27)).timestamp()) == 1795734000
