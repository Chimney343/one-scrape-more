#!/usr/bin/env python3
"""Polite, stdlib-only scraper for onedaymore.pl products and nutrition facts.

The site runs WordPress + WooCommerce and renders its product pages on the
server. Every sampled product page carries the nutrition facts as a plain HTML
``<table>`` right after an ``<h3>Wartość odżywcza</h3>`` heading, so a single
sequential connection with a polite delay is enough: no OCR, no headless
browser, no async.

Usage:
    python odm_scraper.py --contact you@example.com [--max-requests 800] [--refresh]
"""

from __future__ import annotations

import argparse
import csv
import email.utils
import hashlib
import json
import logging
import os
import random
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
import xml.etree.ElementTree as ET
from collections.abc import Iterator
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

__all__ = [
    "Amount",
    "BudgetExceeded",
    "BackOff",
    "Config",
    "LinkParser",
    "Nutrition",
    "PoliteFetcher",
    "ProductPageParser",
    "ProductRecord",
    "Response",
    "canonical",
    "classify",
    "discover",
    "extract_allergens",
    "extract_jsonld_product",
    "is_product_url",
    "iter_jsonl",
    "iter_listing",
    "iter_sitemap",
    "main",
    "norm_space",
    "parse_amount",
    "parse_energy",
    "parse_nutrition",
    "parse_product_page",
    "probe_store_api",
    "sanity_check",
    "to_float",
    "url_allowed",
    "write_csv",
]

BASE = "https://onedaymore.pl"
ALLOWED_NETLOCS = frozenset({"onedaymore.pl", "www.onedaymore.pl"})
SITEMAP_NS = "{http://www.sitemaps.org/schemas/sitemap/0.9}"
LOG = logging.getLogger("odm")

NUMBER = r"\d{1,3}(?:[ \u00a0]\d{3})+(?:[.,]\d+)?|\d+(?:\s*[.,]\s*\d+)?"
ALLERGEN_MARKER_RE = re.compile(r"może\s+zawierać", re.IGNORECASE)
HEADER_PREFIX_RE = re.compile(r"^\s*(?:składniki|skład|może\s+zawierać)\s*[:.]?\s*", re.IGNORECASE)
CLAUSE_END_RE = re.compile(r"[.!?](?=\s|$)")
FIELDS = (
    "energy_kj",
    "energy_kcal",
    "fat_g",
    "saturated_fat_g",
    "carbs_g",
    "sugars_g",
    "fibre_g",
    "protein_g",
    "salt_g",
)
CSV_COLS = [
    "source_url",
    "scraped_at",
    "name",
    "sku",
    "gtin",
    "price_pln",
    "availability",
    "nutrition_status",
    "portion_g",
    *FIELDS,
    "polyols_g",
    *[f"portion_{key}" for key in FIELDS],
    "warnings",
]


@dataclass(frozen=True)
class Config:
    """Runtime configuration for a crawl."""

    contact: str
    delay_s: float = 3.0
    jitter_s: float = 1.5
    timeout_s: float = 20.0
    max_requests: int = 800
    max_retries: int = 4
    max_age_h: float = 20.0
    cache_dir: Path = Path("cache")
    out_dir: Path = Path("out")
    refresh: bool = False

    @property
    def user_agent(self) -> str:
        """Descriptive User-Agent that identifies the crawler and a contact."""
        return f"odm-nutrition-research/1.0 (+mailto:{self.contact}; single connection, obeys robots.txt)"


class BudgetExceeded(RuntimeError):
    """Raised when the hard request cap for a run is reached."""


class BackOff(RuntimeError):
    """Raised when the server keeps throttling or asks to wait too long."""


@dataclass
class Response:
    """A fetched (or cached) HTTP response.

    Attributes:
        url: The requested URL.
        status: HTTP status code; ``0`` for network failure, ``-1`` when the URL
            is disallowed by robots.txt or is not an allow-listed shop URL.
        body: Raw response body bytes.
        from_cache: Whether the body came from the on-disk cache.
    """

    url: str
    status: int
    body: bytes
    from_cache: bool = False


