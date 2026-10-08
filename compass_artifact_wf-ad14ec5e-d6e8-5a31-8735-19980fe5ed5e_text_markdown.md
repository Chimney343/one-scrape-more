# One Day More (onedaymore.pl): site reconnaissance, nutrition dataset and a polite stdlib-only scraper

Every One Day More product page I sampled (`/sklep/<slug>/`) publishes its nutrition facts as an ordinary HTML `<table>`, placed right after an `<h3>Wartość odżywcza</h3>` heading. The pages are rendered on the server and come back complete on a plain GET. The site runs WordPress with WooCommerce. A sequential, standard-library scraper that keys on that heading is enough: no OCR, no headless browser, no async.

**Before the first run:**
- I could not read robots.txt, the sitemap or the `/wp-json` endpoints. My fetch tool refused those URLs; the site did not block them. The scraper reads and obeys all three at runtime, but run `curl -s https://onedaymore.pl/robots.txt` once yourself.
- `enrich_draft` failed twice (an internal error, then a length limit), so this report did not get its source-enrichment pass.

## TL;DR
- **Platform and where the nutrition data is.**
  - WordPress + WooCommerce. Evidence: `woocommerce_*` and `wp-settings` cookies, the custom theme path `/wp-content/themes/main/`, the `mda-cookies` plugin, and Yoast-style SEO meta tags.
  - Nutrition is a table with a label column, a "NA 100 g" column, usually a "NA PORCJĘ X g" column, and on Karma bars an extra "% RWS" column.
  - I found it on every category I sampled: granola, muesli, porridge, bars, nut creams, freeze-dried fruit, crispbread and keto mixes. I did not find any product where the values exist only as an image.
- **Dataset.**
  - Full per-100 g values for 41 products and partial values for 9 more (table below).
  - The shop listing runs to 19 pages of about 15 products each, so roughly 285 listings. Many are refills, multipacks or sets of the same recipe. The brand itself says "ponad 80 produktów".\[1\]
- **Scraper.** One Python 3.11 module, standard library only:
  - obeys robots.txt and its Crawl-delay; waits 3 s plus random jitter of up to 1.5 s between requests over a single connection;
  - has a hard cap on total requests and retries with exponential backoff that honours Retry-After;
  - keeps an on-disk cache using ETag/Last-Modified;
  - writes resumable JSON Lines and a flat CSV, with a pytest test.

## Key Findings

**1. Platform**
- **Shop software:** cookies `woocommerce_cart_hash`, `woocommerce_items_in_cart`, `wp-settings-1` and `wordpress_test_cookie` mean WooCommerce on WordPress.\[2\]\[3\]
- **Theme and agency:** assets load from `/wp-content/themes/main/dist/` and `/wp-content/plugins/mda-cookies/`.\[3\] That points to a custom theme from the Poznań agency MDA, which lists OneDayMore as a client.\[4\]
- **SEO plugin:** the robots meta `index, follow, max-image-preview:large…` and the label "Szacowany czas czytania" look like Yoast.\[5\] So `/sitemap_index.xml` probably exists (inferred, not checked).\[6\]\[7\]
- **Template quality:** the "Idealnie smakuje z:" section prints raw PHP debug output (`array(3) { [0]=> int(2992742) … }`).\[5\] The template is hand-maintained and may change without notice.
- **Operator:** OneDayMore sp. z o.o., ul. Nieszawska 1, 61-021 Poznań, sklep@onedaymore.pl.\[2\]\[3\]\[8\]

**2. robots.txt and sitemaps: not verified.** I have no Disallow, Crawl-delay or Sitemap lines and cannot count sitemap URLs. At runtime the scraper:
- parses robots.txt with `urllib.robotparser` and uses any Crawl-delay it finds;
- then tries any `Sitemap:` lines, else `/sitemap_index.xml` (preferring child sitemaps with "product" in the name), else `/sitemap.xml`;\[6\]\[7\]\[9\]\[10\]
- falls back to the listing pagination if none of those work.

**3. JSON endpoints.**
- WooCommerce's unauthenticated Store API lives at `/wp-json/wc/store/v1/products`.\[11\]
  - It pages with `page` and `per_page` (max 100) and reports totals in the `X-WP-Total` and `X-WP-TotalPages` headers.\[12\]
  - Since WooCommerce 10.6, `per_page=0` (everything at once) returns an error.\[13\]
  - Fields include id, name, permalink, sku, prices, is_in_stock, categories, images and variations.
- I could not confirm whether onedaymore.pl exposes it.
- The nutrition table is page content written by the theme, not a Store API field. So the API is useful for discovery and for SKU, price and stock only; nutrition has to come from the HTML.

**4. Catalogue map.**
- **Product URLs:** `/sklep/<slug>/`. Slugs are not always tidy: `/sklep/musli-proteinowe-nowa-receptura-2/`, `/sklep/musli-ube-450g-egzotyczne-sniadanie-onedaymore/`.\[3\]\[5\] Some no longer match the product name, e.g. `/sklep/granola-czeko-squad` is "Granola ChocoLove 450g".\[14\]
- **Categories:** about 60 links of the form `/sklep/grupa/<slug>/`. They overlap heavily, so deduplicate on product URL.\[3\]
- **Pagination:** `/sklep/page/N/`, plain links over 19 pages, no infinite scroll.\[3\]
- **Variants:** pack sizes are shown inside one page, e.g. "Tuba 450 g", "Refill Pack 450g / 2x450g / 4x450g".\[15\]\[16\] Variant links carry `?pvid=`.
- **Sets:** multipacks and sets also exist as separate products and repeat the same nutrition table.\[17\]\[18\]
- **Discontinued products:** `/sklep/granola-kawowa/` now returns the homepage (canonical `https://onedaymore.pl/`). \[19\] The scraper treats a canonical URL that isn't a product URL as "gone".

**5. Where nutrition lives.** I fetched `/sklep/musli-sylwetkowe/` in full.
- **Page order:** `<h1>` name → "Warto wiedzieć" → "Główne składniki mieszanki" → `<h3>Składniki</h3>` → `<h3>Wartość odżywcza</h3>` → table → footnote and portion note → "Przepisy" → reviews.\[5\]
  - Under Składniki: "Skład: …" with allergens in bold, then "Może zawierać: …".\[5\]
  - The table header row reads `WARTOŚĆ ODŻYWCZA MUSLI | NA 100 g | NA PORCJĘ 80 g`, in bold.\[5\]
- **Selector:** use the first `<table>` after a heading containing "Wartość odżywcza". I could not see the theme's CSS classes, because the tool returned rendered text only. Anchoring on the heading text is the more stable choice anyway.
- **Hidden label repeats:** search snippets show "Wartość odżywcza." repeated before every row, which suggests a hidden label inside each cell.\[20\]\[21\]\[22\] The parser strips it.
- **JSON-LD:** whether the page carries a schema.org Product block is unconfirmed. The scraper uses it if present.

Three table formats appear. All are real HTML text.

| Family | Columns | Quirks |
|---|---|---|
| Granola, muesli, porridge | 100 g + portion of 50–80 g | Header names the product type ("…GRANOLI"). Extra mineral rows with "(21%\*)" RWS. Fat breakdown "TŁUSZCZ, W TYM:" then nasycone / jednonienasycone / wielonienasycone.\[23\]\[24\] Beta-glucan rows |
| Karma bars | 100 g + "NA 44 g (porcja)" + "% RWS\* w porcji" | Omega-3 row |
| Nut creams, freeze-dried fruit, Melvit crispbread, 200 g pouches | Mostly "Na 100 g" only | Mixed case ("Tłuszcz:", "w tym nasycone kwasy tłuszczowe"). POLIOLE row. Energy sometimes printed kcal first |

