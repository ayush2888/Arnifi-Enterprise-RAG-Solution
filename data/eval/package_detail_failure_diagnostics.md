# Package detail failure diagnostics (13 bad cases)

Generated: 2026-08-29T12:21:54.001660+00:00

## Prompt review (adapted)

Country-catalog package_detail queries ask for a multi-section package summary (intro+highlights+process+FAQs+terms), not one fixed section. expected.section reflects that. Classification still uses top-3 metadata hierarchy when present.

Read-only: no re-chunk / re-embed / index writes.

## Summary ΓÇö failure buckets

- **cross_service_bleed**: 1
- **cross_package_bleed**: 1
- **wrong_section_right_package**: 1
- **correct_retrieval_wrong_answer**: 10

## Section types appearing in top-3 of failing cases

- `pricing`: 12
- `introduction`: 11
- `faq`: 7
- `terms`: 6
- `highlights`: 3

## Expected packages appearing in >1 failing case

- None (each failing case targets a distinct package).

## Expected services appearing in >1 failing case

- Funds: 2

## Case table

| Query (short) | Expected (Service / Package / Section) | Top-1 Retrieved | Failure Bucket | Notes |
|---|---|---|---|---|
| From Arnifi's package detail page for 'Guernsey ΓÇô Authorised Closed... | Funds / Guernsey ΓÇô Authorised Closed-ended Colle / package_detail_summary(all) | Funds / Guernsey ΓÇô Authorised Closed-ended Colle / faq | `correct_retrieval_wrong_answer` | Top-3 includes correct package across multiple sections, but synthesized answer still missed GT phrases (missing sample: |
| From Arnifi's package detail page for 'IFZA License and visa quota ... | business-setup (UAE) / IFZA License and visa quota package / package_detail_summary(all) | licence / IFZA License and visa quota package / faq | `correct_retrieval_wrong_answer` | Top-3 includes correct package across multiple sections, but synthesized answer still missed GT phrases (missing sample: |
| From Arnifi's package detail page for 'KSA Company Formation', summ... | business-setup (Saudi Arabia) / KSA Company Formation / package_detail_summary(all) | licence / KSA Company Formation / pricing | `correct_retrieval_wrong_answer` | Top-3 includes correct package across multiple sections, but synthesized answer still missed GT phrases (missing sample: |
| From Arnifi's package detail page for 'British Virgin Island Compan... | business-setup (British Virgin Islands) / British Virgin Island Company Formation  / package_detail_summary(all) | licence / British Virgin Island Company Formation  / pricing | `correct_retrieval_wrong_answer` | Top-3 includes correct package across multiple sections, but synthesized answer still missed GT phrases (missing sample: |
| From Arnifi's package detail page for 'Cayman Islands Company Forma... | business-setup (Cayman Island) / Cayman Islands Company Formation / package_detail_summary(all) | Funds / Cayman Island Company Formation / Limite / terms | `wrong_section_right_package` | Right package but top-3 stuck on section='terms'; multi-section coverage missing for a package_detail summary query. |
| From Arnifi's package detail page for 'Singapore Basic Incorporatio... | business-setup (Singapore) / Singapore Basic Incorporation / package_detail_summary(all) | licence / Singapore Basic Incorporation / pricing | `correct_retrieval_wrong_answer` | Top-3 includes correct package across multiple sections, but synthesized answer still missed GT phrases (missing sample: |
| From Arnifi's package detail page for 'Mauritius Company Setup - AC... | business-setup (Mauritius) / Mauritius Company Setup - AC (Authorised / package_detail_summary(all) | licence / Mauritius Company Setup - AC (Authorised / faq | `correct_retrieval_wrong_answer` | Top-3 includes correct package across multiple sections, but synthesized answer still missed GT phrases (missing sample: |
| From Arnifi's package detail page for 'Cyprus Company Incorporation... | business-setup (Cyprus) / Cyprus Company Incorporation / Complete  / package_detail_summary(all) | licence / Cyprus Company Incorporation / Complete  / introduction | `correct_retrieval_wrong_answer` | Top-3 includes correct package across multiple sections, but synthesized answer still missed GT phrases (missing sample: |
| From Arnifi's package detail page for 'Hong Kong Company Setup', su... | business-setup (Hong Kong) / Hong Kong Company Setup / package_detail_summary(all) | licence / Hong Kong Company Setup / introduction | `correct_retrieval_wrong_answer` | Top-3 includes correct package across multiple sections, but synthesized answer still missed GT phrases (missing sample: |
| From Arnifi's package detail page for 'Ireland Company Incorporatio... | business-setup (Ireland) / Ireland Company Incorporation & Annual M / package_detail_summary(all) | licence / Ireland Company Incorporation & Annual M / introduction | `correct_retrieval_wrong_answer` | Top-3 includes correct package across multiple sections, but synthesized answer still missed GT phrases (missing sample: |
| From Arnifi's package detail page for 'Luxembourg Holding Company S... | business-setup (Luxembourg) / Luxembourg Holding Company Setup (SOPARF / package_detail_summary(all) | Funds / Luxembourg RAIF (SCSP) Fund Setup / faq | `cross_service_bleed` | Top-1 service/package unrelated to expected ('Funds' / 'Luxembourg RAIF (SCSP) Fund Setup'). |
| From Arnifi's package detail page for 'Malaysia Company Incorporati... | business-setup (Malaysia) / Malaysia Company Incorporation / package_detail_summary(all) | licence / Malaysia Company Incorporation / introduction | `correct_retrieval_wrong_answer` | Top-3 includes correct package across multiple sections, but synthesized answer still missed GT phrases (missing sample: |
| From Arnifi's package detail page for 'Jersey Expert Fund Setup', s... | Funds / Jersey Expert Fund Setup / package_detail_summary(all) | Funds / Luxembourg RAIF (SCSP) Fund Setup / faq | `cross_package_bleed` | Expected package not in top-3; top-1='Luxembourg RAIF (SCSP) Fund Setup'. |

Full JSON: `data/eval/package_detail_failure_diagnostics.json`
