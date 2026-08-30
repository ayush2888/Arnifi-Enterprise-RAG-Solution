# Website catalog ingest ΓÇö 29 August 2026

**Status:** implemented 29 August 2026 (code in repo; live Pinecone ingest still needs `.env` + `website-crawl` / `website-ingest`)  
**Goal:** scrape [arnifi.com](https://arnifi.com/) location and service pages into Pinecone so questions about the public website can be answered from RAG (`source_type=website`).

Blogs (~2,907 posts) stay on the existing blog pipeline. This work is the **public catalog**: countries, services, and package detail pages.

---

## Why this exists

A user asking ΓÇ£What is the corporate tax rate in the UAE?ΓÇ¥ or ΓÇ£What documents does a visa package need?ΓÇ¥ should retrieve **website sections**, not only blogs or Drive pricing sheets.

The homepage footer is the map:

- **15 locations** ΓÇö Guernsey, Saint Vincent and the Grenadines, UAE, Saudi Arabia, BVI, Cayman, Singapore, Mauritius, Cyprus, Hong Kong, Ireland, Luxembourg, Malaysia, Puerto Rico, United Kingdom
- **10 services** ΓÇö Post Setup, Visa, Attestation, Accounting, Legal, Product Registration, Liquidation, Banking, Will Drafting, Product Insights & Guides

Clicking a **location** (example `/ae`) shows: Explore Packages / Funds, Recent Market Insights, Key Selling Points, Country Overview, application process, FAQ.

Clicking a **service** shows packages; each **detail** page has: introduction, documents required, highlights, process flow, FAQ, terms, pricing.

---

## What we ingest vs what we skip

| Ingest | Skip (for now) |
|---|---|
| Country overview pages (all 15), section-by-section | Tools (calculator, AML, organogram, docs, assistant) |
| Service landings (overview + package names) | Careers, checkout, cart |
| Package / product-detail pages (full sections) | JS listing grids as full documents (`/product-listing`, ΓÇ£Load MoreΓÇ¥) |
| Funds authority pages already seeded | `/business-guides` until HTML has real body text |
| Contact, llms.txt, pricing master list, case studies | Re-crawling all blogs |

Listing pages are used only to **discover** ΓÇ£View DetailsΓÇ¥ URLs. The RAG unit is the **detail page**, not the first 9 cards of a 78-package grid.

---

## How a page becomes searchable

```
website-crawl  ΓåÆ  find URLs (homepage footer, seeds, sitemap, one hop)
website-ingest ΓåÆ  extract sections ΓåÆ Titan embed ΓåÆ Pinecone upsert
query          ΓåÆ  embed question ΓåÆ retrieve source_type=website (and blogs)
```

Each section becomes its own chunk where it matters (one FAQ pair = one chunk). That way ΓÇ£UAE corporate tax FAQΓÇ¥ does not get mixed with selling points.

---

## How website **changes** are handled

Pinecone does not watch the live site. Recrawl is a CLI (or a weekly scheduled task wrapping the same CLI).

1. **Unchanged page** ΓÇö SHA1 of parsed content matches SQLite ΓåÆ skip (no embed cost).
2. **Edited page** (Accounting FAQ, new selling point) ΓÇö hash differs ΓåÆ re-chunk ΓåÆ upsert (overwrites old vectors for that page).
3. **New URL** ΓÇö footer or sitemap shows it ΓåÆ ingest.
4. **Removed / 404 page** ΓÇö ingest marks it gone and deletes its vectors.

```powershell
python -m app.cli website-crawl
python -m app.cli website-ingest
```

`--force` re-embeds everything. `--page-kind country_overview` (or `service_landing`, `service_package`, `product_detail`) ingests one slice at a time.

---

## Commands (after `.env` and venv)

```powershell
cd Arnifi-Enterprise-RAG-Solution
.\venv\Scripts\Activate.ps1

python -m app.cli website-crawl
python -m app.cli website-ingest --page-kind country_overview --dry-run
python -m app.cli website-ingest --page-kind country_overview
python -m app.cli website-ingest --page-kind service_landing
python -m app.cli website-ingest --page-kind service_package
python -m app.cli website-ingest --page-kind product_detail
python -m app.cli query "What is the UAE corporate tax rate?" --source website
```

---

## Code map

| File | Role |
|---|---|
| `config/settings.yaml` | Homepage + country + service seeds |
| `app/utils/helpers.py` | URL `page_kind` (country vs service vs package) |
| `app/services/ingestion/website_sections.py` | Section extractors (FAQ pairs, documents required, ΓÇª) |
| `app/services/ingestion/website.py` | Crawl + ingest orchestration |
| `app/services/prodapi/client.py` | Public prodapi HTTP client |
| `app/services/ingestion/prodapi_locations.py` | Locations API ΓåÆ Pinecone |
| `app/cli.py` | `website-crawl` / `website-ingest` / `prodapi-locations-ingest` |

---

## Location Explore Funds (added 29 Aug 2026)

Country pages like [Guernsey `/gg`](https://arnifi.com/gg) embed **Top Funds** in the page JSON (title, slug, starting price). Those cards are now:

1. Chunked on the country page (`top_funds`) so questions like ΓÇ£Guernsey Authorised Closed-endedΓÇªΓÇ¥ and ΓÇ£all Guernsey fundsΓÇ¥ retrieve answers.
2. Turned into `/product-details/funds/{country}/{slug}/{id}` URLs during `website-crawl` for detail ingest when the CMS exposes body HTML.

**Limit:** some fund detail pages are client-rendered and `prodapi` `/product-pages/{slug}` returns 404 for Guernsey funds ΓÇö process/documents/FAQ on the **detail** URL may be empty until Arnifi exposes that API. Country-page **application process** and fund **starting prices** still ingest from `/gg`.

```powershell
python -m app.cli website-crawl
python -m app.cli website-ingest --page-kind country_overview --force
python -m app.cli website-ingest --page-kind product_detail
```

---

## Preferred: prodapi locations (added 29 Aug 2026)

Navbar locations are also available as public JSON (no auth):

- `GET https://prodapi.arnifi.com/api/get-countries`
- `GET https://prodapi.arnifi.com/api/country-overview/:slug`

**Use this for locations** instead of relying on HTML alone. Same Pinecone metadata: `source_type=website`, `page_kind=country_overview`. Canonical `source_url` is `https://arnifi.com/{shortcode}` (e.g. `/gg`).

```powershell
python -m app.cli prodapi-locations-ingest --dry-run
python -m app.cli prodapi-locations-ingest
python -m app.cli prodapi-locations-ingest --slug Guernsey --force
```

**Services** (`/micro-services/ΓÇª`) are deferred until location answers are verified. Setup products remain on `/product-pages` only; funds/service packages use micro-services (next step).

| Module | Role |
|---|---|
| `app/services/prodapi/client.py` | Public HTTP client |
| `app/services/ingestion/prodapi_locations.py` | List ΓåÆ overview ΓåÆ chunk ΓåÆ upsert |
| `extract_country_overview_from_api` in `website_sections.py` | JSON ΓåÆ Document sections |