**6. Other fields available per product.**
- Name (`<h1>`).
- Prices in PLN: sale price, regular price, price per 100 g, and the "Najniższa cena z ostatnich 30 dni" (lowest price in the last 30 days, required under Omnibus rules).\[5\]
- Prices for each variant.\[15\]
- Ingredients, with allergens in `<strong>`, a "Może zawierać" (may contain) list, and storage instructions.\[5\]
- Rating and review count, gallery images, and the breadcrumb category trail.
- **Availability:** the visible text is unreliable. "Brak w magazynie" (out of stock) sits in a hidden "notify me" block even when the "add to cart" button is shown.\[5\] Take stock status from JSON-LD or the Store API instead.
- **SKU and EAN:** not shown on the page. Third-party retailers list EANs (e.g. 5902884463214 for Musli Sylwetkowe).\[25\]

**7. Terms of service.**
- The shop terms (`/nowy-regulamin/`, dated 25 April 2025) §III.2 require users to use the shop "w sposób niezakłócający jego funkcjonowania" (in a way that doesn't disrupt it). They also limit use of all content "jedynie w zakresie własnego użytku osobistego" (to one's own personal use).\[26\]
- That page is set to noindex and may not be the canonical copy.\[26\]
- I could not open the 2026/05 site-terms PDF, so I don't know whether it contains an explicit anti-scraping clause.\[2\]

**8. Anti-bot measures.**
- None observed: no Cloudflare, no CAPTCHA, no content rendered only by JavaScript, no age gate.
- The cookie banner and the "Antyspam" modal are overlays in the same HTML, not walls.\[3\]\[5\]
- Countdown widgets change the HTML on every load.\[5\] Change detection should therefore hash only the nutrition and ingredients text, not the whole page.

## Extracted dataset (per 100 g)
Values are as printed on onedaymore.pl on 8 Oct 2026. Musli Sylwetkowe comes from the full page fetch; the other rows come from search-indexed page text. All amounts except energy are grams. ⚠ marks a problem on the site's own label.

| Product | kJ | kcal | Fat | Sat | Carb | Sugar | Fibre | Prot | Salt | Portion | Notes |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Granola Marcińska 500g | 1705 | 406 | 11.1 | 2.9 | 63.9 | 24 | 8 | 8.9 | 0.12 | 60 g | ⚠ fibre per portion 6.4 ≠ 4.8\[27\] |
| Granola Owocowa 450g | 1650 | 395 | 8.4 | 1.3 | 62.9 | 15.4 | 10.3 | 11.3 | 0.13 | 80 g |\[23\] |
| Granola Kawowa 450g | 1647 | 393 | 5.9 | 2.1 | 69.6 | 13.9 | 8.9 | 10.2 | 0.02 | 80 g | ⚠ URL now redirects to homepage\[19\] |
| Granola cynamonowa z orzechami 450g | 1783 | 426 | 14.7 | 1.8 | 55.2 | 8.6 | 8.7 | 13.4 | 0.12 | 80 g |\[24\] |
| Granola Proteinowa NR 400g | 1750 | 419 | 14.5 | 2.3 | 39.3 | 12.1 | 9.1 | 28 | 0.20 | 80 g |\[20\] |
| Granola KETO orzechowa 450g | 2460 | 595 | 46.6 | 5.4 | 17.1 | 13.6 | 9.3 | 22.1 | 0.45 | 50 g |\[28\] |
| Granola ChocoLove 450g | 1640 | 391 | 7.4 | 3.2 | 66.8 | 15 | 9.7 | 9.3 | 0.04 | 65 g |\[14\] |
| Granola Funny Honey 400g | 1600 | 380 | 4.6 | 0.9 | 68.2 | 9.3 | 9.7 | 11.3 | 0.02 | 60 g | ⚠ portion values fit 65 g, not 60 g\[29\] |
| Musli Odpornościowe 400g | 1750 | 418 | 14.6 | 2.1 | 54 | 11.1 | 8.3 | 13.1 | 0.08 | 80 g |\[21\] |
| Musli Ube | 1677 | 400 | 13.6 | 7.5 | 58.9 | 19.5 | 7.5 | 8.3 | 0.17 | 80 g |\[30\] |
| Musli Sylwetkowe 450g | 1425 | 341 | 3.6 | 0.8 | 60.9 | 15 | 12.6 | 10.4 | 0.12 | 80 g | full page fetch\[5\] |
| Musli Męskie 450g | 1840 | 441 | 19.8 | 4.2 | 44.4 | 13.2 | 9.4 | 16.6 | 0 | 80 g |\[31\] |
| Musli Proteinowe NR 400g | 1670 | 399 | 11.2 | 1.9 | 48.5 | 15.6 | 9.3 | 20.8 | 0.30 | 80 g |\[32\] |
| Musli Health Protein 200g | 1700 | 405 | 12.8 | 2 | 45.9 | 10.6 | 10.7 | 21 | 0.33 | 50 g | salt printed "0, 33 g"\[33\] |
| Musli Codzienne 500g | 1626 | 387 | 11.2 | 1.7 | 52.7 | 8.3 | 10.4 | 13.7 | 0.05 | 80 g |\[34\] |
| Musli Superfoods Energy 480g | 1760 | 421 | 15.4 | 3 | 52.8 | 10.1 | 9.6 | 12.9 | 0.04 | 80 g |\[22\] |
| Musli Superfoods Active 480g | 1710 | 407 | 12.6 | 2.8 | 57.2 | 15.6 | 9.1 | 11.5 | 0.16 | 80 g |\[35\] |
| Muesli PowerUp | 1800 | 431 | 16.6 | 3.7 | 52.9 | 15.4 | 19.5 | 12.6 | 0.31 | 80 g | ⚠ portion 1400 kJ ≠ 1440\[36\] |
| Owsianka pomarańczowa 450g | 1545 | 368 | 7.2 | 2.4 | 56.2 | 7.2 | 13.6 | 12.5 | 0.03 | 65 g | "kcall" typo\[37\] |
| Owsianka Podwójnie Czekoladowa 450g | 1620 | 386 | 8.6 | 3.1 | 58.8 | 12.6 | 10.9 | 12.5 | 0.02 | 65 g |\[38\] |
| Owsianka Proteinowa jabłkowa 450g | 1570 | 372 | 5.5 | 1.5 | 55.5 | 14.6 | 8.1 | 21 | 0.23 | 65 g |\[39\] |
| Owsianka Proteinowa bananowa 450g | 1565 | 371 | 5.6 | 1.7 | 54.7 | 8.6 | 8.3 | 21.2 | 0.28 | 65 g |\[40\] |
| Owsianka fińska truskawka-figa | 1510 | 359 | 5.8 | 1.2 | 60 | 15.3 | 11.2 | 11.2 | 0.11 | 65 g |\[41\] |
| Owsianka Prot. jabłko-wanilia 200g pouch | 398 | 95 | 0.9 | 0.3 | 15.9 | 10.2 | 2.2 | 5.1 | 0.09 | pack | ⚠ multipack pages differ\[42\]\[43\] |
| Owsianka Prot. truskawka 200g pouch | 385 | 92 | 0.9 | 0.3 | 15 | 9.7 | 2.2 | 5.1 | 0.04 | pack | ⚠ 3×200g page differs\[44\]\[45\] |
| Krem czekoladowy o smaku orzechowym | 2222 | 531 | 42 | 7.6 | 31 | 3.1 | 6.6 | 14 | 0.01 | 50 g | polyols 25\[46\] |
| Krem orzeszki ziemne + laskowe 500g | 2188 | 523 | 40.1 | 7.5 | 34 | 6.1 | 7.4 | 12.9 | 0.02 | – | polyols 25\[47\] |
| Baton ZREGENERUJ SIĘ Karma 44g | 2367 | 566 | 41 | 5.6 | 30 | 27 | 7.5 | 16 | 0.51 | 44 g | 4-column table with % RWS\[48\] |
| Baton NAŁADUJ SIĘ Karma 40g | 2408 | 576 | 44 | 3.6 | 28 | 24 | 6.8 | 14 | 0.52 | 40 g |\[49\] |
| Baton ZADBAJ O SIEBIE Karma 35g | 2358 | 564 | 43 | 20 | 28 | 25 | 9.8 | 13 | 0.54 | 35 g | ⚠ Mn "0,62 g" (should be mg)\[50\] |
| Mix Keto Matcha 600g | 2550 | 617 | 52.6 | 8.6 | 4.6 | 0.4 | 13 | 25 | 0.04 | 60 g |\[51\] |
| Snack Fruity 200g | 2385 | 576 | 44.8 | n/a | 18.3 | 12.8 | 10.4 | 22.3 | 0.36 | 50 g |\[52\] |
| Liofilizowane żurawiny 60g | 1381 | 330 | 3.3 | 0.0 | 55 | 29 | 34 | 2.9 | 0.04 | – |\[53\] |
| Truskawki liofilizowane | 1332 | 316 | 2.1 | 0.4 | 58 | 49.2 | 19.6 | 6.5 | n/a | 15 g |\[54\] |
| Crispy żytni pomidor-bazylia 130g (Melvit) | 1911 | 457 | 19.3 | 2.5 | 51 | 5.1 | 9.4 | 15 | 2.65 | – | third-party brand\[55\] |
| Crispy Protein 130g (Melvit) | 2095 | 503 | 29.9 | 3.6 | 20 | 1.8 | 11 | 33 | 1.8 | – |\[56\] |
| Crispy żytnie kozi ser 130g | 1851 | 444 | 22 | 7.8 | 37 | 2.5 | 17 | 16 | 2.6 | – |\[57\] |
| Crispy orkiszowy 130g | 1945 | 467 | 24.3 | 3.7 | 36 | 5.5 | 16 | 18 | 1.83 | – |\[58\] |
| Fit Grain Crispy żytni 150g | 1813 | 434 | 20 | 2.7 | 45 | 8.8 | 13 | 12 | 2.7 | – |\[59\] |
| Crispy pszenny ser-cebulka 130g | 1860 | 445 | 21 | 6.2 | 41 | 5.7 | 10 | 18 | n/a | – |\[60\] |
| Crispy żytni vegan 130g | 1840 | 441 | 21 | 2.5 | 42 | 6.2 | 16 | 13 | n/a | – |\[61\] |