def url_allowed(url: str) -> bool:
    """Return whether ``url`` is a safe http(s) target on the shop's own hosts."""
    parts = urllib.parse.urlsplit(url)
    return parts.scheme in ("http", "https") and parts.netloc in ALLOWED_NETLOCS


class PoliteFetcher:
    """Sequential HTTP fetcher that honours robots.txt, polite delays, retries and a budget."""

    def __init__(self, cfg: Config) -> None:
        """Initialise the fetcher and load robots.txt.

        Args:
            cfg: Crawl configuration.
        """
        self.cfg = cfg
        self.requests_made = 0
        self._last = 0.0
        self.delay = cfg.delay_s
        cfg.cache_dir.mkdir(parents=True, exist_ok=True)
        self.robots = urllib.robotparser.RobotFileParser(f"{BASE}/robots.txt")
        response = self._fetch(f"{BASE}/robots.txt")
        if response.status == 200:
            self.robots.parse(response.body.decode("utf-8", "replace").splitlines())
        elif response.status in (401, 403):
            self.robots.disallow_all = True
        else:
            self.robots.allow_all = True
        self.robots.modified()
        crawl_delay = self.robots.crawl_delay(cfg.user_agent)
        if crawl_delay:
            self.delay = max(self.delay, float(crawl_delay))
        LOG.info(
            "robots status=%s delay=%.1fs sitemaps=%s",
            response.status,
            self.delay,
            self.robots.site_maps(),
        )

    def get(self, url: str) -> Response:
        """Return a response for ``url``, refusing it when robots.txt disallows it."""
        if not self.robots.can_fetch(self.cfg.user_agent, url):
            LOG.warning("robots.txt disallows %s", url)
            return Response(url, -1, b"")
        return self._fetch(url)

    def _paths(self, url: str) -> tuple[Path, Path]:
        key = hashlib.sha256(url.encode()).hexdigest()[:32]
        return self.cfg.cache_dir / f"{key}.json", self.cfg.cache_dir / f"{key}.body"

    def _fetch(self, url: str) -> Response:
        if not url_allowed(url):
            LOG.warning("refusing non-allowlisted URL %s", url)
            return Response(url, -1, b"")
        meta_path, body_path = self._paths(url)
        meta: dict[str, Any] | None = None
        if meta_path.exists() and body_path.exists():
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            fresh_enough = time.time() - meta["fetched_at"] < self.cfg.max_age_h * 3600
            if not self.cfg.refresh and fresh_enough and body_path.stat().st_size:
                return Response(url, meta["status"], body_path.read_bytes(), True)
        headers = {
            "User-Agent": self.cfg.user_agent,
            "Accept-Language": "pl-PL,pl;q=0.9",
            "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
        }
        if meta and meta.get("etag"):
            headers["If-None-Match"] = meta["etag"]
        if meta and meta.get("last_modified"):
            headers["If-Modified-Since"] = meta["last_modified"]
        status = 0
        for attempt in range(self.cfg.max_retries + 1):
            self._throttle()
            try:
                with urllib.request.urlopen(
                    urllib.request.Request(url, headers=headers), timeout=self.cfg.timeout_s
                ) as resp:
                    if not url_allowed(resp.geturl()):
                        LOG.warning("refusing redirect to non-allowlisted URL %s", resp.geturl())
                        return Response(url, -1, b"")
                    status, body, response_headers = resp.status, resp.read(), resp.headers
            except urllib.error.HTTPError as exc:
                status, body, response_headers = exc.code, exc.read(), exc.headers
            except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
                LOG.warning("network error %s: %s", url, exc)
                self._backoff(attempt, None)
                continue
            if status == 304 and meta and body_path.stat().st_size:
                meta["fetched_at"] = time.time()
                self._write_atomic(meta_path, json.dumps(meta).encode("utf-8"))
                return Response(url, meta["status"], body_path.read_bytes(), True)
            if status in (429, 500, 502, 503, 504):
                self._backoff(attempt, response_headers.get("Retry-After"))
                continue
            if status == 200:
                self._write_atomic(body_path, body)
                self._write_atomic(
                    meta_path,
                    json.dumps(
                        {
                            "url": url,
                            "status": 200,
                            "fetched_at": time.time(),
                            "etag": response_headers.get("ETag"),
                            "last_modified": response_headers.get("Last-Modified"),
                        }
                    ).encode("utf-8"),
                )
            return Response(url, status, body)
        if status in (429, 503):
            raise BackOff(f"server keeps answering {status} for {url}")
        return Response(url, status, b"")

    @staticmethod
    def _write_atomic(path: Path, data: bytes) -> None:
        """Write ``data`` to ``path`` atomically so a crash cannot leave a partial file."""
        temp = path.with_name(path.name + ".tmp")
        temp.write_bytes(data)
        os.replace(temp, path)

    def _throttle(self) -> None:
        if self.requests_made >= self.cfg.max_requests:
            raise BudgetExceeded(f"hard cap {self.cfg.max_requests} reached")
        wait = self._last + self.delay + random.uniform(0, self.cfg.jitter_s) - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self._last = time.monotonic()
        self.requests_made += 1

    def _backoff(self, attempt: int, retry_after: str | None) -> None:
        delay = min(300.0, self.delay * 2 ** (attempt + 1))
        if retry_after:
            try:
                requested = float(retry_after)
            except ValueError:
                try:
                    requested = (email.utils.parsedate_to_datetime(retry_after) - datetime.now(UTC)).total_seconds()
                except (TypeError, ValueError):
                    requested = 0.0
            if requested > 900:
                raise BackOff(f"Retry-After={retry_after!r} > 15 min; resume later")
            delay = max(delay, requested)
        LOG.info("backing off %.0fs", delay)
        time.sleep(delay + random.uniform(0, 1))


