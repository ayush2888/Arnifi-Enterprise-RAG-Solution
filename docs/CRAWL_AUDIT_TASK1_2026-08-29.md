# Task 1 ΓÇö Crawl / ingest audit (2026-08-29)

## Symptom that triggered this review

Query: **Documents required ΓÇö Singapore Statutory Audit**

| Source | Content |
|--------|---------|
| Live website | Numbered list of **15** documents (Certificate of Incorporation ΓÇª management representation letter) |
| Chatbot answer | Generic FAQ line: ΓÇ£financial statements, ledgers, invoices, reconciliations, bank statementsΓÇªΓÇ¥ |

## Verdict (read this first)

**This is not primarily a crawl-depth / ΓÇ£stuck on listing gridΓÇ¥ bug for the path that feeds package bodies.**

Package **detail bodies** (Introduction, Documents Required, Process Flow, etc.) are loaded by the public site from **prodapi** (`https://prodapi.arnifi.com/api/...`). Landing HTML is a Next.js shell; HTML scrapers never see those section texts even if they ΓÇ£followΓÇ¥ the detail URL.

The Singapore Statutory Audit failure was an **extraction schema bug**:

- CMS field `documentsRequired` has **15** items (exact website list).
- `extract_micro_service_from_api` ingested intro / highlights / process / FAQs / T&Cs / `documentsFromAuthority`, but **skipped `documentsRequired`**.
- Retrieval then answered from the FAQ *ΓÇ£What documents are neededΓÇªΓÇ¥*, which is the generic text the chatbot returned.

Accounting & Bookkeeping: **28 / 29** packages have a non-empty `documentsRequired` array ΓÇö so this gap affected almost the whole service line, not one page.

---

## Q1 ΓÇö Does the scraper discover links into a queue, or only seeds?

There are **two** pipelines:

### A) `WebsiteCrawler` (`app/services/ingestion/website.py`) ΓÇö HTML

- Starts from a **fixed seed list** in `config/settings.yaml` ΓåÆ `seeds.website_pages` (homepage, country hubs, service landings, etc.).
- On each seed, parses `<a href>` once and registers child URLs that look like product/service/country pages (`extract_child_urls`).
- **Does not BFS**: discovered children are written to SQLite (`website_urls`) but **are not fetched again for further link discovery** in the same crawl pass.
- Cap: `crawl.website.max_child_pages` (default 5000).
- Optional: sitemap locs added as URLs (still no detail-body extraction from HTML shells).

### B) `ProdapiServicesIndexer` (`app/services/ingestion/prodapi_services.py`) ΓÇö CMS API (primary for package content)

- **Fixed catalog enumeration**, not HTML link following:
  1. `list_service_types()` ΓåÆ for each type, **paginated** `list_micro_services(type)` ΓåÆ every slug ΓåÆ `get_micro_service_detail`.
  2. With `--full-catalog`: every country ΓåÆ `list_licence_packages_for_country` ΓåÆ every product id ΓåÆ `get_product_page_by_id`.
- Pagination for micro-service grids is handled in `ProdapiClient` (`page` / `pageCount`), which is the API equivalent of ΓÇ£Load MoreΓÇ¥.
- Latest full run: **368 jobs, 368 processed, 0 failures, 3116 chunks**.

---

## Q2 ΓÇö Max-depth / allow-list / filters?

| Mechanism | Effect |
|-----------|--------|
| HTML denylist prefixes | Blocks some marketing/blog paths from website crawl |
| `is_product_detail_url` / `is_service_package_url` | Only those patterns registered as children from HTML |
| No depth>1 BFS on HTML | Nested ΓÇ£related packagesΓÇ¥ on detail HTML are not walked |
| Prodapi `ten_services_only` | Can exclude Funds/Other if flag set |
| Prodapi `full_catalog` | Includes all micro types + full licence grids |

Nothing in prodapi was excluding Singapore Statutory AuditΓÇÖs **URL** ΓÇö the page was ingested; the **Documents Required** field was omitted in mapping.

---

## Q3 ΓÇö Is ΓÇ£Load MoreΓÇ¥ on listing grids triggered?

| Path | Behavior |
|------|----------|
| HTML `WebsiteCrawler` | **No** Load More / infinite-scroll click. First HTML paint only. |
| Blog `ListingCrawler` | Has special-case Load More / pagination helpers (blogs only). |
| Prodapi micro-services | **Yes** ΓÇö walks all API pages until `pageCount` exhausted (e.g. Accounting 29 across 3 pages). |

So for service grids, relying on HTML Load More would under-count; prodapi already solves discovery.

---

## Q4 ΓÇö URLs visited vs URLs that exist

Rough site shape:

- ~15 country overview pages  
- ~12 micro-service types (10 ΓÇ£promptΓÇ¥ services + Funds + Other)  
- **262** micro-service detail packages (GT after full scrape)  
- **~106** country licence / setup packages (location catalog sum)  

| Pipeline | Distinct detail targets |
|----------|-------------------------|
| Prodapi `--full-catalog` (last run) | **368** micro + setup jobs (= packages, not hubs) |
| Service GT catalog | **262** micro packages |
| Location licence catalog | UAE 78 + KSA 6 + ΓÇª Γëê **106** licences |
| HTML website crawl | Seeds (~40) + children discovered from first hop / sitemap ΓÇö **does not equal** full package bodies |

**Conclusion:** Prodapi coverage of package *URLs* is in the right ballpark. The Singapore docs miss was **field coverage**, not URL coverage.

---

## Task 3 note (schemas)

| Page type | Intended schema | Status after this fix |
|-----------|-----------------|------------------------|
| Country overview | tagline, insights, selling points, compare, FAQsΓÇª | Via `prodapi_locations` / compare ingest |
| Package listing | counts + card metadata | Catalog indexes + prodapi list endpoints |
| Package detail | Intro, **Documents Required**, Highlights, Process, FAQs, T&Cs | Documents Required now mapped from `documentsRequired` |

---

## Follow-ups implemented after this audit

1. Map `documentsRequired` ΓåÆ `documents_required` section in `extract_micro_service_from_api`.
2. Include the field in `scripts/scrape_service_packages.py` GT.
3. `scripts/check_prodapi_coverage.py` ΓÇö listing count == detail fetch count; section completeness when CMS has data.
4. Re-ingest micro-services so Pinecone replaces thin older chunks for the same `source_url`.