**Partial values only.** The search excerpts were cut off; the full tables exist on the pages and the scraper will capture them:

| Product | Values found (per 100 g) |
|---|---|
| Pasta Smooth 1 kg | 2526 kJ / 611 kcal; fat 50; sat 5.8; carbs 7.1; sugars 6.8\[62\] |
| Krem słony karmel | 2205 kJ / 527 kcal; fat 40.9\[63\] |
| Owsianka Owocowa | 1493 kJ / 357 kcal\[64\] |
| Owsianka Karmelowa | 1653 kJ / 396 kcal\[15\] |
| Owsianka Bananowa | 1540 kJ / 367 kcal\[16\] |
| Owsianka Proteinowa czekoladowa | 1620 kJ / 385 kcal\[65\] |
| Owsianka Jabłkowa | 1510 kJ / 361 kcal\[17\] |
| Baton UŚMIECHNIJ SIĘ | 2247 kJ / 537 kcal\[66\] |
| Granola Świąteczna | 1670 kJ / 399 kcal\[67\] |

**No table expected:** accessories (Lunchpot) and the Starter Pack builder. The scraper records these as `nutrition_status="missing"`.

## Scraper design and trade-offs
| Choice | Reason | Alternative |
|---|---|---|
| Nutrition from the product HTML; discovery from Store API → sitemap → listing pages | Nutrition exists only in the HTML; the API gives clean commerce fields | API-only would miss nutrition |
| JSON-LD for price, stock and GTIN if present, otherwise `<h1>` | Cleanest source when it exists | — |
| Standard library only | One table per page, found by its heading, is easy with `HTMLParser` | `requests`/`httpx` add keep-alive, and `selectolax`/`lxml` parse faster. Neither matters when every request waits 3 s or more |
| Synchronous, one connection | The politeness delay is the bottleneck, not I/O | Async adds complexity for no gain |
| Delay of max(3 s, Crawl-delay) plus up to 1.5 s jitter | About 330 requests ≈ 20 minutes | — |
| Backoff that honours Retry-After; stop if the server keeps answering 429/503 or asks to wait more than 15 min | Respects the server's request to slow down | — |
| ETag/Last-Modified cache plus a `max_age` shortcut | WordPress often sends no validators, so `max_age` does most of the saving | SQLite cache |
| JSON Lines (raw, append-only, used for resume) + CSV (flat) | Crash-safe, easy to diff, opens in pandas or Excel | SQLite if you refresh regularly and want history |
| Store sanity-check warnings instead of correcting values | The site's own labels contain errors (see table) | — |
| Hash only nutrition + ingredients for change detection | The countdown widgets change the full HTML on every load | — |

Crawl between 01:00 and 06:00 Warsaw time; the script warns otherwise. Put a real contact address in the User-Agent. The default hard cap is 800 requests.

