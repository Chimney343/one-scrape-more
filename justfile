# One Day More (onedaymore.pl) nutrition scraper.
#
# List recipes:        just
# Install the env:     just sync
# Full fresh download: just download you@example.com
# Recommended window:  01:00-06:00 Europe/Warsaw

set shell := ["powershell.exe", "-NoProfile", "-Command"]

contact_email := "you@example.com"
max_requests := "800"
delay := "3.0"
out_dir := "out"

# List available recipes
default:
    @just --list

# Create/update the uv-managed virtualenv and uv.lock
sync:
    uv sync

# Crawl every product page, re-fetching even cached pages
download contact=contact_email max=max_requests wait=delay:
    uv run python odm_scraper.py --contact {{contact}} --refresh --max-requests {{max}} --delay {{wait}} --out {{out_dir}}

# Resume an interrupted crawl, reusing cached pages
resume contact=contact_email max=max_requests wait=delay:
    uv run python odm_scraper.py --contact {{contact}} --max-requests {{max}} --delay {{wait}} --out {{out_dir}}

# Run the test suite
test:
    uv run pytest -q

# Lint and check formatting
lint:
    uv run ruff check .
    uv run ruff format --check .

# Auto-format the source
fmt:
    uv run ruff format .

# Static type check
typecheck:
    uv run mypy odm_scraper.py

# Delete scraped output and the HTTP cache
clean:
    Remove-Item -Recurse -Force {{out_dir}}, cache -ErrorAction SilentlyContinue
