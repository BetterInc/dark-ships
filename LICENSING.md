# Data source licensing - READ BEFORE GOING COMMERCIAL

The risk-list layer mixes free-for-any-use government data with aggregated data
that is **free only for non-commercial use**. This file records which is which
so the commercial/non-commercial decision can be executed cleanly later.

## Current sources

| Source | Importer | Access route | License | Commercial use |
|---|---|---|---|---|
| OFAC SDN (US) | `import_ofac` | US Treasury CSV (direct) | US Government public domain | ✅ Free |
| EU Annex XLII | `import_eu` | Danish Maritime Authority CSV (direct) | Public-sector info | ✅ Free |
| UK OFSI | CSV import | UK OFSI ConList (direct) | Open Government Licence | ✅ Free |
| UANI Ghost Armada | CSV import | scraped from unitedagainstnucleariran.com | NGO campaign data | ⚠️ Check with UANI |
| **Ukraine GUR** | `import_opensanctions_vessels` | **OpenSanctions** `ua_war_sanctions` | **CC BY-NC 4.0** | ❌ **Needs license** |
| **Paris MoU** | `import_opensanctions_vessels` | **OpenSanctions** `paris_mou_banned` | **CC BY-NC 4.0** | ❌ **Needs license** |
| **Tokyo MoU** | `import_opensanctions_vessels` | **OpenSanctions** `tokyo_mou_detention` | **CC BY-NC 4.0** | ❌ **Needs license** |
| Cerulean oil slicks | `services/cerulean` | SkyTruth API | CC BY-NC (verify) | ⚠️ Check |
| Global Fishing Watch | (roadmap) | GFW API | Non-commercial only | ❌ Needs license |

The three OpenSanctions-sourced importers share one generic loader
(`import_opensanctions_vessels` in `services/risklists.py`); they are the only
hard non-commercial dependency in the risk-list layer.

## If dark-ships stays NON-COMMERCIAL (research / NGO / internal)

Simplest and most complete path: **consolidate to the OpenSanctions
`sanctions` dataset** (~1,920 deduped sanctioned vessels, 88 government
programs, built-in cross-jurisdiction entity resolution). One importer replaces
OFAC + EU + UK + GUR. Same FtM-JSON format the generic loader already parses -
add slug `sanctions` to `OS_VESSEL_DATASETS` and retire the individual ones.

## If dark-ships goes COMMERCIAL

Two options:
1. **Buy the OpenSanctions data license** (flat-rate, priced on contact) - then
   the consolidated feed above is the cleanest architecture and legal.
2. **Swap the 3 OpenSanctions importers for direct government sources**, all
   free for commercial use:
   - Canada SEMA - XML at international.gc.ca (⚠️ vessels are in free-text
     remarks, not structured fields - needs regex extraction, not a clean parse)
   - Switzerland SECO - XML at sesam.search.admin.ch
   - Australia DFAT - XLSX (needs `openpyxl`)
   - UN 1718 DPRK vessels - 59 ships, UN public
   Note (per research): because jurisdictions mirror each other's Russia/DPRK
   designations, the net-new *unique* IMOs from these is far below their raw
   counts. OFAC + EU + direct GUR scrape already covers most of the fleet.
   GUR's own site (war-sanctions.gur.gov.ua) is scrapeable directly with a
   browser user-agent if you need GUR without OpenSanctions.

## Recommendation while undecided

Keep the current mix (it works). Do **not** build the government-XML importers
yet - low net-new value and real parsing effort. Revisit this file before any
commercial launch and pick a path then.
