"""Tests for the One Day More nutrition scraper.

Run with: python -m pytest -q
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from odm_scraper import (
    Config,
    PoliteFetcher,
    canonical,
    is_product_url,
    iter_jsonl,
    parse_amount,
    parse_energy,
    parse_nutrition,
    parse_product_page,
    write_csv,
)

PAGE = """<html><head><link rel="canonical" href="https://onedaymore.pl/sklep/musli-sylwetkowe/">
<script>var c="Pozostało: 663";</script></head><body><h1>Musli Sylwetkowe 450g</h1>
<h3>Składniki</h3><p>Skład: płatki zbożowe 63% (<strong>owsiane</strong>, <strong>jęczmienne</strong>), suszone śliwki 11%.<br>
Może zawierać: <strong>orzeszki ziemne</strong>, <strong>mleko</strong>.</p>
<h3>Wartość odżywcza</h3><table><tbody>
<tr><td><strong>WARTOŚĆ ODŻYWCZA MUSLI</strong></td><td><strong>NA 100 g</strong></td><td><strong>NA PORCJĘ 80 g</strong></td></tr>
<tr><td>WARTOŚĆ ENERGETYCZNA</td><td>1425 kJ/341 kcal</td><td>1140 kJ/273 kcal</td></tr>
<tr><td>TŁUSZCZ</td><td>3,6 g</td><td>2,9 g</td></tr>
<tr><td>W TYM KWASY TŁUSZCZOWE NASYCONE</td><td>0,8 g</td><td>0,6 g</td></tr>
<tr><td>WĘGLOWODANY</td><td>60,9 g</td><td>48,7 g</td></tr>
<tr><td>W TYM CUKRY</td><td>15 g</td><td>12 g</td></tr>
<tr><td>BŁONNIK</td><td>12,6 g</td><td>10,1 g</td></tr>
<tr><td>BIAŁKO</td><td>10,4 g</td><td>8,3 g</td></tr>
<tr><td>SÓL</td><td>0,12 g</td><td>0,1 g</td></tr>
<tr><td>CYNK</td><td>2,07 mg (21%*)</td><td>1,66 mg (17%*)</td></tr>
</tbody></table><h3>Przepisy</h3><table><tr><td>x</td><td>100</td></tr></table></body></html>"""


def test_page() -> None:
    r = parse_product_page("https://onedaymore.pl/sklep/musli-sylwetkowe/", PAGE)
    n = r.nutrition
    assert r.name == "Musli Sylwetkowe 450g"
    assert r.nutrition_status == "table"
    assert n is not None and n["portion_g"] == 80
    assert n["per_100g"] == {
        "energy_kj": 1425,
        "energy_kcal": 341,
        "fat_g": 3.6,
        "saturated_fat_g": 0.8,
        "carbs_g": 60.9,
        "sugars_g": 15,
        "fibre_g": 12.6,
        "protein_g": 10.4,
        "salt_g": 0.12,
    }
    assert n["per_portion"]["energy_kcal"] == 273
    assert "CYNK" in n["extras"]
    assert n["warnings"] == []
    assert r.allergens == ["jęczmienne", "owsiane"]
    assert r.may_contain == ["mleko", "orzeszki ziemne"]


def test_portion_mismatch_flagged() -> None:
    n = parse_nutrition(
        [
            ["WARTOŚĆ ODŻYWCZA", "NA 100 g", "NA PORCJĘ 60 g"],
            ["WARTOŚĆ ENERGETYCZNA", "1705 kJ / 406 kcal", "1023 kJ / 244 kcal"],
            ["BŁONNIK", "8 g", "6,4 g"],
        ]
    )
    assert n is not None
    assert any(w.startswith("fibre_g: portion") for w in n.warnings)


def test_100g_only_and_rws() -> None:
    m = parse_nutrition(
        [
            ["Wartość odżywcza", "Na 100 g"],
            ["Tłuszcz:", "19,3 g"],
            ["w tym nasycone kwasy tłuszczowe", "2,5 g"],
        ]
    )
    assert m is not None
    assert m.portion_g is None
    assert m.per_100g["saturated_fat_g"] == 2.5
    k = parse_nutrition(
        [
            ["WARTOŚĆ ODŻYWCZA", "NA 100 g", "NA 44 g (porcja)", "% RWS* w porcji"],
            ["TŁUSZCZ", "41 g", "18 g", "26%"],
        ]
    )
    assert k is not None
    assert k.portion_g == 44
    assert k.per_portion["fat_g"] == 18


def test_missing_table_returns_status_missing() -> None:
    r = parse_product_page("https://onedaymore.pl/sklep/lunchpot/", "<html><h1>Lunchpot</h1></html>")
    assert r.nutrition_status == "missing"
    assert r.nutrition is None


@pytest.mark.parametrize(
    ("t", "v", "u", "lt"),
    [
        ("12,5 g", 12.5, "g", False),
        ("<0,5 g", 0.5, "g", True),
        ("0, 33 g", 0.33, "g", False),
        ("2,07 mg (21%*)", 2.07, "mg", False),
    ],
)
def test_amount(t: str, v: float, u: str, lt: bool) -> None:
    a = parse_amount(t)
    assert (a.value, a.unit, a.less_than) == (v, u, lt)


@pytest.mark.parametrize(
    ("t", "kj", "kcal"),
    [
        ("1850 kJ / 442 kcal", 1850, 442),
        ("1040kJ/228kcal", 1040, 228),
        ("531 kcal/ 2222 kJ", 2222, 531),
        ("1545 kJ/368 kcall", 1545, 368),
        ("394 kJ / 93 cal", 394, 93),
    ],
)
def test_energy(t: str, kj: float, kcal: float) -> None:
    assert parse_energy(t) == (kj, kcal)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://onedaymore.pl/sklep/musli-sylwetkowe", "https://onedaymore.pl/sklep/musli-sylwetkowe/"),
        ("/sklep/granola-czeko-squad/", "https://onedaymore.pl/sklep/granola-czeko-squad/"),
        (
            "https://www.onedaymore.pl/sklep/owsianka-bananowa/?pvid=1",
            "https://www.onedaymore.pl/sklep/owsianka-bananowa/",
        ),
    ],
)
def test_canonical(raw: str, expected: str) -> None:
    assert canonical(raw) == expected


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://onedaymore.pl/sklep/musli-sylwetkowe/", True),
        ("https://onedaymore.pl/sklep/grupa/granole/", False),
        ("https://onedaymore.pl/sklep/page/3/", False),
        ("https://onedaymore.pl/o-nas/", False),
    ],
)
def test_is_product_url(url: str, expected: bool) -> None:
    assert is_product_url(url) is expected


def test_write_csv_keeps_latest_and_flattens(tmp_path: Path) -> None:
    jsonl = tmp_path / "records.jsonl"
    base = parse_product_page("https://onedaymore.pl/sklep/musli-sylwetkowe/", PAGE)
    record = base.__dict__.copy()
    with jsonl.open("w", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        record["name"] = "Musli Sylwetkowe 450g (refreshed)"
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    count = write_csv(jsonl, tmp_path / "nutrition.csv")
    assert count == 1
    parsed, names = [], []
    import csv

    with (tmp_path / "nutrition.csv").open(encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            names.append(row["name"])
            parsed.append(row)
    assert names == ["Musli Sylwetkowe 450g (refreshed)"]
    assert float(parsed[0]["energy_kcal"]) == 341
    assert float(parsed[0]["portion_g"]) == 80


def test_iter_jsonl_skips_broken_lines(tmp_path: Path) -> None:
    jsonl = tmp_path / "records.jsonl"
    jsonl.write_text('{"a": 1}\nnot json\n{"b": 2}\n', encoding="utf-8")
    assert list(iter_jsonl(jsonl)) == [{"a": 1}, {"b": 2}]


def test_iter_jsonl_missing_file(tmp_path: Path) -> None:
    assert list(iter_jsonl(tmp_path / "nope.jsonl")) == []


def test_may_contain_marker_inside_bold_node() -> None:
    html = (
        "<html><body><h1>X</h1><h3>Sk\u0142adniki</h3>"
        "<p>Sk\u0142ad: p\u0142atki <strong>owsiane</strong>.</p>"
        "<p><strong>Mo\u017ce zawiera\u0107: orzeszki ziemne, orzechy, sezam.</strong></p>"
        "</body></html>"
    )
    record = parse_product_page("https://onedaymore.pl/sklep/x/", html)
    assert record.allergens == ["owsiane"]
    assert record.may_contain == ["orzechy", "orzeszki ziemne", "sezam"]


def test_trailing_bold_after_may_contain_clause_is_dropped() -> None:
    html = (
        "<html><body><h1>X</h1><h3>Sk\u0142adniki</h3>"
        "<p>Sk\u0142ad: p\u0142atki <strong>owsiane</strong>. "
        "Mo\u017ce zawiera\u0107: <strong>orzeszki ziemne</strong>. "
        "<strong>Uwaga: zak\u0142ad przetwarza orzechy</strong>.</p>"
        "</body></html>"
    )
    record = parse_product_page("https://onedaymore.pl/sklep/x/", html)
    assert record.allergens == ["owsiane"]
    assert record.may_contain == ["orzeszki ziemne"]


class _FakeResponse:
    def __init__(self, url: str, body: bytes) -> None:
        self._url = url
        self._body = body
        self.status = 200
        self.headers: dict[str, str] = {}

    def geturl(self) -> str:
        return self._url

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False


def _patch_urlopen(monkeypatch: pytest.MonkeyPatch) -> dict[str, int]:
    import urllib.request

    calls = {"count": 0}

    def fake_urlopen(request: object, timeout: float | None = None) -> _FakeResponse:
        calls["count"] += 1
        url = getattr(request, "full_url", str(request))
        return _FakeResponse(url, b"<html>fresh</html>")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    return calls


def test_refresh_bypasses_fresh_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _patch_urlopen(monkeypatch)
    url = "https://onedaymore.pl/sklep/x/"
    cold = Config(contact="a@b.c", cache_dir=tmp_path, delay_s=0.0, jitter_s=0.0)
    fetcher = PoliteFetcher(cold)
    fetcher.get(url)
    fetcher.get(url)
    assert calls["count"] == 2

    warm = Config(contact="a@b.c", cache_dir=tmp_path, delay_s=0.0, jitter_s=0.0, refresh=True)
    refreshed = PoliteFetcher(warm)
    refreshed.get(url)
    assert calls["count"] == 4


def test_fetcher_refuses_non_allowlisted_targets(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_urlopen(monkeypatch)
    cfg = Config(contact="a@b.c", cache_dir=tmp_path, delay_s=0.0, jitter_s=0.0)
    fetcher = PoliteFetcher(cfg)
    assert fetcher.get("file:///etc/passwd").status == -1
    assert fetcher.get("http://169.254.169.254/latest/meta-data/").status == -1
    assert fetcher.get("https://evil.example.com/sitemap.xml").status == -1