def canonical(url: str) -> str:
    """Return the canonical, trailing-slash-qualified form of ``url``."""
    parts = urllib.parse.urlsplit(urllib.parse.urljoin(BASE + "/", url))
    path = parts.path if parts.path.endswith("/") else parts.path + "/"
    return f"https://{parts.netloc}{path}"


def is_product_url(url: str) -> bool:
    """Return whether ``url`` is a single product page (``/sklep/<slug>/``)."""
    parts = urllib.parse.urlsplit(url)
    segments = [segment for segment in parts.path.split("/") if segment]
    return (
        parts.netloc in ("onedaymore.pl", "www.onedaymore.pl")
        and len(segments) == 2
        and segments[0] == "sklep"
        and segments[1] not in ("grupa", "page")
    )


def iter_sitemap(fetcher: PoliteFetcher, url: str, depth: int = 0) -> Iterator[str]:
    """Yield product-ish URLs from an XML sitemap, following nested indexes."""
    response = fetcher.get(url)
    if response.status != 200 or depth > 2:
        return
    try:
        root = ET.fromstring(response.body)
    except ET.ParseError:
        return
    locations = [el.text.strip() for el in root.iter(f"{SITEMAP_NS}loc") if el.text]
    if root.tag == f"{SITEMAP_NS}sitemapindex":
        children = [loc for loc in locations if "product" in loc] or locations
        for child in children:
            yield from iter_sitemap(fetcher, child, depth + 1)
    else:
        yield from locations


def probe_store_api(fetcher: PoliteFetcher, max_pages: int = 10) -> dict[str, dict[str, Any]]:
    """Page the WooCommerce Store API and map canonical URL to commerce fields."""
    products: dict[str, dict[str, Any]] = {}
    for page in range(1, max_pages + 1):
        response = fetcher.get(f"{BASE}/wp-json/wc/store/v1/products?per_page=100&page={page}")
        try:
            batch = json.loads(response.body) if response.status == 200 else None
        except json.JSONDecodeError:
            batch = None
        if not isinstance(batch, list):
            break
        for product in batch:
            prices = product.get("prices") or {}
            minor_unit = int(prices.get("currency_minor_unit") or 2)
            raw_price = prices.get("price")
            products[canonical(product.get("permalink", ""))] = {
                "id": product.get("id"),
                "sku": product.get("sku") or None,
                "name": product.get("name"),
                "price": int(raw_price) / 10**minor_unit if raw_price not in (None, "") else None,
                "in_stock": product.get("is_in_stock"),
                "categories": [category.get("name") for category in product.get("categories", [])],
                "images": [image.get("src") for image in product.get("images", [])],
                "variations": product.get("variations", []),
            }
        if len(batch) < 100:
            break
    return products