```python
#!/usr/bin/env python3
"""Polite, stdlib-only scraper for onedaymore.pl products + nutrition.
Usage: python odm_scraper.py --contact you@example.com [--max-requests 800] [--refresh]"""
from __future__ import annotations
import argparse, csv, email.utils, hashlib, json, logging, random, re, time
import urllib.error, urllib.parse, urllib.request, urllib.robotparser
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterator

BASE = "https://onedaymore.pl"
SM_NS = "{http://www.sitemaps.org/schemas/sitemap/0.9}"
log = logging.getLogger("odm")

@dataclass(frozen=True)
class Config:
    contact: str
    delay_s: float = 3.0
    jitter_s: float = 1.5
    timeout_s: float = 20.0
    max_requests: int = 800
    max_retries: int = 4
    max_age_h: float = 20.0
    cache_dir: Path = Path("cache")
    out_dir: Path = Path("out")
    @property
    def user_agent(self) -> str:
        return f"odm-nutrition-research/1.0 (+mailto:{self.contact}; single connection, obeys robots.txt)"

class BudgetExceeded(RuntimeError): ...
class BackOff(RuntimeError): ...

@dataclass
class Response:
    url: str
    status: int            # 0 network failure, -1 disallowed by robots
    body: bytes
    from_cache: bool = False

class PoliteFetcher:
    def __init__(self, cfg: Config) -> None:
        self.cfg, self.requests_made, self._last, self.delay = cfg, 0, 0.0, cfg.delay_s
        cfg.cache_dir.mkdir(parents=True, exist_ok=True)
        self.robots = urllib.robotparser.RobotFileParser(f"{BASE}/robots.txt")
        r = self._fetch(f"{BASE}/robots.txt")
        if r.status == 200:
            self.robots.parse(r.body.decode("utf-8", "replace").splitlines())
        elif r.status in (401, 403):
            self.robots.disallow_all = True
        else:
            self.robots.allow_all = True
        self.robots.modified()
        cd = self.robots.crawl_delay(cfg.user_agent)
        if cd:
            self.delay = max(self.delay, float(cd))
        log.info("robots status=%s delay=%.1fs sitemaps=%s", r.status, self.delay, self.robots.site_maps())

    def get(self, url: str) -> Response:
        if not self.robots.can_fetch(self.cfg.user_agent, url):
            log.warning("robots.txt disallows %s", url)
            return Response(url, -1, b"")
        return self._fetch(url)

    def _paths(self, url: str) -> tuple[Path, Path]:
        k = hashlib.sha256(url.encode()).hexdigest()[:32]
        return self.cfg.cache_dir / f"{k}.json", self.cfg.cache_dir / f"{k}.body"

    def _fetch(self, url: str) -> Response:
        meta_p, body_p = self._paths(url)
        meta: dict[str, Any] | None = None
        if meta_p.exists() and body_p.exists():
            meta = json.loads(meta_p.read_text(encoding="utf-8"))
            if time.time() - meta["fetched_at"] < self.cfg.max_age_h * 3600:
                return Response(url, meta["status"], body_p.read_bytes(), True)
        headers = {"User-Agent": self.cfg.user_agent, "Accept-Language": "pl-PL,pl;q=0.9",
                   "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8"}
        if meta and meta.get("etag"):
            headers["If-None-Match"] = meta["etag"]
        if meta and meta.get("last_modified"):
            headers["If-Modified-Since"] = meta["last_modified"]
        status = 0
        for attempt in range(self.cfg.max_retries + 1):
            self._throttle()
            try:
                with urllib.request.urlopen(urllib.request.Request(url, headers=headers),
                                            timeout=self.cfg.timeout_s) as resp:
                    status, body, hdrs = resp.status, resp.read(), resp.headers
            except urllib.error.HTTPError as e:
                status, body, hdrs = e.code, e.read(), e.headers
            except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
                log.warning("network error %s: %s", url, e)
                self._backoff(attempt, None)
                continue
            if status == 304 and meta:
                meta["fetched_at"] = time.time()
                meta_p.write_text(json.dumps(meta), encoding="utf-8")
                return Response(url, meta["status"], body_p.read_bytes(), True)
            if status in (429, 500, 502, 503, 504):
                self._backoff(attempt, hdrs.get("Retry-After"))
                continue
            if status == 200:
                body_p.write_bytes(body)
                meta_p.write_text(json.dumps({"url": url, "status": 200, "fetched_at": time.time(),
                    "etag": hdrs.get("ETag"), "last_modified": hdrs.get("Last-Modified")}), encoding="utf-8")
            return Response(url, status, body)
        if status in (429, 503):
            raise BackOff(f"server keeps answering {status} for {url}")
        return Response(url, status, b"")

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
                ra = float(retry_after)
            except ValueError:
                try:
                    ra = (email.utils.parsedate_to_datetime(retry_after) - datetime.now(timezone.utc)).total_seconds()
                except (TypeError, ValueError):
                    ra = 0.0
            if ra > 900:
                raise BackOff(f"Retry-After={retry_after!r} > 15 min; resume later")
            delay = max(delay, ra)
        log.info("backing off %.0fs", delay)
        time.sleep(delay + random.uniform(0, 1))

# ---------------- discovery
def canonical(url: str) -> str:
    p = urllib.parse.urlsplit(urllib.parse.urljoin(BASE + "/", url))
    return f"https://{p.netloc}{p.path if p.path.endswith('/') else p.path + '/'}"

def is_product_url(url: str) -> bool:
    p = urllib.parse.urlsplit(url)
    parts = [s for s in p.path.split("/") if s]
    return (p.netloc in ("onedaymore.pl", "www.onedaymore.pl") and len(parts) == 2
            and parts[0] == "sklep" and parts[1] not in ("grupa", "page"))

def iter_sitemap(f: PoliteFetcher, url: str, depth: int = 0) -> Iterator[str]:
    r = f.get(url)
    if r.status != 200 or depth > 2:
        return
    try:
        root = ET.fromstring(r.body)
    except ET.ParseError:
        return
    locs = [el.text.strip() for el in root.iter(f"{SM_NS}loc") if el.text]
    if root.tag == f"{SM_NS}sitemapindex":
        for child in [u for u in locs if "product" in u] or locs:
            yield from iter_sitemap(f, child, depth + 1)
    else:
        yield from locs

def probe_store_api(f: PoliteFetcher, max_pages: int = 10) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for page in range(1, max_pages + 1):
        r = f.get(f"{BASE}/wp-json/wc/store/v1/products?per_page=100&page={page}")
        try:
            batch = json.loads(r.body) if r.status == 200 else None
        except json.JSONDecodeError:
            batch = None
        if not isinstance(batch, list):
            break
        for p in batch:
            pr = p.get("prices") or {}
            minor, price = int(pr.get("currency_minor_unit") or 2), pr.get("price")
            out[canonical(p.get("permalink", ""))] = {
                "id": p.get("id"), "sku": p.get("sku") or None, "name": p.get("name"),
                "price": int(price) / 10 ** minor if price not in (None, "") else None,
                "in_stock": p.get("is_in_stock"),
                "categories": [c.get("name") for c in p.get("categories", [])],
                "images": [i.get("src") for i in p.get("images", [])],
                "variations": p.get("variations", [])}
        if len(batch) < 100:
            break
    return out

class LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: set[str] = set()
    def handle_starttag(self, tag, attrs):
        href = dict(attrs).get("href")
        if tag == "a" and href:
            self.links.add(urllib.parse.urljoin(BASE + "/", href))

def iter_listing(f: PoliteFetcher, max_pages: int = 60) -> Iterator[str]:
    seen: set[str] = set()
    for page in range(1, max_pages + 1):
        r = f.get(f"{BASE}/sklep/" if page == 1 else f"{BASE}/sklep/page/{page}/")
        if r.status != 200:
            break
        lp = LinkParser()
        lp.feed(r.body.decode("utf-8", "replace"))
        new = {canonical(u) for u in lp.links if is_product_url(u)} - seen
        if not new:
            break
        seen |= new
        yield from sorted(new)

def discover(f: PoliteFetcher, store: dict[str, dict[str, Any]]) -> list[str]:
    urls = {u for u in store if is_product_url(u)}
    for sm in f.robots.site_maps() or [f"{BASE}/sitemap_index.xml", f"{BASE}/sitemap.xml"]:
        found = {canonical(u) for u in iter_sitemap(f, sm) if is_product_url(u)}
        urls |= found
        if found:
            break
    if not urls:
        urls |= set(iter_listing(f))
    log.info("discovered %d product URLs", len(urls))
    return sorted(urls)

# ---------------- parsing
NUMBER = r"\d{1,3}(?:[ \u00a0]\d{3})+(?:[.,]\d+)?|\d+(?:\s*[.,]\s*\d+)?"
FIELDS = ("energy_kj", "energy_kcal", "fat_g", "saturated_fat_g", "carbs_g",
          "sugars_g", "fibre_g", "protein_g", "salt_g")

def norm_space(s: str) -> str:
    return re.sub(r"\s+", " ", s.replace("\xa0", " ")).strip()

def to_float(s: str) -> float:
    return float(re.sub(r"\s", "", s).replace(",", "."))

@dataclass
class Amount:
    value: float | None
    unit: str | None = None
    less_than: bool = False

def parse_amount(text: str) -> Amount:
    t = re.sub(r"\(.*?\)", " ", text.replace("\xa0", " "))
    m = re.search(rf"(<|≤|poniżej)?\s*({NUMBER})\s*(mg|µg|μg|g)?(?![a-ząćęłńóśźż])", t, re.I)
    if not m:
        return Amount(None)
    return Amount(to_float(m.group(2)), (m.group(3) or "").lower() or None, bool(m.group(1)))

def parse_energy(text: str) -> tuple[float | None, float | None]:
    t = text.replace("\xa0", " ")
    kj = re.search(rf"({NUMBER})\s*kj", t, re.I)
    kcal = re.search(rf"({NUMBER})\s*k?cal", t, re.I)
    return (to_float(kj.group(1)) if kj else None, to_float(kcal.group(1)) if kcal else None)

def classify(label: str) -> str | None:
    s = re.sub(r"^wartość odżywcza\s*", "", norm_space(label).lower())
    s = re.sub(r"^w tym:?\s*", "", s).strip(" :,*")
    if "energ" in s: return "energy"
    if "jednonienasyc" in s: return "monounsaturated_fat_g"
    if "wielonienasyc" in s: return "polyunsaturated_fat_g"
    if "nasycon" in s: return "saturated_fat_g"
    if s.startswith("tłuszcz"): return "fat_g"
    if "cukr" in s: return "sugars_g"
    if "poliol" in s: return "polyols_g"
    for prefix, key in (("węglowodan", "carbs_g"), ("błonnik", "fibre_g"),
                        ("białk", "protein_g"), ("sól", "salt_g")):
        if s.startswith(prefix):
            return key
    return None

@dataclass
class Nutrition:
    per_100g: dict[str, float | None] = field(default_factory=dict)
    per_portion: dict[str, float | None] = field(default_factory=dict)
    portion_g: float | None = None
    extras: dict[str, dict[str, str]] = field(default_factory=dict)
    less_than: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

def parse_nutrition(rows: list[list[str]]) -> Nutrition | None:
    if not rows:
        return None
    n, col100, colp = Nutrition(), 1, 2
    if classify(rows[0][0]) is None and any("100" in c for c in rows[0][1:]):
        head = [c.lower() for c in rows[0]]
        col100 = next((i for i, c in enumerate(head) if "100" in c), 1)
        colp = next((i for i, c in enumerate(head)
                     if i != col100 and ("porcj" in c or re.search(r"na\s*\d", c))), None)
        if colp is not None:
            m = re.search(rf"({NUMBER})\s*g", head[colp])
            n.portion_g = to_float(m.group(1)) if m else None
        rows = rows[1:]
    for row in rows:
        if len(row) <= col100:
            continue
        cells = {"100": row[col100], "portion": row[colp] if colp is not None and colp < len(row) else ""}
        key = classify(row[0])
        if key is None:
            n.extras[row[0]] = cells
            continue
        for where, target in (("100", n.per_100g), ("portion", n.per_portion)):
            if not cells[where]:
                continue
            if key == "energy":
                target["energy_kj"], target["energy_kcal"] = parse_energy(cells[where])
                continue
            a = parse_amount(cells[where])
            if a.unit == "mg" and a.value is not None:
                a.value /= 1000
            target[key] = a.value
            if a.less_than:
                n.less_than.append(f"{key}@{where}")
    for k in FIELDS:
        n.per_100g.setdefault(k, None)
    n.warnings = sanity_check(n)
    return n

def sanity_check(n: Nutrition) -> list[str]:
    w, p = [], n.per_100g
    kj, kcal = p.get("energy_kj"), p.get("energy_kcal")
    if kj is None or kcal is None:
        w.append("energy: kJ or kcal missing")
    elif kcal and abs(kj / kcal - 4.184) > 0.15:
        w.append(f"energy: kJ/kcal ratio {kj / kcal:.2f}")
    for part, whole in (("saturated_fat_g", "fat_g"), ("sugars_g", "carbs_g")):
        if p.get(part) is not None and p.get(whole) is not None and p[part] > p[whole] + 0.05:
            w.append(f"{part} > {whole}")
    if n.portion_g:
        fct = n.portion_g / 100
        for k, v in n.per_portion.items():
            base = p.get(k)
            if v is None or base is None:
                continue
            exp = base * fct
            tol = 0.025 * exp if k.startswith("energy") else max(0.3, 0.04 * exp)
            if abs(v - exp) > tol:
                w.append(f"{k}: portion {v:g} != {base:g} x {fct:g} ({exp:.2f})")
    missing = [k for k in FIELDS if p.get(k) is None]
    if missing:
        w.append("missing per-100g: " + ", ".join(missing))
    return w

class ProductPageParser(HTMLParser):
    """JSON-LD, <h1>, canonical, first table after a 'Wartość odżywcza' heading, 'Składniki' section."""
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.jsonld: list[str] = []
        self.h1 = self.canonical = ""
        self.rows: list[list[str]] = []
        self.ingredients_text: list[str] = []
        self.bold: list[tuple[str, bool]] = []
        self._script: list[str] | None = None
        self._skip = self._armed = self._capturing = False
        self._heading: tuple[str, list[str]] | None = None
        self._section, self._depth = "", 0
        self._row: list[str] | None = None
        self._cell: list[str] | None = None
        self._bold: list[str] | None = None

    def _in_ingr(self) -> bool:
        return self._section.startswith("składniki") and self._heading is None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "script" and (a.get("type") or "").lower() == "application/ld+json":
            self._script = []
        elif tag in ("script", "style"):
            self._skip = True
        elif tag == "link" and (a.get("rel") or "").lower() == "canonical":
            self.canonical = a.get("href") or ""
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
            if self._in_ingr():
                self.ingredients_text.append(" ")
        elif tag in ("strong", "b") and self._in_ingr():
            self._bold = []

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        if tag == "script" and self._script is not None:
            self.jsonld.append("".join(self._script)); self._script = None
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
                self.bold.append((text, "może zawierać" in "".join(self.ingredients_text).lower()))
            self._bold = None

    def handle_data(self, data):
        if self._script is not None:
            self._script.append(data); return
        if self._skip:
            return
        if self._heading is not None:
            self._heading[1].append(data); return
        if self._cell is not None:
            self._cell.append(data)
        if self._in_ingr():
            self.ingredients_text.append(data)
            if self._bold is not None:
                self._bold.append(data)

def extract_jsonld_product(blocks: list[str]) -> dict[str, Any]:
    for raw in blocks:
        try:
            stack: list[Any] = [json.loads(raw)]
        except json.JSONDecodeError:
            continue
        while stack:
            node = stack.pop()
            if isinstance(node, list):
                stack.extend(node); continue
            if not isinstance(node, dict):
                continue
            stack.extend(node.get("@graph", []))
            t = node.get("@type")
            if "Product" not in (t if isinstance(t, list) else [t]):
                continue
            offer = node.get("offers") or {}
            offer = offer[0] if isinstance(offer, list) and offer else offer
            offer = offer if isinstance(offer, dict) else {}
            return {"name": node.get("name"), "sku": node.get("sku"),
                    "gtin": node.get("gtin13") or node.get("gtin"),
                    "price": offer.get("price"),
                    "availability": str(offer.get("availability") or "").rsplit("/", 1)[-1] or None}
    return {}

@dataclass
class ProductRecord:
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
    pp = ProductPageParser(); pp.feed(html); pp.close()
    ld, st = extract_jsonld_product(pp.jsonld), store or {}
    text = norm_space("".join(pp.ingredients_text))
    m = re.search(r"Skład(?:niki)?:\s*(.+?)(?=Najlepiej spożyć|Przechowywać|$)", text, re.I)
    bold = [(t, may) for t, may in pp.bold if not re.search(r"skład|zawierać", t, re.I)]
    nut = parse_nutrition(pp.rows)
    status = "missing" if nut is None else ("partial" if any(v is None for v in nut.per_100g.values()) else "table")
    price = ld.get("price") if ld.get("price") not in (None, "") else st.get("price")
    ins = st.get("in_stock")
    return ProductRecord(
        source_url=url, scraped_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        canonical_url=pp.canonical, name=ld.get("name") or pp.h1 or st.get("name"),
        sku=ld.get("sku") or st.get("sku"), gtin=ld.get("gtin"),
        price_pln=float(price) if price not in (None, "") else None,
        availability=ld.get("availability") or (None if ins is None else ("InStock" if ins else "OutOfStock")),
        ingredients=(m.group(1).strip() if m else text) or None,
        allergens=sorted({t for t, may in bold if not may}),
        may_contain=sorted({t for t, may in bold if may}),
        nutrition_status=status, nutrition=asdict(nut) if nut else None,
        jsonld_found=bool(ld), store_api=store,
        nutrition_sha256=hashlib.sha256(json.dumps([pp.rows, text], ensure_ascii=False).encode()).hexdigest())

# ---------------- output
CSV_COLS = ["source_url", "scraped_at", "name", "sku", "gtin", "price_pln", "availability",
            "nutrition_status", "portion_g", *FIELDS, "polyols_g",
            *[f"portion_{k}" for k in FIELDS], "warnings"]

def iter_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    if not path.exists():
        return
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue

def write_csv(jsonl: Path, csv_path: Path) -> int:
    latest = {r["source_url"]: r for r in iter_jsonl(jsonl)}
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=CSV_COLS); w.writeheader()
        for rec in latest.values():
            nut = rec.get("nutrition") or {}
            p100, ppor = nut.get("per_100g", {}), nut.get("per_portion", {})
            row = {k: rec.get(k) for k in CSV_COLS[:8]}
            row |= {"portion_g": nut.get("portion_g"), "polyols_g": p100.get("polyols_g"),
                    "warnings": " | ".join(nut.get("warnings", []))}
            row |= {k: p100.get(k) for k in FIELDS}
            row |= {f"portion_{k}": ppor.get(k) for k in FIELDS}
            w.writerow(row)
    return len(latest)

def warn_if_peak() -> None:
    try:
        from zoneinfo import ZoneInfo
        h = datetime.now(ZoneInfo("Europe/Warsaw")).hour
    except Exception:
        return
    if not 1 <= h < 6:
        log.warning("It is %02d:xx in Poland; prefer 01:00-06:00 Europe/Warsaw", h)

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--contact", required=True)
    ap.add_argument("--max-requests", type=int, default=800)
    ap.add_argument("--delay", type=float, default=3.0)
    ap.add_argument("--max-age-h", type=float, default=20.0)
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--out", type=Path, default=Path("out"))
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg = Config(contact=a.contact, delay_s=a.delay, max_requests=a.max_requests,
                 max_age_h=a.max_age_h, out_dir=a.out)
    cfg.out_dir.mkdir(parents=True, exist_ok=True)
    jsonl = cfg.out_dir / "records.jsonl"
    warn_if_peak()
    f = PoliteFetcher(cfg)
    done = set() if a.refresh else {r["source_url"] for r in iter_jsonl(jsonl) if "source_url" in r}
    try:
        store = probe_store_api(f)
        urls = discover(f, store)
        with jsonl.open("a", encoding="utf-8") as out:
            for i, url in enumerate(urls, 1):
                if url in done:
                    continue
                r = f.get(url)
                if r.status != 200:
                    log.warning("skip %s (%s)", url, r.status); continue
                rec = parse_product_page(url, r.body.decode("utf-8", "replace"), store.get(url))
                if rec.canonical_url and not is_product_url(rec.canonical_url):
                    log.warning("skip %s: canonical %s (discontinued?)", url, rec.canonical_url); continue
                out.write(json.dumps(asdict(rec), ensure_ascii=False) + "\n"); out.flush()
                log.info("[%d/%d] %s nutrition=%s", i, len(urls), rec.name, rec.nutrition_status)
    except (BudgetExceeded, BackOff) as e:
        log.warning("stopping early: %s; re-run to resume", e)
    finally:
        if jsonl.exists():
            log.info("wrote %d CSV rows", write_csv(jsonl, cfg.out_dir / "nutrition.csv"))
        log.info("network requests this run: %d", f.requests_made)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
```

