# Uređena zemlja scraper

Automates [OSS Uređena zemlja](https://oss.uredjenazemlja.hr) (the Croatian public land registry / cadastre portal): you enter an address, the script finds the matching parcel on the map and downloads the official PDF extracts for it.

## What it downloads

For the parcel found at the given address:

| File prefix | Document |
|---|---|
| `PossessionSheet_*.pdf` | Possession sheet extract (*posjedovni list*) |
| `CadastralPlan_*.pdf` | Cadastral plan extract (*kopija katastarskog plana*) |
| `LandRegistry_*.pdf` | Land registry extract (*zemljišnoknjižni izvadak*) |

Files are saved to `result_pdfs/<address>/`, e.g. `result_pdfs/Ilica_55/`.

## How it works

1. Opens the portal in headless Chromium (Playwright) and types the address into the search box.
2. Picks the best suggestion: exact match first, then a fuzzy match (`difflib`), otherwise the first suggestion.
3. Clicks the map centre to select the parcel and captures the portal's own API requests (`parcel-info`, `lr/lr-unit`, `lr/lr-units`).
4. Reuses the browser's cookies with `requests` to call the public report endpoints and download the PDFs.
5. If no parcel data was captured, it clicks the map again as a fallback.

## Installation

Requires Python 3.10+.

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # macOS / Linux

pip install -r requirements.txt
playwright install chromium
```

## Usage

```bash
python playwright_visit_map_headless.py
```

You will be prompted for an address:

```
Enter the address (e.g. 'Ilica 55'): Ilica 55
```

Progress is printed to the console (`✓` success, `⚠` warning, `✗` error).

To watch the browser while it works, set `headless = False` at the bottom of `playwright_visit_map_headless.py`.

## Project structure

```
playwright_visit_map_headless.py   # the scraper
requirements.txt
playwright_cache/                  # created on first run - browser profile (git-ignored)
result_pdfs/                       # created on first run - downloaded PDFs (git-ignored)
```

## Notes

- The portal is public and needs no login.
- Downloaded extracts contain owners' personal data. `result_pdfs/` is git-ignored on purpose; don't commit or share them.
- The scraper depends on the portal's current page structure and internal API, so it may break if the site changes.
- Keep request volume reasonable and respect the portal's terms of use.
