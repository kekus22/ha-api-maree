"""Tests des calculs de marée (sans Home Assistant)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from custom_components.api_maree.tide import (
    HIGH,
    LOW,
    Extremum,
    TideData,
    parse_attribution,
    parse_curve,
    parse_extrema,
    parse_sun,
)

from .conftest import load_fixture


def _data(with_curve: bool = True) -> TideData:
    start, step, curve = parse_curve(load_fixture("water_levels.json"))
    return TideData(
        site_id="brest",
        site_name="Brest",
        extrema=parse_extrema(load_fixture("tide_extrema.json")),
        curve_start=start if with_curve else None,
        curve_step=step,
        curve=curve if with_curve else [],
        sun=parse_sun(load_fixture("sun_times.json")),
        attribution=parse_attribution(load_fixture("tide_extrema.json")),
    )


NOW = datetime(2026, 11, 27, 9, 0, tzinfo=UTC)


def test_parse_extrema_sorted_and_typed() -> None:
    extrema = parse_extrema(load_fixture("tide_extrema.json"))
    assert extrema == sorted(extrema, key=lambda e: e.time)
    first_high = next(e for e in extrema if e.time.date().isoformat() == "2026-11-27")
    assert first_high == Extremum(HIGH, datetime(2026, 11, 27, 5, 24, tzinfo=UTC), 7.305, 93)
    assert all(e.coefficient is None for e in extrema if e.kind == LOW)


def test_parse_curve() -> None:
    start, step, curve = parse_curve(load_fixture("water_levels.json"))
    assert start == datetime(2026, 11, 26, 22, 0, tzinfo=UTC)
    assert step == timedelta(minutes=10)
    assert len(curve) == 360


def test_next_and_previous() -> None:
    data = _data()
    assert data.previous_extremum(NOW).time == datetime(2026, 11, 27, 5, 24, tzinfo=UTC)
    nxt = data.next_extremum(NOW)
    assert nxt.kind == LOW and nxt.time == datetime(2026, 11, 27, 11, 55, tzinfo=UTC)
    high = data.next_extremum(NOW, HIGH)
    assert high.coefficient == 90
    assert data.is_rising(NOW) is False
    assert data.is_rising(datetime(2026, 11, 27, 14, 0, tzinfo=UTC)) is True


def test_height_from_curve_matches_extrema() -> None:
    data = _data()
    # À l'heure de la pleine mer, la courbe fine doit être proche de la hauteur annoncée.
    at_high = data.height_at(datetime(2026, 11, 27, 5, 24, tzinfo=UTC))
    assert at_high == pytest.approx(7.305, abs=0.05)
    mid = data.height_at(NOW)
    assert 1.3 < mid < 7.3


def test_height_fallback_without_curve() -> None:
    data = _data(with_curve=False)
    assert data.height_at(datetime(2026, 11, 27, 5, 24, tzinfo=UTC)) == pytest.approx(7.305)
    # Au milieu de l'intervalle : moyenne des deux extrêmes.
    prev, nxt = data.previous_extremum(NOW), data.next_extremum(NOW)
    middle = prev.time + (nxt.time - prev.time) / 2
    assert data.height_at(middle) == pytest.approx((prev.height + nxt.height) / 2)


def test_height_fallback_matches_curve_roughly() -> None:
    fine, rough = _data(), _data(with_curve=False)
    for minutes in range(0, 24 * 60, 30):
        when = NOW + timedelta(minutes=minutes)
        assert rough.height_at(when) == pytest.approx(fine.height_at(when), abs=0.6)


def test_level_percent() -> None:
    data = _data()
    pct = data.level_percent(NOW)
    assert 0 < pct < 100
    assert data.level_percent(datetime(2026, 11, 27, 5, 25, tzinfo=UTC)) > 95


def test_coefficients_on() -> None:
    data = _data()
    day = datetime(2026, 11, 27, tzinfo=UTC)
    assert data.coefficients_on(day, day + timedelta(days=1)) == [93, 90]


def test_card_attributes_compact() -> None:
    attrs = _data().card_attributes(NOW)
    assert attrs["curve_step"] == 10
    # Fenêtre [maintenant - 24 h, maintenant + 48 h] : la fin de la courbe est coupée.
    assert attrs["curve_start"] == "2026-11-26T22:00:00+00:00"
    # 26/11 22:00 -> 29/11 09:00 (maintenant + 48 h), plus un point de marge.
    assert len(attrs["curve"]) == 6 * 59 + 2
    assert "sea_curve" not in attrs and "locks" not in attrs
    assert attrs["source"] == "api_maree"
    assert attrs["extrema"][0]["type"] in (HIGH, LOW)
    assert "api-maree.fr" in attrs["data_attribution"]
    assert attrs["sun"][1]["sunrise"].startswith("2026-11-27T")


def test_empty_data_is_safe() -> None:
    data = TideData(site_id="x", site_name="x", extrema=[])
    assert data.height_at(NOW) is None
    assert data.is_rising(NOW) is None
    assert data.level_percent(NOW) is None
    assert data.next_extremum(NOW) is None
