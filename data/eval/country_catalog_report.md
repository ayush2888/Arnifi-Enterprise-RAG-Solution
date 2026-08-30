# Country catalog agent eval

Generated: 2026-08-29T12:02:34.704477+00:00

## Prompt review (adapted)

**Agent invocation:** `QueryEngine.ask(question, source="website")` (same stack as
`python -m app.cli query "..." --source website` and the portal on :8000).

**Ground truth:** live prodapi `GET /api/get-countries` + `/api/country-overview/:slug`
cached under `data/eval/country_ground_truth/*.json` (not HTML scrape files).
Package-detail GT is fetched live from `/micro-services/:type/:slug` or `/product-pages`.

**Prompt vs reality adjustments:**
1. **Content areas** mapped to prodapi: `marketInsights`, `keySellingPoints`,
   `overviewDetail`, `processStep`, `FAQs`, `compare` (cross + generic), `productPages`/`funds`,
   plus package detail (intro/highlights/process/FAQ/T&C/pricing).
2. **Locations** come from `prodapi-locations-ingest` (includes `compare` chunks).
3. **Package details** come from `prodapi-services-ingest` (micro-services + setup products).
4. Scoring is **phrase-coverage vs ground truth** (present / coverage / country-leak),
   not a second LLM judge ΓÇö comparable across countries, cheap to re-run.

## Summary

- Graded cases: **119** (PASS 72, PARTIAL 35, FAIL 12)
- N/A skipped: **2**
- Overall PASS rate (of graded): **61%**

### Countries with most failures

- **Hong Kong**: FAIL 1, PARTIAL 5, PASS 2
- **UAE**: FAIL 2, PARTIAL 3, PASS 3
- **British Virgin Islands**: FAIL 1, PARTIAL 3, PASS 4
- **Mauritius**: FAIL 2, PARTIAL 1, PASS 5
- **United Kingdom**: FAIL 0, PARTIAL 4, PASS 4
- **Guernsey**: FAIL 1, PARTIAL 2, PASS 5
- **Saint Vincent and the Grenadines**: FAIL 1, PARTIAL 2, PASS 5
- **Saudi Arabia**: FAIL 0, PARTIAL 4, PASS 5

### Content areas with most failures

- **package_detail**: FAIL 4, PARTIAL 9, PASS 1
- **packages**: FAIL 0, PARTIAL 13, PASS 1
- **overview**: FAIL 6, PARTIAL 1, PASS 8
- **faq**: FAIL 0, PARTIAL 8, PASS 7
- **market_insights**: FAIL 1, PARTIAL 3, PASS 11
- **process_flow**: FAIL 1, PARTIAL 1, PASS 13
- **cross_compare**: FAIL 0, PARTIAL 0, PASS 1
- **selling_points**: FAIL 0, PARTIAL 0, PASS 15
- **generic_compare**: FAIL 0, PARTIAL 0, PASS 15

### Country-mix incidents (priority)

- **Saint Vincent and the Grenadines / generic_compare**: leaked cayman ΓÇö Coverage 100% (65/65 GT phrases). COUNTRY MIX: leaked cayman
- **British Virgin Islands / generic_compare**: leaked cayman ΓÇö Coverage 83% (54/65 GT phrases). Missing: Low / varies by license; Capital Requirement Low / varies by license; 9% (above threshold); Corporate Tax Rate 9% (above threshold) COUNTRY MIX: leaked cayman
- **Cayman Island / generic_compare**: leaked british virgin ΓÇö Coverage 83% (54/65 GT phrases). Missing: Low / varies by license; Capital Requirement Low / varies by license; 9% (above threshold); Corporate Tax Rate 9% (above threshold) COUNTRY MIX: leaked british virgin
- **Hong Kong / overview**: leaked british virgin, singapore ΓÇö Coverage 0% (0/2 GT phrases). Missing: Establish your Hong Kong company with Arnifi, tap into world-class inf; springboard for global trade and mainland China expansion. Hong Kong d COUNTRY MIX: leaked british virgin, singapore
- **Hong Kong / process_flow**: leaked cayman ΓÇö Coverage 100% (4/4 GT phrases). COUNTRY MIX: leaked cayman
- **Ireland / generic_compare**: leaked british virgin ΓÇö Coverage 95% (56/59 GT phrases). Missing: Very strong (global finance hub); International Market Access Very strong (global finance hub); Annual Compliance Moderate COUNTRY MIX: leaked british virgin
- **Luxembourg / generic_compare**: leaked cayman ΓÇö Coverage 100% (65/65 GT phrases). COUNTRY MIX: leaked cayman