**Test (`test_odm_scraper.py`)**

The HTML fragment copies the structure of `/sklep/musli-sylwetkowe/`. To run: `python -m pytest -q`, then `python odm_scraper.py --contact you@example.com`.

```python
import pytest
from odm_scraper import parse_amount, parse_energy, parse_nutrition, parse_product_page

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

def test_page():
    r = parse_product_page("https://onedaymore.pl/sklep/musli-sylwetkowe/", PAGE)
    n = r.nutrition
    assert r.name == "Musli Sylwetkowe 450g" and r.nutrition_status == "table" and n["portion_g"] == 80
    assert n["per_100g"] == {"energy_kj": 1425, "energy_kcal": 341, "fat_g": 3.6, "saturated_fat_g": 0.8,
        "carbs_g": 60.9, "sugars_g": 15, "fibre_g": 12.6, "protein_g": 10.4, "salt_g": 0.12}
    assert n["per_portion"]["energy_kcal"] == 273 and "CYNK" in n["extras"] and n["warnings"] == []
    assert r.allergens == ["jęczmienne", "owsiane"] and r.may_contain == ["mleko", "orzeszki ziemne"]

def test_portion_mismatch_flagged():
    n = parse_nutrition([["WARTOŚĆ ODŻYWCZA", "NA 100 g", "NA PORCJĘ 60 g"],
                         ["WARTOŚĆ ENERGETYCZNA", "1705 kJ / 406 kcal", "1023 kJ / 244 kcal"], ["BŁONNIK", "8 g", "6,4 g"]])
    assert any(w.startswith("fibre_g: portion") for w in n.warnings)

def test_100g_only_and_rws():
    m = parse_nutrition([["Wartość odżywcza", "Na 100 g"], ["Tłuszcz:", "19,3 g"],
                         ["w tym nasycone kwasy tłuszczowe", "2,5 g"]])
    assert m.portion_g is None and m.per_100g["saturated_fat_g"] == 2.5
    k = parse_nutrition([["WARTOŚĆ ODŻYWCZA", "NA 100 g", "NA 44 g (porcja)", "% RWS* w porcji"], ["TŁUSZCZ", "41 g", "18 g", "26%"]])
    assert k.portion_g == 44 and k.per_portion["fat_g"] == 18

@pytest.mark.parametrize("t,v,u,lt", [("12,5 g", 12.5, "g", False), ("<0,5 g", 0.5, "g", True),
    ("0, 33 g", 0.33, "g", False), ("2,07 mg (21%*)", 2.07, "mg", False)])
def test_amount(t, v, u, lt):
    a = parse_amount(t); assert (a.value, a.unit, a.less_than) == (v, u, lt)

@pytest.mark.parametrize("t,kj,kcal", [("1850 kJ / 442 kcal", 1850, 442), ("1040kJ/228kcal", 1040, 228),
    ("531 kcal/ 2222 kJ", 2222, 531), ("1545 kJ/368 kcall", 1545, 368), ("394 kJ / 93 cal", 394, 93)])
def test_energy(t, kj, kcal):
    assert parse_energy(t) == (kj, kcal)
```