class LinkParser(HTMLParser):
    """Collect absolute ``href`` targets from an HTML document."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        href = dict(attrs).get("href")
        if tag == "a" and href:
            self.links.add(urllib.parse.urljoin(BASE + "/", href))


def iter_listing(fetcher: PoliteFetcher, max_pages: int = 60) -> Iterator[str]:
    """Walk the shop listing pagination and yield new product URLs."""
    seen: set[str] = set()
    for page in range(1, max_pages + 1):
        url = f"{BASE}/sklep/" if page == 1 else f"{BASE}/sklep/page/{page}/"
        response = fetcher.get(url)
        if response.status != 200:
            break
        parser = LinkParser()
        parser.feed(response.body.decode("utf-8", "replace"))
        new = {canonical(link) for link in parser.links if is_product_url(link)} - seen
        if not new:
            break
        seen |= new
        yield from sorted(new)


def discover(fetcher: PoliteFetcher, store: dict[str, dict[str, Any]]) -> list[str]:
    """Combine Store API, sitemaps and listing pagination into product URLs."""
    urls = {url for url in store if is_product_url(url)}
    candidates = fetcher.robots.site_maps() or [f"{BASE}/sitemap_index.xml", f"{BASE}/sitemap.xml"]
    for sitemap in candidates:
        found = {canonical(url) for url in iter_sitemap(fetcher, sitemap) if is_product_url(url)}
        urls |= found
        if found:
            break
    if not urls:
        urls |= set(iter_listing(fetcher))
    LOG.info("discovered %d product URLs", len(urls))
    return sorted(urls)


def norm_space(text: str) -> str:
    """Collapse all whitespace runs (including non-breaking spaces) to single spaces."""
    return re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()


def to_float(text: str) -> float:
    """Parse a possibly space-grouped, comma-decimal number into a float."""
    return float(re.sub(r"\s", "", text).replace(",", "."))


@dataclass
class Amount:
    """A parsed numeric amount with optional unit and a "less than" flag."""

    value: float | None
    unit: str | None = None
    less_than: bool = False


def parse_amount(text: str) -> Amount:
    """Parse the first numeric amount from a nutrition cell.

    Strips parenthesised notes, understands comma decimals and an optional
    ``<``/``≤``/``poniżej`` prefix, and captures ``mg``/``µg``/``g`` units.
    """
    cleaned = re.sub(r"\(.*?\)", " ", text.replace("\xa0", " "))
    match = re.search(
        rf"(<|≤|poniżej)?\s*({NUMBER})\s*(mg|µg|μg|g)?(?![a-ząćęłńóśźż])",
        cleaned,
        re.IGNORECASE,
    )
    if not match:
        return Amount(None)
    return Amount(
        to_float(match.group(2)),
        (match.group(3) or "").lower() or None,
        bool(match.group(1)),
    )


def parse_energy(text: str) -> tuple[float | None, float | None]:
    """Return the ``(kJ, kcal)`` pair printed in an energy cell."""
    cleaned = text.replace("\xa0", " ")
    kj = re.search(rf"({NUMBER})\s*kj", cleaned, re.IGNORECASE)
    kcal = re.search(rf"({NUMBER})\s*k?cal", cleaned, re.IGNORECASE)
    return (to_float(kj.group(1)) if kj else None, to_float(kcal.group(1)) if kcal else None)


def classify(label: str) -> str | None:
    """Map a nutrition row label to a canonical field key, or ``None`` if unknown."""
    text = re.sub(r"^wartość odżywcza\s*", "", norm_space(label).lower())
    text = re.sub(r"^w tym:?\s*", "", text).strip(" :,*")
    if "energ" in text:
        return "energy"
    if "jednonienasyc" in text:
        return "monounsaturated_fat_g"
    if "wielonienasyc" in text:
        return "polyunsaturated_fat_g"
    if "nasycon" in text:
        return "saturated_fat_g"
    if text.startswith("tłuszcz"):
        return "fat_g"
    if "cukr" in text:
        return "sugars_g"
    if "poliol" in text:
        return "polyols_g"
    for prefix, key in (
        ("węglowodan", "carbs_g"),
        ("błonnik", "fibre_g"),
        ("białk", "protein_g"),
        ("sól", "salt_g"),
    ):
        if text.startswith(prefix):
            return key
    return None


@dataclass
class Nutrition:
    """Nutrition facts parsed from a product table."""

    per_100g: dict[str, float | None] = field(default_factory=dict)
    per_portion: dict[str, float | None] = field(default_factory=dict)
    portion_g: float | None = None
    extras: dict[str, dict[str, str]] = field(default_factory=dict)
    less_than: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def parse_nutrition(rows: list[list[str]]) -> Nutrition | None:
    """Parse nutrition-facts rows into a :class:`Nutrition`, or ``None`` when empty."""
    if not rows:
        return None
    nutrition = Nutrition()
    col100: int = 1
    colp: int | None = 2
    if classify(rows[0][0]) is None and any("100" in cell for cell in rows[0][1:]):
        header = [cell.lower() for cell in rows[0]]
        col100 = next((i for i, cell in enumerate(header) if "100" in cell), 1)
        colp = next(
            (i for i, cell in enumerate(header) if i != col100 and ("porcj" in cell or re.search(r"na\s*\d", cell))),
            None,
        )
        if colp is not None:
            portion_match = re.search(rf"({NUMBER})\s*g", header[colp])
            nutrition.portion_g = to_float(portion_match.group(1)) if portion_match else None
        rows = rows[1:]
    for row in rows:
        if len(row) <= col100:
            continue
        cells = {
            "100": row[col100],
            "portion": row[colp] if colp is not None and colp < len(row) else "",
        }
        key = classify(row[0])
        if key is None:
            nutrition.extras[row[0]] = cells
            continue
        for where, target in (("100", nutrition.per_100g), ("portion", nutrition.per_portion)):
            if not cells[where]:
                continue
            if key == "energy":
                target["energy_kj"], target["energy_kcal"] = parse_energy(cells[where])
                continue
            amount = parse_amount(cells[where])
            if amount.unit == "mg" and amount.value is not None:
                amount.value /= 1000
            target[key] = amount.value
            if amount.less_than:
                nutrition.less_than.append(f"{key}@{where}")
    for key in FIELDS:
        nutrition.per_100g.setdefault(key, None)
    nutrition.warnings = sanity_check(nutrition)
    return nutrition


def sanity_check(nutrition: Nutrition) -> list[str]:
    """Return warnings for internally inconsistent nutrition values."""
    warnings: list[str] = []
    per_100g = nutrition.per_100g
    kj, kcal = per_100g.get("energy_kj"), per_100g.get("energy_kcal")
    if kj is None or kcal is None:
        warnings.append("energy: kJ or kcal missing")
    elif kcal and abs(kj / kcal - 4.184) > 0.15:
        warnings.append(f"energy: kJ/kcal ratio {kj / kcal:.2f}")
    for part, whole in (("saturated_fat_g", "fat_g"), ("sugars_g", "carbs_g")):
        if (
            per_100g.get(part) is not None
            and per_100g.get(whole) is not None
            and per_100g[part] > per_100g[whole] + 0.05  # type: ignore[operator]
        ):
            warnings.append(f"{part} > {whole}")
    if nutrition.portion_g:
        factor = nutrition.portion_g / 100
        for key, value in nutrition.per_portion.items():
            base = per_100g.get(key)
            if value is None or base is None:
                continue
            expected = base * factor
            tolerance = 0.025 * expected if key.startswith("energy") else max(0.3, 0.04 * expected)
            if abs(value - expected) > tolerance:
                warnings.append(f"{key}: portion {value:g} != {base:g} x {factor:g} ({expected:.2f})")
    missing = [key for key in FIELDS if per_100g.get(key) is None]
    if missing:
        warnings.append("missing per-100g: " + ", ".join(missing))
    return warnings


class ProductPageParser(HTMLParser):
    """Extract JSON-LD, the ``<h1>``, canonical link, nutrition table and ingredients."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.jsonld: list[str] = []
        self.h1 = ""
        self.canonical = ""
        self.rows: list[list[str]] = []
        self.ingredients_text: list[str] = []
        self.bold: list[tuple[str, int]] = []
        self._script: list[str] | None = None
        self._skip = False
        self._armed = False
        self._capturing = False
        self._heading: tuple[str, list[str]] | None = None
        self._section = ""
        self._depth = 0
        self._row: list[str] | None = None
        self._cell: list[str] | None = None
        self._bold: list[str] | None = None
        self._ingredients_len = 0
        self._bold_start = 0

    def _in_ingredients(self) -> bool:
        return self._section.startswith("składniki") and self._heading is None

    def _append_ingredient(self, data: str) -> None:
        self.ingredients_text.append(data)
        self._ingredients_len += len(data)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = dict(attrs)
        if tag == "script" and (attr.get("type") or "").lower() == "application/ld+json":
            self._script = []
        elif tag in ("script", "style"):
            self._skip = True
        elif tag == "link" and (attr.get("rel") or "").lower() == "canonical":
            self.canonical = attr.get("href") or ""
        elif tag in ("h1", "h2", "h3", "h4"):
            self._heading = (tag, [])
        elif tag == "table":
            self._depth += 1
            if self._armed and not self.rows and self._depth == 1:
                self._capturing = True
        elif self._capturing and tag == "tr":
            self._row = []
        elif self._capturing and tag in ("td", "th"):
            self._cell = []
        elif tag == "br":
            if self._cell is not None:
                self._cell.append(" ")
            if self._in_ingredients():
                self._append_ingredient(" ")
        elif tag in ("strong", "b") and self._in_ingredients():
            self._bold = []
            self._bold_start = self._ingredients_len

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self._script is not None:
            self.jsonld.append("".join(self._script))
            self._script = None
        elif tag in ("script", "style"):
            self._skip = False
        elif self._heading and tag == self._heading[0]:
            text = norm_space("".join(self._heading[1]))
            if tag == "h1" and not self.h1:
                self.h1 = text
            self._section = text.lower()
            self._armed = "wartość odżywcza" in self._section or "wartości odżywcze" in self._section
            self._heading = None
        elif tag in ("td", "th") and self._cell is not None:
            if self._row is not None:
                self._row.append(norm_space("".join(self._cell)))
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if any(self._row):
                self.rows.append(self._row)
            self._row = None
        elif tag == "table" and self._depth:
            self._depth -= 1
            if self._capturing and self._depth == 0:
                self._capturing = self._armed = False
        elif tag in ("strong", "b") and self._bold is not None:
            text = norm_space("".join(self._bold)).strip(" ,.;")
            if text:
                self.bold.append((text, self._bold_start))
            self._bold = None

    def handle_data(self, data: str) -> None:
        if self._script is not None:
            self._script.append(data)
            return
        if self._skip:
            return
        if self._heading is not None:
            self._heading[1].append(data)
            return
        if self._cell is not None:
            self._cell.append(data)
        if self._in_ingredients():
            self._append_ingredient(data)
            if self._bold is not None:
                self._bold.append(data)


