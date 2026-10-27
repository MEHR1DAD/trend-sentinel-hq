# 🛰️ Trend Sentinel v4 (Zero-AI Deterministic Bypass)

Trend Sentinel v4 is an autonomous, ultra-low-latency OSINT monitoring and alerting engine. It listens in real-time to VIP Telegram intelligence nodes, filters out noise, routes events to domain-specific Sentinel channels, and enforces geographic alert severity.

## 🏛️ Core Architecture (3-Step Pure-Python Engine)

1. **Step 1: Sieve / Filter (الک کردن)**
   - Normalized clause-aware n-gram matching against the 2-year curated corpus (`pattern_matrix.json`).
   - Drops generic chatter, ads, and irrelevant messages within 2ms.
2. **Step 2: Routing / Destination (مقصدیابی)**
   - Calculates domain lexical scores across War, Economy, and Protests.
   - Routes dynamic multi-topic channels (e.g., `@VahidOnline`) to the appropriate destination:
     - 🔴 `@DidebanJang` (War & Security)
     - 🟢 `@DidehbanEghtesad` (Economy & Energy)
     - 🟣 `@DidebanEterazat` (Protests & Infrastructure)
3. **Step 3: Alert Severity (تعیین سطح هشدار)**
   - **🚨 URGENT (Loud / Sound ON):** Kinetic incidents within Iran's territory/airspace or nationwide infrastructure collapses.
   - **🔕 SILENT (Silent / Sound OFF):** Regional Middle East events (e.g., Hebron, South Lebanon, Haifa), diplomatic statements, and daily financial updates.

## 📡 Active VIP Nodes (6 Channels)
- `@withyashar` (Geopolitics, War, Iran security)
- `@Ali_bk_8` (Middle East military events, English/Persian OSINT)
- `@VahidOnline` (Comprehensive coverage: War, Economy, Protests)
- `@iliaen` (Tehran field reports, explosions, local market)
- `@netblocks` (Internet shutdowns & national connectivity metrics)
- `@kianmeli1` (Breaking news, protests, military developments)

## 📁 Repository Structure
```text
trend-sentinel-hq/
├── config.py                 # Source nodes, channel IDs, settings
├── pattern_matrix.json       # Curated n-gram patterns mined from 107k messages
├── engine.py                 # 3-Step Pure-Python Deterministic Engine
├── main.py                   # Real-time Telethon listener & Bot dispatcher
├── posted_registry.json      # Deduplication history
├── session_report.md         # Uptime and heartbeat health report
├── telegram_peer_cache.json  # InputPeerChannel access cache
└── .github/workflows/
    └── watch.yml             # 24/7 autonomous runner with seamless handoff
```