## When X happens, consider Y
- **Tables become JavaScript-rendered** (the heading is there but the parser finds no rows): first look for an XHR or `admin-ajax.php` endpoint that returns the data. Only then use Playwright, with one page at a time and the same delays.
- **Nutrition moves into label images** (the gallery already has "…sklad-i-wartosci-odzywcze.jpg" pack shots):\[5\] use Tesseract with the Polish language pack as a flagged fallback and record `nutrition_status="image"`.
- **You need regular refreshes:** run nightly between 01:00 and 06:00 via cron. Move storage to SQLite with a history keyed on `nutrition_sha256` and alert on changes; "Nowa Receptura" relaunches are exactly this case.
- **The catalogue grows large:** use sitemap `<lastmod>` to fetch only changed pages before adding any concurrency.
- **You get 403s or a challenge page:** stop. Don't rotate IPs or user agents; email sklep@onedaymore.pl and ask for a product feed.

## Caveats
- **Unverified:**
  - robots.txt, the sitemap count, whether the Store API is exposed, JSON-LD presence and the table's CSS classes. My tooling blocked these checks; the code detects each one at runtime.
  - The catalogue size is an estimate.
- **Data quality on the site's own labels:**
  - Per-portion columns that don't match the stated portion (Funny Honey, Marcińska, PowerUp).\[27\]\[29\]
  - Typos: "kcall", "93 cal", "266 g / 1111 kJ", "0,62 g" for manganese.\[37\]\[44\]\[46\]\[50\]
  - The same recipe listed with different values on its single-pack and multipack pages.\[42\]\[43\]
  - Treat per-100 g as authoritative and review the `warnings` field. Search-index text may lag the live pages, so the scraper's output supersedes my table.