def extract_jsonld_product(blocks: list[str]) -> dict[str, Any]:
    """Return name/SKU/GTIN/price/availability from the first JSON-LD Product block."""
    for raw in blocks:
        try:
            stack: list[Any] = [json.loads(raw)]
        except json.JSONDecodeError:
            continue
        while stack:
            node = stack.pop()
            if isinstance(node, list):
                stack.extend(node)
                continue
            if not isinstance(node, dict):
                continue
            stack.extend(node.get("@graph", []))
            node_type = node.get("@type")
            if "Product" not in (node_type if isinstance(node_type, list) else [node_type]):
                continue
            offer: Any = node.get("offers") or {}
            if isinstance(offer, list) and offer:
                offer = offer[0]
            offer = offer if isinstance(offer, dict) else {}
            return {
                "name": node.get("name"),
                "sku": node.get("sku"),
                "gtin": node.get("gtin13") or node.get("gtin"),
                "price": offer.get("price"),
                "availability": str(offer.get("availability") or "").rsplit("/", 1)[-1] or None,
            }
    return {}


def extract_allergens(text: str, bold: list[tuple[str, int]]) -> tuple[list[str], list[str]]:
    """Split bold ingredient terms into ``(allergens, may_contain)``.

    ``bold`` holds ``(term, offset)`` pairs where the offset is the character
    position of the term in ``text``. Terms before the "Może zawierać" marker
    are allergens; terms inside that marker's sentence are may-contain items;
    anything after that sentence is ignored. A single term carrying the marker
    prefix is stripped and split into individual items.
    """
    marker = ALLERGEN_MARKER_RE.search(text)
    marker_at = marker.start() if marker else None
    clause_end = len(text)
    if marker:
        end = CLAUSE_END_RE.search(text, marker.end())
        clause_end = end.end() if end else len(text)
    allergens: set[str] = set()
    may_contain: set[str] = set()
    for term, offset in bold:
        if marker_at is not None and offset >= clause_end:
            continue
        cleaned = HEADER_PREFIX_RE.sub("", norm_space(term)).strip(" ,.;")
        if not cleaned:
            continue
        target = may_contain if marker_at is not None and offset >= marker_at else allergens
        for item in re.split(r"\s*,\s*|\s+oraz\s+", cleaned):
            item = item.strip(" ,.;")
            if item:
                target.add(item)
    return sorted(allergens), sorted(may_contain)


