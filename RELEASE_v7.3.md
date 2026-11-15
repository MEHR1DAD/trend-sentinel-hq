# Trend Sentinel v7.3.0 Release Notes: Kinetic Action Gatekeeper & Entity Disambiguation

**Release Date:** 2026-11-15  
**Version:** `7.3.0`  
**Git Tag:** `v7.3.0`

---

## 1. Executive Summary

Version `7.3.0` implements the **Kinetic Action Gatekeeper** for `@DidebanJang` following the forensic analysis of post #1028. It guarantees that the war channel remains strictly an operational radar of physical and security incidents (strikes, launches, explosions, missiles, drones, air defense, shootings, downings, casualties, sirens, and seismic tremors). Media allegations, political commentaries, and non-kinetic weapons transit rumors without an active combat incident are now 100% filtered out.

---

## 2. Key Architecture Upgrades

### A. Strict Kinetic Action Gatekeeper (`_has_kinetic_or_operational_event`)
* Every candidate dispatch for `@DidebanJang` (outside guaranteed domestic seismic tremors) must now verify the presence of at least one physical operational incident token (`انفجار`, `شلیک`, `حمله`, `بمباران`, `اصابت`, `موشک`, `پهپاد`, `پدافند`, `ساقط شد`, `سرنگون شد`, `سقوط`, `تیراندازی`, `درگیری مسلحانه`, `ترور`, `شهادت`, `کشته شدن سرباز`, `آژیر خطر`, `آژیر قرمز`, `آماده‌باش کامل`, `توقیف`, `هدف قرار داد`, `منهدم شد`, etc.).
* Reports lacking physical/operational events are automatically zeroed out (`war_score = 0`).

### B. Purge of Pure Entity Patterns from `war_patterns`
* Removed standalone military organization titles (`سپاه پاسداران`, `نیروی هوایی`, `نیروی دریایی`, `نیروهای مسلح`, `ستاد کل نیروهای مسلح`) from `war_patterns`.
* Real military events are triggered exclusively through physical action and weapon verbs (`شلیک موشک`, `حمله پهپادی`, `توقیف شناور`, `فعالیت پدافند`), preventing mere mentions of military entities in speeches or media articles from generating war scores.

### C. Operational Launch Indicators in Proxy Bypass
* Replaced bare geographical `"از ایران"` with explicit operational launch phrases (`شلیک از ایران`, `شلیک از خاک ایران`, `پرتاب از ایران`, `پرتاب از خاک ایران`, `حمله از ایران`, `حمله از خاک ایران`, `پاسخ موشکی ایران`, `پاسخ مستقیم ایران`).
* Descriptions of transit corridors, trade routes, or geographical pipelines no longer bypass the foreign proxy veto.

### D. Refinement of Diplomatic & Commander Targets
* Replaced broad entity names (`نیروی قدس`, `سپاه قدس`) in `geo_diplomatic_and_commanders` with specific personnel and base targets (`فرماندهان نیروی قدس`, `مستشاران نیروی قدس`, `مواضع نیروی قدس در سوریه`, `پایگاه نیروی قدس`).