## Full results

| Country | Section | Status | Notes |
|---|---|---|---|
| Guernsey | market_insights | PASS | Coverage 100% (4/4 GT phrases). |
| Guernsey | selling_points | PASS | Coverage 100% (6/6 GT phrases). |
| Guernsey | overview | FAIL | Coverage 0% (0/2 GT phrases). Missing: Build your Guernsey company with Arnifi. Benefit from a globally trust; Guernsey is a leading international finance centre known for its poli |
| Guernsey | process_flow | PASS | Coverage 100% (4/4 GT phrases). |
| Guernsey | faq | PASS | Coverage 100% (5/5 GT phrases). |
| Guernsey | generic_compare | PASS | Coverage 100% (67/67 GT phrases). |
| Guernsey | packages | PARTIAL | Coverage 50% (4/8 GT phrases). Missing: 12000; 13750; 52600; 12900 |
| Guernsey | package_detail | PARTIAL | Coverage 48% (11/23 GT phrases). Missing: Guernsey is one of the world's leading jurisdictions for closed-ended ; Established international fund jurisdiction; Designed for long-ter |
| Saint Vincent and the Grenadines | market_insights | PASS | Coverage 100% (4/4 GT phrases). |
| Saint Vincent and the Grenadines | selling_points | PASS | Coverage 86% (6/7 GT phrases). Missing: Tax-Neutral Offshore Jurisdiction No corporate income tax, capital gai |
| Saint Vincent and the Grenadines | overview | FAIL | Coverage 0% (0/2 GT phrases). Missing: Launch your SVG company with Arnifi. Benefit from a flexible offshore ; is a well-established offshore jurisdiction offering a flexible, tax- |
| Saint Vincent and the Grenadines | process_flow | PASS | Coverage 100% (4/4 GT phrases). |
| Saint Vincent and the Grenadines | faq | PARTIAL | Coverage 60% (3/5 GT phrases). Missing: Can foreigners own a company in SVG?; How long does it take to incorporate a company? |
| Saint Vincent and the Grenadines | generic_compare | PASS | Coverage 100% (65/65 GT phrases). COUNTRY MIX: leaked cayman |
| Saint Vincent and the Grenadines | packages | PARTIAL | Coverage 50% (1/2 GT phrases). Missing: 38342 |
| Saint Vincent and the Grenadines | package_detail | PASS | Coverage 89% (8/9 GT phrases). Missing: 38342 |
| UAE | market_insights | FAIL | Coverage 0% (0/4 GT phrases). Missing: R&D Tax Incentive Programme; The UAE government introduced a major R&D tax credit scheme to boost; E-Invoicing Framework; The UAE issued deta |
| UAE | selling_points | PASS | Coverage 83% (5/6 GT phrases). Missing: 0% personal income tax and a competitive 9% corporate tax rate for mos |
| UAE | overview | PASS | Coverage 100% (2/2 GT phrases). |
| UAE | process_flow | FAIL | Coverage 25% (1/4 GT phrases). Missing: Define Business Setup; Set Up Operational Requirements; Maintain Ongoing Compliance |
| UAE | faq | PARTIAL | Coverage 60% (3/5 GT phrases). Missing: Is 100% foreign ownership allowed in the UAE?; Do I need a physical office to start a business? |
| UAE | generic_compare | PASS | Coverage 91% (63/69 GT phrases). Missing: Low (SGD 1 minimum); Capital Requirement Low (SGD 1 minimum); 9% (GST); Extensive schemes |
| UAE | packages | PARTIAL | Coverage 50% (2/4 GT phrases). Missing: 12900; 14010 |
| UAE | package_detail | PARTIAL | Coverage 58% (15/26 GT phrases). Missing: Establishing a corporate presence in IFZA Dubai provides international; What happens when initial or security approval gets rejected?; How |
| Saudi Arabia | market_insights | PARTIAL | Coverage 50% (2/4 GT phrases). Missing: KSA Declares 2026 as the Year of AI; The Kingdom is accelerating AI adoption, with $9.1 billion in investme |
| Saudi Arabia | selling_points | PASS | Coverage 100% (6/6 GT phrases). |
| Saudi Arabia | overview | PASS | Coverage 100% (2/2 GT phrases). |
| Saudi Arabia | process_flow | PASS | Coverage 100% (4/4 GT phrases). |
| Saudi Arabia | faq | PARTIAL | Coverage 40% (2/5 GT phrases). Missing: What is MISA, and why is it important?; Can foreigners own 100% of a business in Saudi Arabia?; How long does it take to set up a company in |
| Saudi Arabia | generic_compare | PASS | Coverage 91% (63/69 GT phrases). Missing: Low (SGD 1 minimum); Capital Requirement Low (SGD 1 minimum); 9% (GST); Extensive schemes |
| Saudi Arabia | packages | PARTIAL | Coverage 50% (2/4 GT phrases). Missing: 18000; 54000 |
| Saudi Arabia | package_detail | PARTIAL | Coverage 43% (3/7 GT phrases). Missing: Can foreigners fully own a company in KSA?; Is a physical office required in Saudi Arabia?; What authority regulates foreign investment?; 18 |
| British Virgin Islands | market_insights | PASS | Coverage 100% (4/4 GT phrases). |
| British Virgin Islands | selling_points | PASS | Coverage 100% (6/6 GT phrases). |
| British Virgin Islands | overview | FAIL | Coverage 0% (0/2 GT phrases). Missing: Establish your BVI company with Arnifi, manage global structures effic; international business The British Virgin Islands (BVI) is one of the |
| British Virgin Islands | process_flow | PASS | Coverage 100% (4/4 GT phrases). |
| British Virgin Islands | faq | PARTIAL | Coverage 40% (2/5 GT phrases). Missing: Is the BVI a tax-free jurisdiction?; Who regulates companies in the BVI?; How long does it take to set up a company? |
| British Virgin Islands | generic_compare | PASS | Coverage 83% (54/65 GT phrases). Missing: Low / varies by license; Capital Requirement Low / varies by license; 9% (above threshold); Corporate Tax Rate 9% (above threshold) COUNTR |
| British Virgin Islands | packages | PARTIAL | Coverage 50% (2/4 GT phrases). Missing: 1599; 1299 |
| British Virgin Islands | package_detail | PARTIAL | Coverage 43% (3/7 GT phrases). Missing: Do I need to visit the British Virgin Islands to register a company?; Can foreigners own a BVI company?; Is BVI company information public?; |
| Cayman Island | market_insights | PASS | Coverage 100% (4/4 GT phrases). |
| Cayman Island | selling_points | PASS | Coverage 100% (6/6 GT phrases). |
| Cayman Island | overview | PASS | Coverage 100% (2/2 GT phrases). |
| Cayman Island | process_flow | PASS | Coverage 100% (4/4 GT phrases). |
| Cayman Island | faq | PASS | Coverage 80% (4/5 GT phrases). Missing: Why is the Cayman Islands considered tax-neutral? |
| Cayman Island | generic_compare | PASS | Coverage 83% (54/65 GT phrases). Missing: Low / varies by license; Capital Requirement Low / varies by license; 9% (above threshold); Corporate Tax Rate 9% (above threshold) COUNTR |
| Cayman Island | packages | PARTIAL | Coverage 50% (2/4 GT phrases). Missing: 2799; 7400 |
| Cayman Island | package_detail | PARTIAL | Coverage 43% (3/7 GT phrases). Missing: Establishing a company in the Cayman Islands offers businesses a globa; Is physical presence required?; What type of businesses use Cayman s |
| Singapore | market_insights | PASS | Coverage 100% (4/4 GT phrases). |
| Singapore | selling_points | PASS | Coverage 100% (7/7 GT phrases). |
| Singapore | overview | PASS | Coverage 100% (2/2 GT phrases). |
| Singapore | process_flow | PASS | Coverage 100% (4/4 GT phrases). |
| Singapore | faq | PARTIAL | Coverage 40% (2/5 GT phrases). Missing: Can foreigners fully own a company in Singapore?; What is the corporate tax rate in Singapore?; How long does it take to register a company? |
| Singapore | generic_compare | PASS | Coverage 91% (63/69 GT phrases). Missing: 2ΓÇô6 weeks; 20% (foreign entities); Limited; High |
| Singapore | packages | PASS | Coverage 100% (2/2 GT phrases). |
| Singapore | package_detail | PARTIAL | Coverage 50% (1/2 GT phrases). Missing: Singapore stands out as one of the worldΓÇÖs most attractive destination |
| Mauritius | market_insights | PASS | Coverage 100% (4/4 GT phrases). |
| Mauritius | selling_points | PASS | Coverage 100% (6/6 GT phrases). |
| Mauritius | overview | FAIL | Coverage 0% (0/2 GT phrases). Missing: Start your Mauritius company with Arnifi, access African and global ma; compliance with a seamless setup experience Mauritius has positioned  |
| Mauritius | process_flow | PASS | Coverage 100% (4/4 GT phrases). |
| Mauritius | faq | PASS | Coverage 100% (4/4 GT phrases). |
| Mauritius | generic_compare | PASS | Coverage 94% (64/68 GT phrases). Missing: Not applicable; Visa Application Not applicable; Tax-neutral regime; Tax Incentives Tax-neutral regime |
| Mauritius | packages | PARTIAL | Coverage 50% (2/4 GT phrases). Missing: 5590; 11010 |
| Mauritius | package_detail | FAIL | Coverage 38% (3/8 GT phrases). Missing: A Mauritius Authorised Company is a practical structure for businesses; Who can own a Mauritius AC?; Can an AC trade inside Mauritius?; Why  |
| Cyprus | market_insights | PASS | Coverage 100% (4/4 GT phrases). |
| Cyprus | selling_points | PASS | Coverage 100% (6/6 GT phrases). |
| Cyprus | overview | PARTIAL | Coverage 50% (1/2 GT phrases). Missing: Expand through Cyprus with Arnifi, unlock EU market access, benefit fr |
| Cyprus | process_flow | PASS | Coverage 100% (5/5 GT phrases). |
| Cyprus | faq | PASS | Coverage 100% (5/5 GT phrases). |
| Cyprus | generic_compare | PASS | Coverage 100% (42/42 GT phrases). |
| Cyprus | packages | PARTIAL | Coverage 50% (1/2 GT phrases). Missing: 4345 |
| Cyprus | package_detail | FAIL | Coverage 33% (1/3 GT phrases). Missing: Cyprus functions as a critical legal and financial hub for businesses ; 4345 |
| Hong Kong | market_insights | PARTIAL | Coverage 50% (2/4 GT phrases). Missing: Aggressive Enterprise Incentives; The 2026-27 Budget slashed tax rates to 5 percent for priority industr |
| Hong Kong | selling_points | PASS | Coverage 83% (5/6 GT phrases). Missing: Tax System Built to Compete 8.25 percent on the first HKD 2 million of |
| Hong Kong | overview | FAIL | Coverage 0% (0/2 GT phrases). Missing: Establish your Hong Kong company with Arnifi, tap into world-class inf; springboard for global trade and mainland China expansion. Hong Kong  |
| Hong Kong | process_flow | PARTIAL | Coverage 100% (4/4 GT phrases). COUNTRY MIX: leaked cayman |
| Hong Kong | faq | PARTIAL | Coverage 60% (3/5 GT phrases). Missing: What's the actual tax rate?; Are annual audits mandatory? |
| Hong Kong | generic_compare | PASS | Coverage 92% (61/66 GT phrases). Missing: Not applicable; Visa Application Not applicable; Strong (global structuring); Tax-neutral regime |
| Hong Kong | packages | PARTIAL | Coverage 50% (2/4 GT phrases). Missing: 1480; 1245 |
| Hong Kong | package_detail | PARTIAL | Coverage 67% (2/3 GT phrases). Missing: 1480 |
| Ireland | market_insights | PASS | Coverage 100% (4/4 GT phrases). |
| Ireland | selling_points | PASS | Coverage 100% (6/6 GT phrases). |
| Ireland | overview | PASS | Coverage 100% (2/2 GT phrases). |
| Ireland | process_flow | PASS | Coverage 100% (5/5 GT phrases). |
| Ireland | faq | PASS | Coverage 100% (5/5 GT phrases). |
| Ireland | generic_compare | PASS | Coverage 95% (56/59 GT phrases). Missing: Very strong (global finance hub); International Market Access Very strong (global finance hub); Annual Compliance Moderate COUNTRY MIX: le |
| Ireland | packages | PARTIAL | Coverage 50% (2/4 GT phrases). Missing: 9054; 6384 |
| Ireland | package_detail | FAIL | Coverage 33% (1/3 GT phrases). Missing: An Ireland company incorporation secures a tax-efficient, English-spea; 9054 |
| Luxembourg | market_insights | PASS | Coverage 100% (4/4 GT phrases). |
| Luxembourg | selling_points | PASS | Coverage 100% (5/5 GT phrases). |
| Luxembourg | overview | PASS | Coverage 100% (2/2 GT phrases). |
| Luxembourg | process_flow | PASS | Coverage 100% (4/4 GT phrases). |
| Luxembourg | faq | PASS | Coverage 80% (4/5 GT phrases). Missing: How long does it take to set up a company? |
| Luxembourg | generic_compare | PASS | Coverage 100% (65/65 GT phrases). COUNTRY MIX: leaked cayman |
| Luxembourg | packages | PARTIAL | Coverage 50% (4/8 GT phrases). Missing: 17760; 18800; 18050; 91090 |
| Luxembourg | package_detail | PARTIAL | Coverage 46% (11/24 GT phrases). Missing: Luxembourg has long been one of Europe's preferred jurisdictions for e; Established European holding structure; Supports international inv |
| Malaysia | market_insights | PASS | Coverage 100% (4/4 GT phrases). |
| Malaysia | selling_points | PASS | Coverage 100% (6/6 GT phrases). |
| Malaysia | overview | PASS | Coverage 100% (2/2 GT phrases). |
| Malaysia | process_flow | PASS | Coverage 100% (4/4 GT phrases). |
| Malaysia | faq | PARTIAL | Coverage 60% (3/5 GT phrases). Missing: Is 100% foreign ownership allowed in Malaysia?; How long does it take to set up a company in Malaysia? |
| Malaysia | generic_compare | PASS | Coverage 94% (66/70 GT phrases). Missing: Low (SGD 1 minimum); Capital Requirement Low (SGD 1 minimum); 9% (GST); Tech, finance, trade |
| Malaysia | packages | PARTIAL | Coverage 50% (1/2 GT phrases). Missing: 4920 |
| Malaysia | package_detail | FAIL | Coverage 33% (1/3 GT phrases). Missing: Executing a Malaysia Company Incorporation provides global businesses ; 4920 |
| Puerto Rico | market_insights | PASS | Coverage 100% (4/4 GT phrases). |
| Puerto Rico | selling_points | PASS | Coverage 100% (6/6 GT phrases). |
| Puerto Rico | overview | FAIL | Coverage 0% (0/2 GT phrases). Missing: Start your Puerto Rico company with Arnifi, access US markets, benefit; a seamless setup experience Puerto Rico has emerged as a compelling d |
| Puerto Rico | process_flow | PASS | Coverage 100% (5/5 GT phrases). |
| Puerto Rico | faq | PASS | Coverage 100% (2/2 GT phrases). |
| Puerto Rico | generic_compare | PASS | Coverage 91% (64/70 GT phrases). Missing: 20% (foreign entities); Required for expats; Visa Application Required for expats; High |
| Puerto Rico | packages | N/A | N/A ΓÇö not present in source (prodapi country-overview). |
| Puerto Rico | package_detail | N/A | N/A ΓÇö no fund/package detail available from prodapi for this country. |
| United Kingdom | market_insights | PARTIAL | Coverage 50% (2/4 GT phrases). Missing: Tech & Professional FDI Leader; The UK remains Europe's top destination for financial and business ser |
| United Kingdom | selling_points | PASS | Coverage 86% (6/7 GT phrases). Missing: Flexible Capital Structure: No mandatory minimum share capital require |
| United Kingdom | overview | PASS | Coverage 100% (2/2 GT phrases). |
| United Kingdom | process_flow | PASS | Coverage 100% (5/5 GT phrases). |
| United Kingdom | faq | PARTIAL | Coverage 60% (3/5 GT phrases). Missing: What is the minimum capital required to incorporate a UK company?; Do I need to register for VAT immediately in the UK? |
| United Kingdom | generic_compare | PASS | Coverage 94% (62/66 GT phrases). Missing: Not applicable; Visa Application Not applicable; Tax-neutral regime; Tax Incentives Tax-neutral regime |
| United Kingdom | packages | PARTIAL | Coverage 50% (3/6 GT phrases). Missing: 23300; 40860; 142000 |
| United Kingdom | package_detail | PARTIAL | Coverage 50% (12/24 GT phrases). Missing: Jersey has established itself as one of the world's most respected jur; Well-established fund jurisdiction; Designed for expert investors; |
| Saudi Arabia | cross_compare | PASS | Coverage 100% (69/69 GT phrases). |

CSV: `data/eval/country_catalog_report.csv`