@dataclass
class ProductRecord:
    """One product's scraped commerce and nutrition data."""

    source_url: str
    scraped_at: str
    canonical_url: str
    name: str | None
    sku: str | None
    gtin: str | None
    price_pln: float | None
    availability: str | None
    ingredients: str | None
    allergens: list[str]
    may_contain: list[str]
    nutrition_status: str
    nutrition: dict[str, Any] | None
    jsonld_found: bool
    store_api: dict[str, Any] | None
    nutrition_sha256: str


def parse_product_page(url: str, html: str, store: dict[str, Any] | None = None) -> ProductRecord:
    """Parse a product page into a :class:`ProductRecord`.

    Args:
        url: The requested product URL.
        html: The decoded page HTML.
        store: Optional Store API record for clean price/stock/SKU fields.

    Returns:
        A fully populated record; ``nutrition_status`` is ``table``, ``partial``
        or ``missing``.
    """
    parser = ProductPageParser()
    parser.feed(html)
    parser.close()
    jsonld = extract_jsonld_product(parser.jsonld)
    store = store or {}
    raw_ingredients = "".join(parser.ingredients_text)
    text = norm_space(raw_ingredients)
    ingredients_match = re.search(r"Skład(?:niki)?:\s*(.+?)(?=Najlepiej spożyć|Przechowywać|$)", text, re.IGNORECASE)
    allergens, may_contain = extract_allergens(raw_ingredients, parser.bold)
    nutrition = parse_nutrition(parser.rows)
    status = (
        "missing"
        if nutrition is None
        else ("partial" if any(value is None for value in nutrition.per_100g.values()) else "table")
    )
    price = jsonld.get("price") if jsonld.get("price") not in (None, "") else store.get("price")
    in_stock = store.get("in_stock")
    availability = jsonld.get("availability") or (
        None if in_stock is None else ("InStock" if in_stock else "OutOfStock")
    )
    return ProductRecord(
        source_url=url,
        scraped_at=datetime.now(UTC).isoformat(timespec="seconds"),
        canonical_url=parser.canonical,
        name=jsonld.get("name") or parser.h1 or store.get("name"),
        sku=jsonld.get("sku") or store.get("sku"),
        gtin=jsonld.get("gtin"),
        price_pln=float(price) if price not in (None, "") else None,
        availability=availability,
        ingredients=(ingredients_match.group(1).strip() if ingredients_match else text) or None,
        allergens=allergens,
        may_contain=may_contain,
        nutrition_status=status,
        nutrition=asdict(nutrition) if nutrition else None,
        jsonld_found=bool(jsonld),
        store_api=store,
        nutrition_sha256=hashlib.sha256(json.dumps([parser.rows, text], ensure_ascii=False).encode()).hexdigest(),
    )