- **Legal (factual, not legal advice):**
  - **Polish database act** (ustawa o ochronie baz danych, 2001): the database maker has an exclusive right over extraction or re-use of the whole or a substantial part (art. 6). Lawful users may take insubstantial parts (art. 7). Repeated, systematic extraction that conflicts with normal use is not allowed (art. 8(2)).\[68\]
  - **Ryanair v PR Aviation** (CJEU C-30/14, 2015): where a database is not protected by the Database Directive, its owner can restrict use through contract terms.\[69\]\[70\]
  - **This shop's terms** limit use of content to personal use and forbid disrupting the service.\[26\]
  - For anything beyond personal or research use, ask OneDayMore for permission, keep the crawl light, and don't republish their marketing text or images.
- **Third-party products:** the Melvit crispbread pages reproduce another manufacturer's label.\[55\]\[58\]\[59\]\[60\]\[61\] Check against Melvit's own data if accuracy matters.

## Sources

1. [OneDayMore PL](https://www.facebook.com/onedaymorepl/)
2. [Regulamin serwisu internetowego - OneDayMore](https://onedaymore.pl/regulamin-serwisu-internetowego/)
3. [Zdrowy pomysł na śniadanie | sklep OneDayMore](https://onedaymore.pl/sklep/)
4. [OneDayMore - Client - Agencja Interaktywna MDA.pl Poznań](https://mda.agency/client/onedaymore/)
5. [Musli Sylwetkowe Fitness | sklep OneDayMore](https://onedaymore.pl/sklep/musli-sylwetkowe/)
6. [XML Sitemap: the ultimate reference guide - Conductor](https://www.conductor.com/academy/xml-sitemap/)
7. [What Is a Sitemap? A Complete Guide to Creating and Submitting One](https://elementor.com/blog/what-is-a-sitemap-a-complete-guide/)
8. [Kontakt - OneDayMorePL](https://onedaymore.pl/kontakt/)
9. [How to Find the Sitemap of a Website](https://seocrawl.com/en/how-to-find-a-sitemap/)
10. [Free Sitemap Finder & Checker — No Signup - SEOmator](https://seomator.com/sitemap-finder)
11. [WooCommerce Store API](https://developer.woocommerce.com/docs/apis/store-api/)
12. [Store API Guiding principles](https://developer.woocommerce.com/docs/apis/store-api/guiding-principles/)
13. [Restricting per\_page for Product and ProductReview Store API Requests in WooCommerce 10.6 Developer Advisories](https://developer.woocommerce.com/2026/02/23/restricting-per_page-for-product-and-productreview-store-api-requests-in-woocommerce-10-6/)
14. [Granola Czeko-Squad](https://onedaymore.pl/sklep/granola-czeko-squad)
15. [Owsianka Karmelowa, słony karmel i biała czekolada - OneDayMore](https://onedaymore.pl/sklep/owsianka-karmelowa/?pvid=)
16. [Owsianka Bananowa Bez Cukru](https://onedaymore.pl/sklep/owsianka-bananowa-bez-cukru/)
17. [Owsianka Jabłkowa z Cynamonem Bez Cukru](https://onedaymore.pl/sklep/zestaw-2x-owsianka-jablkowa-z-cynamonem-bez-dodatku-cukru/)
18. [Owsianki w tubce - OneDayMore](https://onedaymore.pl/sklep/grupa/owsianki-tubka/)
19. [Zdrowe musli, granola i owsianka - gotowe z dobrym składem | OneDayMore](https://onedaymore.pl/sklep/granola-kawowa/)
20. [Granola Proteinowa z orzechami ziemnymi i truskawkami Nowa Receptura 400g - OneDayMore](https://onedaymore.pl/sklep/granola-proteinowa/)
21. [Musli Odpornościowe](https://onedaymore.pl/sklep/musli-odpornosciowe/)
22. [Musli Superfoods Energy bez dodatku cukru 480 g - OneDayMore](https://onedaymore.pl/sklep/musli-superfoods-energy-bez-dodatku-cukru-480g/)
23. [Granola Owocowa - OneDayMore](https://onedaymore.pl/sklep/granola-owocowa/)
24. [Granola cynamonowa z orzechami - OneDayMore](https://onedaymore.pl/sklep/granola-cynamonowa-z-orzechami/)
25. [ONE DAY MORE Musli sylwetkowe - 5902884463214](https://leclerc.com.pl/one-day-more-musli-sylwetkowe)
26. [Regulamin sklepu - OneDayMore](https://onedaymore.pl/nowy-regulamin/)
27. [Granola Marcińska 500g - OneDayMore](https://onedaymore.pl/sklep/granola-marcinska/)
28. [Granola KETO orzechowa 450 g - OneDayMore](https://onedaymore.pl/sklep/orzechowa-granola-keto-450-g/)
29. [Granola Banda Cynamonków](https://onedaymore.pl/sklep/granola-banda-cynamonkow/)
30. [Musli Ube - OneDayMore](https://onedaymore.pl/sklep/musli-ube-450g-egzotyczne-sniadanie-onedaymore/)
31. [Musli Męskie - dużo białka na śniadanie](https://onedaymore.pl/sklep/musli-meskie/?pvid=)
32. [Musli Proteinowe](https://onedaymore.pl/sklep/musli-proteinowe-nowa-receptura-2/)
33. [Musli Health Protein 200g - OneDayMore](https://onedaymore.pl/sklep/musli-health-protein/)
34. [Musli Codzienne dostarcza energii i świetnie smakuje](https://onedaymore.pl/sklep/musli-codzienne/)
35. [Musli Superfoods Active bez dodatku cukru 480 g - OneDayMore](https://onedaymore.pl/sklep/musli-superfoods-active-bez-dodatku-cukru-480g/)
36. [Muesli PowerUp - OneDayMore](https://onedaymore.pl/sklep/muesli-powerup/)
37. [Owsianka pomarańczowa 450g - OneDayMore](https://onedaymore.pl/sklep/owsianka-pomaranczowa/)
38. [Owsianka Czekoladowa](https://onedaymore.pl/sklep/owsianka-podwojnie-czekoladowa/?pvid=2534158)
39. [Owsianka Proteinowa jabłkowa z cynamonem - OneDayMore](https://onedaymore.pl/sklep/owsianka-proteinowa-jablkowa-z-cynamonem/)
40. [Owsianka Proteinowa bananowa ze słonym karmelem - OneDayMore](https://onedaymore.pl/sklep/owsianka-proteinowa-bananowa/)
41. [Zestaw 2x Owsianka z fińskimi płatkami owsianymi z truskawkami i suszonymi figami - OneDayMore](https://onedaymore.pl/sklep/zestaw-2x-owsianka-z-finskimi-platkami-owsianymi-z-truskawkami-i-suszonymi-figami/)
42. [Owsianka Proteinowa o smaku jabłkowo waniliowym 200g bez dodatku cukrów - OneDayMore](https://onedaymore.pl/sklep/vanilla-apple-protein-porridge-200g/)
43. [Owsianka Proteinowa o smaku jabłkowo waniliowym zestaw 20 x 200g - OneDayMore](https://onedaymore.pl/sklep/owsianka-proteinowa-o-smaku-jablkowo-waniliowym-zestaw-20-x-200g/)
44. [Owsianka Proteinowa o smaku truskawkowym zestaw 3 x 200g - OneDayMore](https://onedaymore.pl/sklep/strawberry-protein-porridge-zestaw-200g-x-3/)
45. [Owsianka Proteinowa o smaku truskawkowym 200g bez dodatku cukrów - OneDayMore](https://onedaymore.pl/sklep/strawberry-protein-porridge-200g-bez-dodatku-cukrow/)
46. [Krem czekoladowy o smaku orzechowym - OneDayMore](https://onedaymore.pl/sklep/krem-czekoladowy-o-smaku-orzechowym/)
47. [Krem z orzeszkami ziemnymi i orzechami laskowymi - OneDayMore](https://onedaymore.pl/sklep/krem-z-orzeszkami-ziemnymi-i-orzechami-laskowymi/)
48. [Baton orzechowy z belgijską czekoladą - ZREGENERUJ SIĘ Karma - OneDayMore](https://onedaymore.pl/sklep/baton-zregeneruj-sie-karma-migdal-orzech-wloski-belgijska-czekolada/)
49. [Baton z orzechami pekan, solą morską i syropem klonowym - NAŁADUJ SIĘ Karma - OneDayMore](https://onedaymore.pl/sklep/baton-naladuj-sie-karma-orzechy-pekan-syrop-klonowy-sol-morska/)
50. [Baton orzechowo-kokosowy z miodem - ZADBAJ O SIEBIE Karma - OneDayMore](https://onedaymore.pl/sklep/baton-zadbaj-o-siebie-karma-kokos-migdal-miod/)
51. [Mix Keto Matcha 600g - OneDayMore](https://onedaymore.pl/sklep/mix-keto-matcha-600g/)
52. [Snack Fruity 200 g - OneDayMore](https://onedaymore.pl/sklep/snack-fruity-200-g/)
53. [Liofilizowane żurawiny - OneDayMore](https://onedaymore.pl/sklep/liofilizowane-zurawiny/)
54. [Truskawki liofilizowane](https://onedaymore.pl/sklep/truskawki-liofilizowane/)
55. [Pieczywo chrupkie crispy żytni z pomidorami i bazylią 130g Melvit - OneDayMore](https://onedaymore.pl/sklep/crispy-zytni-z-pomidorami-i-bazylia-130g/)
56. [Pieczywo chrupkie proteinowe Crispy Protein 130 g - OneDayMore](https://onedaymore.pl/sklep/pieczywo-chrupkie-proteinowe-crispy-protein-130-g/)
57. [Pieczywo chrupkie Crispy żytnie z kozim serem i rozmarynem 130 g - OneDayMore](https://onedaymore.pl/sklep/pieczywo-chrupkie-crispy-zytnie-z-kozim-serem-i-rozmarynem-130-g/)
58. [Crispy orkiszowy ze słonecznikiem, sezamem i siemieniem 130g Melvit - OneDayMore](https://onedaymore.pl/sklep/crispy-orkiszowy-ze-slonecznikiem-sezamem-i-siemieniem-130g/)
59. [Fit Grain Crispy żytni z pomidorami i bazylią 150g Melvit - OneDayMore](https://onedaymore.pl/sklep/fit-grain-crispy-zytni-z-pomidorami-i-bazylia-150g/)
60. [Crispy pszenny z serem i cebulką 130g Melvit - OneDayMore](https://onedaymore.pl/sklep/crispy-pszenny-z-serem-i-cebulka-130g/)
61. [Crispy żytni vegan 130g Melvit - OneDayMore](https://onedaymore.pl/sklep/crispy-zytni-vegan-130g/)
62. [Pasta z orzeszków ziemnych Smooth - OneDayMore](https://onedaymore.pl/sklep/pasta-z-orzeszkow-ziemnych-smooth/)
63. [Krem z orzeszkami ziemnymi o smaku słonego karmelu - OneDayMore](https://onedaymore.pl/sklep/krem-z-orzeszkami-ziemnymi-o-smaku-slonego-karmelu/)
64. [Owsianka Owocowa bez dodatku cukru - pełna owoców! - OneDayMore](https://onedaymore.pl/sklep/owsianka-owocowa/)
65. [Owsianka Proteinowa czekoladowa - OneDayMore](https://onedaymore.pl/sklep/owsianka-proteinowa-czekoladowa/)
66. [Baton migdałowy z pastą z orzechów ziemnych i belgijską czekoladą - UŚMIECHNIJ SIĘ Karma - OneDayMore](https://onedaymore.pl/sklep/baton-migdalowy-z-pasta-z-orzechow-ziemnych-i-belgijska-czekolada-usmiechnij-sie-karma/)
67. [Granola Świąteczna](https://onedaymore.pl/sklep/granola-swiateczna-wyprzedaz-edycji-zimowych/)
68. [Ustawa z dnia 27 lipca 2001 r. o ochronie baz danych - Standardy Prawa](https://standardyprawa.pl/akt/143)
69. [Screen scrapers beware! T&Cs prohibiting screen scraping can be effective - Lexology](https://www.lexology.com/library/detail.aspx?g=6a55ea49-6901-4272-9755-5702315335b3)
70. [Ryanair flying high at the CJEU](https://gowlingwlg.com/en/insights-resources/articles/2015/ryanair-flying-high-at-the-cjeu)
