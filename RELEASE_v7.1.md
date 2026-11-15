# Trend Sentinel v7.1.0 Release Notes: Sovereign Territorial Hardening & Complete Tehran Matrix

**Release Date:** 2026-11-15  
**Version:** `7.1.0`  
**Git Tag:** `v7.1.0`

---

## 1. Executive Summary

Version `7.1.0` delivers a critical security and precision hardening for Trend Sentinel following the forensic analysis of post #1024 in `@DidebanJang`. It completely fortifies the Version 7 Sovereign Territorial Gatekeeper, eliminates a homonym false-positive loophole, cleans foreign proxy patterns, and integrates an exhaustive colloquial, historical, and civic geographic matrix for Tehran and Iranian territories.

---

## 2. Key Changes & Architecture Upgrades

### A. Code Fix in `engine.py` (Proxy Veto Enforcement)
* **Removed Faulty Bypass:** Eliminated `if self._contains_any(match_text, self.geo_iran_land): return False` inside `_is_foreign_proxy_conflict_without_iran`.
* **Strict Direct Iranian Attachment:** Foreign proxy conflicts (Yemen, Bab al-Mandab, Red Sea, Gaza, Lebanon, Syria/Iraq militias) are now 100% vetoed unless they feature explicit direct Iranian launches (`از خاک ایران`, `شلیک از ایران`), direct strikes on Iranian soil/waters, or strikes on official Iranian diplomatic missions and senior commanders (`سفارت ایران`, `کنسولگری ایران`, `فرماندهان ارشد سپاه`, `مستشاران نظامی ایران`).

### B. Cleaning & Sifting `war_patterns` in `pattern_matrix.json`
* **Removed 16 Non-Sovereign Foreign / Verbal Patterns:**
  * Proxy battlegrounds & militias: `دریای سرخ`, `باب المندب`, `حزب‌الله`, `حزب الله`, `نیروهای یمنی`, `انصارالله`, `حوثی‌ها`, `مقاومت اسلامی`
  * Foreign armed forces & domestic Israeli systems: `ارتش اسرائیل`, `اسرائیل حمله`, `گنبد آهنین`, `فلاخن داوود`, `پیکان`
  * Verbal / political posturing: `کاخ سفید اعلام کرد`, `پنتاگون`, `سنتکام`
* **Retained 75 Pure Kinetic Defense & Sovereign Patterns:** All physical kinetic triggers (explosions, missiles, fighter jets, drones, air defense, seismic/tremors, Iranian armed forces).

### C. Complete Eradication of 63 Bare Homonyms in `geo_iran_land`
* Replaced bare dangerous nouns (`سرباز`, `پاسداران`, `سلسله`, `مرکزی`, `توپخانه`, `فلسطین`, `مجلس`, `بازار`, `خزانه`, `سعدی`, `حافظ`, `پیروزی`, `انقلاب`, `جمهوری`, `رسالت`, `توحید`, `ظفر`, `فردوس`, `فلاح`, `خیام`, `بعثت`, `بهمن`, `ملت`, `دانشگاه`, `قدس`, `ویلا`, etc.) with unambiguous qualified terms (`شهرستان سلسله`, `استان مرکزی`, `شهرستان سرباز`, `ساختمان مجلس`, `میدان توپخانه / میدون توپخونه`, `بازار بزرگ تهران`, `محله خزانه`, `خیابان فلسطین`, etc.).

### D. Comprehensive Tehran Geographic & Colloquial Matrix (2,257 Locations)
* **Triple Expression Expansion:** Every major square, avenue, and neighborhood expanded across:
  * `میدان [نام]` and `میدون [نام]`
  * `خیابان [نام]` and `خیابون [نام]`
  * `محله [نام]`
* **Historic & Colloquial Names:** `جردن` (آفریقا / نلسون ماندلا), `محسنی` (مادر / میرداماد / داوودیه), `آریاشهر` (صادقیه), `تاج` (ستارخان), `فرح‌آباد` (پیروزی / کوکاکولا), `تخت طاووس` (مطهری), `تخت جمشید` (طالقانی), `عباس‌آباد` (بهشتی), `پهلوی` (ولیعصر), `شاهرضا` (انقلاب), `آیزنهاور` (آزادی), `کوروش کبیر` (شریعتی), `ژاله/فوزیه` (شهدا), `سپه/توپخانه/توپخونه` (امام خمینی), `باغشاه` (حر), `۲۵ شهریور` (هفت تیر), `گمرک` (رازی), `کوی نصر` (گیشا), `سلطنت‌آباد` (پاسداران), `کاخ` (فلسطین), `نادرشاه` (میرزای شیرازی), `روزولت` (مفتح), `ثریا` (سمیه), `دولت` (کلاهدوز), `فرشته` (فیاضی), `جهان کودک` (حقانی), `پارک وی`, `مخبرالدوله`, `استانبول`, `شاه عبدالعظیم` (شهرری).
* **Bazaar & South Tehran:** `خیابان/خیابون ۱۵ خرداد`, `بازار بزرگ تهران`, `سبزه میدان/میدون`, `پله نوروزخان`, `گلوبندک`, `کوچه مروی`, `کوچه برلن`, `پاچنار`, `تیمچه حاجب‌الدوله`, `سرای مشیر خلوت`, `میدان/میدون ارگ`, `خیابان/خیابون باب همایون`, `خیابان/خیابون صور اسرافیل`, `خیابان/خیابون ناصرخسرو`, `خیابان/خیابون پامنار`, `شاهپور/وحدت اسلامی`, `مولوی/سید اسماعیل`, `سیروس/سرچشمه`, `امین حضور`, `سنگلج`, `عودلاجان`, `درخونگاه`, `میدان اعدام/محمدیه`, `میدان قیام/شاه`, `دروازه غار/هرندی`, `دروازه دولاب`, `دروازه شمیران`, `خزانه بخارایی`.
* **Downtown Central Tehran:** `خیابان/خیابون ظهیرالاسلام`, `میدان/میدون بهارستان`, `ساختمان مجلس / مجلس شورای اسلامی`, `خیابان مجاهدین اسلام`, `خیابان چراغ برق / امیرکبیر`, `خیابان ملت`, `خیابان ایران / عین‌الدوله`, `خیابان فخرآباد`, `خیابان سعدی / سعدی شمالی و جنوبی`, `خیابان لاله‌زار`, `خیابان منوچهری`, `خیابان حافظ / پل حافظ`, `خیابان سی تیر / ۳۰ تیر / قوام‌السلطنه`, `خیابان فردوسی / پلاسکو`, `خیابان وصال شیرازی`, `خیابان قدس`, `خیابان فلسطین`, `خیابان دانشگاه / دانشگاه تهران`, `خیابان ویلا / نجات‌اللهی`, `خیابان ایرانشهر`, `خیابان سپهبد قرنی`, `خیابان بهار`.

---

## 3. Verification & Test Results

* **Post #1024 exact text:** Blocked (Routes: `[]`).
* **Foreign Proxy Wars (Yemen, Gaza, South Lebanon):** 100% Blocked.
* **Homonym Traps (Artillery, US Congress, Palestine, Soldiers, Central HQ):** 100% Blocked.
* **Domestic Seismic & Kinetic Strikes in Iran:** 100% Successfully routed to `@DidebanJang`.
* **Colloquial Citizen Reports in Tehran:** 100% Successfully routed to `@DidebanJang`.