def iter_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    """Yield records from a JSON Lines file, skipping blank or malformed lines."""
    if not path.exists():
        return
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def write_csv(jsonl_path: Path, csv_path: Path) -> int:
    """Write the latest record per URL from ``jsonl_path`` to a flat CSV.

    Returns:
        The number of CSV rows written.
    """
    latest = {record["source_url"]: record for record in iter_jsonl(jsonl_path)}
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_COLS)
        writer.writeheader()
        for record in latest.values():
            nutrition = record.get("nutrition") or {}
            per_100g = nutrition.get("per_100g", {})
            per_portion = nutrition.get("per_portion", {})
            row = {key: record.get(key) for key in CSV_COLS[:8]}
            row |= {
                "portion_g": nutrition.get("portion_g"),
                "polyols_g": per_100g.get("polyols_g"),
                "warnings": " | ".join(nutrition.get("warnings", [])),
            }
            row |= {key: per_100g.get(key) for key in FIELDS}
            row |= {f"portion_{key}": per_portion.get(key) for key in FIELDS}
            writer.writerow(row)
    return len(latest)


def warn_if_peak() -> None:
    """Warn when the crawl runs outside the polite 01:00-06:00 Warsaw window."""
    try:
        from zoneinfo import ZoneInfo

        hour = datetime.now(ZoneInfo("Europe/Warsaw")).hour
    except Exception:
        return
    if not 1 <= hour < 6:
        LOG.warning("It is %02d:xx in Poland; prefer 01:00-06:00 Europe/Warsaw", hour)


