# Trend Sentinel v7.2.0 Release Notes: Advanced Cross-Source Deduplication & Media Release Calming

**Release Date:** 2026-11-15  
**Version:** `7.2.0`  
**Git Tag:** `v7.2.0`

---

## 1. Executive Summary

Version `7.2.0` resolves the deduplication and alert severity issues observed in posts #1026 and #1027 in `@DidebanJang`. It introduces an advanced cross-source deduplication engine that unifies number formats (`شش` $\rightarrow$ `6`), normalizes relative nationality adjectives (`آمریکایی` $\rightarrow$ `آمریکا`), purges channel editorial commentary (e.g. `یاشار : ...`), expands regional outbound military targets in `dedup_specific_locations`, and automatically downgrades past-event media/footage releases to silent dispatches (`🔕`) while preserving loud sirens (`🚨`) for live breaking kinetic strikes and earthquakes.

---

## 2. Key Architecture Upgrades

### A. Number Word-to-Digit Fingerprint Preservation
* Previously, when Persian word numbers (e.g. `شش`, `دو`, `سه`) were converted to digits (`6`, `2`, `3`), single-digit tokens were accidentally discarded by length filters (`len <= 1`).
* Version `7.2.0` re-evaluates `is_num = tok.isdigit()` post-synonym translation, assigning high anchor weight (weight 3) to both numerical digits and converted word numbers.

### B. Nationality & Relative Adjective Stemming
* Added direct relative adjective stemming in `_stem_persian_token`:
  * `امریکایی / آمریکایی` $\rightarrow$ `امریکا`
  * `اسرائیلی` $\rightarrow$ `اسرائیل`
  * `ایرانی` $\rightarrow$ `ایران`
  * `سعودی` $\rightarrow$ `عربستان`
  * `بحرینی` $\rightarrow$ `بحرین`
  * `عراقی` $\rightarrow$ `عراق`
  * `سوری` $\rightarrow$ `سوریه`
  * `لبنانی` $\rightarrow$ `لبنان`
  * `روسی` $\rightarrow$ `روسیه`
  * `اوکراینی` $\rightarrow$ `اوکراین`
* This guarantees that phrase variations like "رادار آمریکایی" and "پدافند آمریکا" share root entity tokens.

### C. Channel Editorial Commentary Suppression
* Added regex pattern stripping in `strip_promotional_noise` for personal author chatter blocks (e.g. `یاشار : ...`), preventing subjective commentary from diluting news similarity calculations.

### D. Regional Target Locations in Deduplication Database
* Purged homonym `"پاسداران"` from `dedup_specific_locations` so IRGC mentions do not falsely bind to Pasdaran neighborhood in Tehran.
* Added official regional military targets and bases: `بحرین`, `الرفاع`, `عربستان`, `ریاض`, `عراق`, `اربیل`, `عین الاسد`, `عین‌الاسد`, `سوریه`, `دمشق`, `لبنان`, `بیروت`, `ضاحیه`.

### E. Alert Sound Calming for Media/Footage Releases
* In `_is_war_urgent`, news dispatches containing footage markers (`تصاویری از`, `تصاویر حمله`, `فیلم حمله`, `ویدیویی از`, `کلیپ از`, `تصاویر عملیات`, `انتشار تصاویر`) without live breaking markers (`هم اکنون`, `فوری`, `لحظاتی پیش`) are designated as archival media releases and routed as **SILENT (`🔕`)**, keeping loud sirens (`🚨`) reserved for live ongoing strikes, sirens, and seismic tremors.