def main(argv: list[str] | None = None) -> int:
    """Run the scrape, resuming from ``records.jsonl`` unless ``--refresh`` is given."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contact", required=True)
    parser.add_argument("--max-requests", type=int, default=800)
    parser.add_argument("--delay", type=float, default=3.0)
    parser.add_argument("--max-age-h", type=float, default=20.0)
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--out", type=Path, default=Path("out"))
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg = Config(
        contact=args.contact,
        delay_s=args.delay,
        max_requests=args.max_requests,
        max_age_h=args.max_age_h,
        out_dir=args.out,
        refresh=args.refresh,
    )
    cfg.out_dir.mkdir(parents=True, exist_ok=True)
    jsonl_path = cfg.out_dir / "records.jsonl"
    warn_if_peak()
    fetcher = PoliteFetcher(cfg)
    done = (
        set() if args.refresh else {record["source_url"] for record in iter_jsonl(jsonl_path) if "source_url" in record}
    )
    try:
        store = probe_store_api(fetcher)
        urls = discover(fetcher, store)
        with jsonl_path.open("a", encoding="utf-8") as out:
            for index, url in enumerate(urls, 1):
                if url in done:
                    continue
                response = fetcher.get(url)
                if response.status != 200:
                    LOG.warning("skip %s (%s)", url, response.status)
                    continue
                record = parse_product_page(url, response.body.decode("utf-8", "replace"), store.get(url))
                if record.canonical_url and not is_product_url(record.canonical_url):
                    LOG.warning("skip %s: canonical %s (discontinued?)", url, record.canonical_url)
                    continue
                out.write(json.dumps(asdict(record), ensure_ascii=False) + "\n")
                out.flush()
                LOG.info("[%d/%d] %s nutrition=%s", index, len(urls), record.name, record.nutrition_status)
    except (BudgetExceeded, BackOff) as exc:
        LOG.warning("stopping early: %s; re-run to resume", exc)
    finally:
        if jsonl_path.exists():
            LOG.info("wrote %d CSV rows", write_csv(jsonl_path, cfg.out_dir / "nutrition.csv"))
        LOG.info("network requests this run: %d", fetcher.requests_made)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
