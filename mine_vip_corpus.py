#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
mine_vip_corpus.py

Autonomous Unfiltered Corpus Miner and Frequency Analyzer for VIP News Channels.
Mines 1-month, 3-month, 6-month, 1-year, and 2-year historical posts from:
- @withyashar
- @Alibk3
- @Ali_bk_8
- @VahidOnline
- @iliaen
- @netblocks
- @kianmeli1

Performs Persian text normalization, sentence-boundary clause segmentation,
stopword stripping, and n-gram frequency extraction (1-gram, 2-gram, 3-gram).
Saves structured JSON and comprehensive Markdown reports in analytics_results/.
"""

import os
import sys
import json
import asyncio
import re
from datetime import datetime, timedelta, timezone
from collections import Counter
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import FloodWaitError, ChannelPrivateError, UsernameInvalidError

# Configuration & Secrets
API_ID = os.environ.get("TELEGRAM_API_ID")
API_HASH = os.environ.get("TELEGRAM_API_HASH")
SESSION_STRING = os.environ.get("TELEGRAM_SESSION_GENERAL")

raw_target = os.environ.get("TARGET_CHANNEL", "ALL").strip()
TARGET_CHANNEL_ARG = "ALL" if raw_target.lower() in ("true", "false", "", "none", "all") else raw_target

raw_days = os.environ.get("MAX_DAYS", "730").strip()
try:
    MAX_DAYS_ARG = int(raw_days)
except (ValueError, TypeError):
    MAX_DAYS_ARG = 730

OUTPUT_DIR = os.environ.get("OUTPUT_DIR", "analytics_results").strip() or "analytics_results"

ALL_VIP_CHANNELS = [
    "withyashar",
    "Alibk3",
    "Ali_bk_8",
    "VahidOnline",
    "iliaen",
    "netblocks",
    "kianmeli1"
]

WINDOWS = {
    "1_month": 30,
    "3_months": 90,
    "6_months": 180,
    "1_year": 365,
    "2_years": 730
}

# Rich Persian Stopwords Set (Grammatical particles, pronouns, auxiliary verbs, and neutral fillers)
PERSIAN_STOPWORDS = {
    # Conjunctions & Prepositions
    "و", "در", "به", "از", "که", "این", "را", "با", "برای", "آن", "یک", "تا", "بر", "بی",
    "نیز", "هم", "یا", "اما", "اگر", "چون", "پس", "سپس", "ولی", "بلکه", "همچنین", "همچنان",
    "گرچه", "اگرچه", "حتی", "مانند", "مثل", "همچون", "نزد", "پیش", "روی", "زیر", "بین",
    "میان", "درون", "بیرون", "پشت", "کنار", "طریق", "ازطریق", "توسط", "بابت", "درباره",
    "راجع", "پیرامون", "جهت", "بدون", "برابر", "علاوه", "ضمن", "نحوه", "طی", "مبنی",

    # Pronouns & Determiners
    "من", "تو", "او", "ما", "شما", "آنها", "ایشان", "وی", "خود", "خودش", "خودمان", "خودتان",
    "خودشان", "همین", "همان", "آنچه", "اینکه", "آنکه", "کس", "کسی", "کسانی", "هر", "همه",
    "هیچ", "دیگر", "دیگری", "چند", "چندین", "برخی", "بعضی", "کدام", "کجا", "کی", "چه",
    "چرا", "چگونه", "چطور", "آیا", "کدامیک",

    # Auxiliary & Structural Verbs
    "است", "بود", "شد", "شدن", "شده", "شدند", "می‌شود", "میشود", "می‌شد", "میشد", "خواهد",
    "خواهدشد", "باشند", "باشد", "بوده", "بودند", "بوده‌اند", "هست", "هستند", "نیست", "نیستند",
    "کرد", "کردن", "کرده", "کردند", "می‌کند", "میکند", "می‌کرد", "میکرد", "دارد", "داشت",
    "داشتن", "داشته", "دارند", "داشتند", "داد", "دادن", "داده", "دادند",
    "می‌دهد", "میدهد", "رفت", "رفتن", "رفته", "رفتند", "آمد", "آمدن", "آمده", "آمدند",
    "نمود", "نمودن", "نموده", "نمودند", "گرفت", "گرفتن", "گرفته", "گرفتند", "یافت",
    "یافتن", "یافته", "یافتند", "رسید", "رسیدن", "رسیده", "رسیدند", "می‌رسد", "میرسد",

    # Common News Structure Fillers (Speech tags, general neutral counts)
    "گفت", "گفتن", "گفته", "گفتند", "می‌گوید", "میگوید", "افزود", "اظهار", "بیان",
    "عنوان", "صورت", "مورد", "اساس", "حال", "حالت", "وجود", "قرار", "نشان",
    "طبق", "تن", "نفر", "بار", "دفعه", "مجدد", "دوباره",
    "امروز", "دیروز", "فردا", "دیشب", "امشب", "صبح", "ظهر", "عصر", "شب", "روز", "ماه", "سال",
    "ساعت", "دقیقه",

    # Tech & Telegram artifacts
    "t", "me", "https", "http", "telegram", "channel", "join", "post", "video", "photo", "link",
    "com", "org", "net", "ir"
}


def normalize_persian(text: str) -> str:
    """Normalizes Persian characters, removes URLs, handles, and special characters."""
    if not text:
        return ""
    # Remove URLs and Telegram handles
    text = re.sub(r'https?://\S+|t\.me/\S+|www\.\S+', ' ', text)
    text = re.sub(r'@\w+', ' ', text)
    
    # Arabic to Persian normalization
    text = text.replace('ي', 'ی').replace('ك', 'ک').replace('ة', 'ه').replace('ۀ', 'ه')
    
    # Remove Tatweel (Kashida)
    text = text.replace('\u0640', '')
    
    # Remove Arabic vowel marks (Harakat)
    text = re.sub(r'[\u064B-\u065F\u0670]', '', text)
    
    # Normalize directional marks
    text = re.sub(r'[\u200e\u200f\u202a-\u202e]', '', text)
    
    return text


def extract_ngrams_from_text(text: str):
    """
    Extracts sentence-aware 1-grams, 2-grams, and 3-grams.
    Clause boundaries prevent invalid cross-sentence phrases.
    """
    norm = normalize_persian(text)
    if not norm:
        return [], [], []

    # Split into clauses by punctuation / line breaks
    clauses = re.split(r'[\n\r.!?؛;،,«»\(\)\[\]{}—–…*#]+', norm)
    
    all_unigrams = []
    all_bigrams = []
    all_trigrams = []

    for clause in clauses:
        # Keep Persian/Arabic letters, half-space, English letters, and numbers
        # Clean symbols
        clean_clause = re.sub(r'[^\w\s\u200c]', ' ', clause)
        raw_tokens = clean_clause.split()
        
        # Filter tokens
        tokens = []
        for tok in raw_tokens:
            tok = tok.strip()
            # Skip single characters, pure digits, and stopwords
            if len(tok) < 2 or tok.isdigit():
                continue
            tok_lower = tok.lower()
            if tok_lower in PERSIAN_STOPWORDS:
                continue
            tokens.append(tok)

        if not tokens:
            continue

        # 1-grams
        all_unigrams.extend(tokens)

        # 2-grams
        if len(tokens) >= 2:
            for pair in zip(tokens[:-1], tokens[1:]):
                all_bigrams.append(f"{pair[0]} {pair[1]}")

        # 3-grams
        if len(tokens) >= 3:
            for trio in zip(tokens[:-2], tokens[1:-1], tokens[2:]):
                all_trigrams.append(f"{trio[0]} {trio[1]} {trio[2]}")

    return all_unigrams, all_bigrams, all_trigrams


def get_input_peer_for_channel(channel_name: str):
    """Loads exact InputPeerChannel with access_hash from peer cache to bypass username resolution issues."""
    cache_path = os.path.join(os.path.dirname(__file__), "backend", "telegram_peer_cache.json")
    if os.path.exists(cache_path):
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                cdata = json.load(f)
            node_peers = cdata.get("node_peers", {})
            clean_target = channel_name.lstrip('@').lower()
            for key, val in node_peers.items():
                if key.lstrip('@').lower() == clean_target:
                    cid = val.get("channel_id")
                    ah = val.get("access_hash")
                    if cid and ah:
                        from telethon.tl.types import InputPeerChannel
                        return InputPeerChannel(channel_id=int(cid), access_hash=int(ah))
        except Exception as e:
            print(f"⚠️ Error reading peer cache for {channel_name}: {e}")
    return None


async def mine_channel(client: TelegramClient, channel_name: str, max_days: int):
    """
    Safely iterates through channel history up to max_days,
    accumulating n-gram frequency distributions across time windows.
    """
    print(f"\n==================================================")
    print(f"🚀 Starting Corpus Mining for @{channel_name} (Max History: {max_days} days)")
    print(f"==================================================")

    now = datetime.now(timezone.utc)
    cutoff_date = now - timedelta(days=max_days)

    window_cutoffs = {
        name: now - timedelta(days=days)
        for name, days in WINDOWS.items()
    }

    # Data structures
    window_stats = {
        w: {
            "message_count": 0,
            "unigrams": Counter(),
            "bigrams": Counter(),
            "trigrams": Counter()
        }
        for w in WINDOWS.keys()
    }

    entity = None
    # 1. Try public username
    try:
        entity = await client.get_entity(channel_name)
    except Exception as e1:
        # 2. Try @username format
        try:
            entity = await client.get_entity(f"@{channel_name.lstrip('@')}")
        except Exception as e2:
            # 3. Fallback to cached InputPeerChannel
            entity = get_input_peer_for_channel(channel_name)
            if entity:
                print(f"🎯 Using cached InputPeerChannel for @{channel_name}")
            else:
                print(f"❌ Cannot resolve entity for @{channel_name} (Err: {e1})")
                return None

    count = 0
    skipped_count = 0

    try:
        async for msg in client.iter_messages(entity, offset_date=now):
            # Check cutoff
            if msg.date < cutoff_date:
                print(f"⏹️ Reached cutoff date {msg.date.strftime('%Y-%m-%d')} for @{channel_name}. Halting.")
                break

            count += 1

            if not msg.text or len(msg.text.strip()) < 5:
                skipped_count += 1
                continue

            # Anti-flood gentle throttle
            if count % 200 == 0:
                await asyncio.sleep(0.3)

            if count % 1000 == 0:
                print(f"📊 [@{channel_name}] Processed {count} messages (Date: {msg.date.strftime('%Y-%m-%d')})...")

            # Extract n-grams
            unigrams, bigrams, trigrams = extract_ngrams_from_text(msg.text)

            # Assign to applicable time windows
            for w_name, w_date in window_cutoffs.items():
                if msg.date >= w_date:
                    window_stats[w_name]["message_count"] += 1
                    window_stats[w_name]["unigrams"].update(unigrams)
                    window_stats[w_name]["bigrams"].update(bigrams)
                    window_stats[w_name]["trigrams"].update(trigrams)

    except FloodWaitError as fwe:
        print(f"⚠️ FloodWaitError encountered on @{channel_name}: Waiting {fwe.seconds}s...")
        await asyncio.sleep(fwe.seconds + 2)
    except Exception as e:
        print(f"⚠️ Exception during message iteration for @{channel_name}: {e}")

    print(f"✅ Completed @{channel_name}: Total {count} messages scanned ({skipped_count} media/empty skipped).")
    for w in WINDOWS.keys():
        print(f"   - {w}: {window_stats[w]['message_count']} messages, {len(window_stats[w]['unigrams'])} unique words.")

    return window_stats


def generate_markdown_report(all_results: dict, global_stats: dict, output_file: str):
    """
    Renders an in-depth, structured Markdown report with tables
    for executive analysis and zero-AI bypass keyword selection.
    """
    md = []
    md.append("# 📊 VIP News Corpus Frequency Analysis & Keyword Discovery Report")
    md.append(f"\n*Generated on: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}*")
    md.append("\nThis document contains unfiltered, frequency-ranked lexical intelligence mined directly from VIP sources.")
    md.append("Use this data to curate deterministic bypass patterns for **@DidebanJang**, **@DidehbanEghtesad**, and **@DidebanEterazat**.\n")

    md.append("## 1. Executive Summary & Message Volume")
    md.append("\n| Channel | 1 Month | 3 Months | 6 Months | 1 Year | 2 Years | Total Scanned |")
    md.append("| :--- | :---: | :---: | :---: | :---: | :---: | :---: |")

    total_scanned_all = 0
    for ch, data in all_results.items():
        m1 = data["1_month"]["message_count"]
        m3 = data["3_months"]["message_count"]
        m6 = data["6_months"]["message_count"]
        y1 = data["1_year"]["message_count"]
        y2 = data["2_years"]["message_count"]
        total_scanned_all += y2
        md.append(f"| `@{ch}` | {m1:,} | {m3:,} | {m6:,} | {y1:,} | {y2:,} | **{y2:,}** |")

    md.append(f"| **GLOBAL TOTAL** | **{global_stats['1_month']['message_count']:,}** | **{global_stats['3_months']['message_count']:,}** | **{global_stats['6_months']['message_count']:,}** | **{global_stats['1_year']['message_count']:,}** | **{global_stats['2_years']['message_count']:,}** | **{total_scanned_all:,}** |\n")

    md.append("---")
    md.append("## 2. Global Cross-Channel Top Phrases (All 7 VIP Channels Combined)")
    md.append("Phrases that appear with high frequency across multiple VIP nodes represent systemic, primary trends.\n")

    for w_name in ["1_month", "3_months", "6_months", "1_year", "2_years"]:
        w_title = w_name.replace("_", " ").title()
        g_data = global_stats[w_name]
        md.append(f"### 📍 Time Window: Last {w_title}")
        md.append(f"*Messages Analyzed: {g_data['message_count']:,}*\n")

        md.append("| Rank | Top 1-Grams (Keywords) | Freq | Top 2-Grams (Key Phrases) | Freq | Top 3-Grams (Triplets) | Freq |")
        md.append("| :---: | :--- | :---: | :--- | :---: | :--- | :---: |")

        top_u = g_data["unigrams"].most_common(25)
        top_b = g_data["bigrams"].most_common(25)
        top_t = g_data["trigrams"].most_common(25)

        for i in range(25):
            u_text, u_cnt = top_u[i] if i < len(top_u) else ("", "")
            b_text, b_cnt = top_b[i] if i < len(top_b) else ("", "")
            t_text, t_cnt = top_t[i] if i < len(top_t) else ("", "")
            u_fmt = f"`{u_text}`" if u_text else "-"
            b_fmt = f"**{b_text}**" if b_text else "-"
            t_fmt = f"{t_text}" if t_text else "-"
            md.append(f"| {i+1} | {u_fmt} | {u_cnt} | {b_fmt} | {b_cnt} | {t_fmt} | {t_cnt} |")
        md.append("\n")

    md.append("---")
    md.append("## 3. Node-by-Node Forensic Breakdown")
    md.append("Detailed lexical patterns per channel across critical time horizons.\n")

    for ch, data in all_results.items():
        md.append(f"### 📡 Channel: `@{ch}`\n")
        
        # We display the 2-year and 3-month horizons for each channel in the markdown report for readability
        for horizon, h_label in [("3_months", "Recent 3 Months"), ("2_years", "Cumulative 2 Years")]:
            h_data = data[horizon]
            md.append(f"#### ⏱️ @{ch} — {h_label} ({h_data['message_count']:,} msgs)")
            md.append("| Rank | Top Unigrams | Count | Top Bigrams (Action Pairs) | Count | Top Trigrams | Count |")
            md.append("| :---: | :--- | :---: | :--- | :---: | :--- | :---: |")
            
            ch_u = h_data["unigrams"].most_common(20)
            ch_b = h_data["bigrams"].most_common(20)
            ch_t = h_data["trigrams"].most_common(20)
            
            for i in range(20):
                u_text, u_cnt = ch_u[i] if i < len(ch_u) else ("", "")
                b_text, b_cnt = ch_b[i] if i < len(ch_b) else ("", "")
                t_text, t_cnt = ch_t[i] if i < len(ch_t) else ("", "")
                u_fmt = f"`{u_text}`" if u_text else "-"
                b_fmt = f"**{b_text}**" if b_text else "-"
                t_fmt = f"{t_text}" if t_text else "-"
                md.append(f"| {i+1} | {u_fmt} | {u_cnt} | {b_fmt} | {b_cnt} | {t_fmt} | {t_cnt} |")
            md.append("\n")

    with open(output_file, "w", encoding="utf-8") as f:
        f.write("\n".join(md))
    print(f"📝 Markdown report saved to: {output_file}")


async def main():
    if not API_ID or not API_HASH or not SESSION_STRING:
        print("❌ Error: Missing TELEGRAM_API_ID, TELEGRAM_API_HASH, or TELEGRAM_SESSION_GENERAL in environment!")
        sys.exit(1)

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    target_channels = ALL_VIP_CHANNELS
    if TARGET_CHANNEL_ARG != "ALL":
        target_channels = [c.strip().lstrip('@') for c in TARGET_CHANNEL_ARG.split(',')]

    print("==================================================")
    print("🛰️ SENTINEL VIP CORPUS FREQUENCY MINER (2-YEAR CORPUS)")
    print(f"Target Nodes: {target_channels}")
    print(f"Max History Depth: {MAX_DAYS_ARG} days")
    print(f"Output Directory: {OUTPUT_DIR}")
    print("==================================================")

    client = TelegramClient(StringSession(SESSION_STRING), int(API_ID), API_HASH)
    await client.connect()

    if not await client.is_user_authorized():
        print("❌ Error: Telegram session string is not authorized. Please check your credentials.")
        await client.disconnect()
        sys.exit(1)

    print("🔐 Telegram client successfully authorized.")

    try:
        print("📥 Pre-populating entity cache from account dialogs...")
        await client.get_dialogs(limit=100)
    except Exception as e:
        print(f"⚠️ Dialogs prefetch note: {e}")

    # Load existing results if available
    json_path = os.path.join(OUTPUT_DIR, "vip_frequency_analysis.json")
    existing_channels_data = {}
    if os.path.exists(json_path):
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                existing_json = json.load(f)
                existing_channels_data = existing_json.get("channels", {})
                print(f"📂 Found existing results for {list(existing_channels_data.keys())}. Preserving unless re-mined.")
        except Exception as e:
            print(f"⚠️ Error reading existing JSON: {e}")

    all_results = {}
    global_stats = {
        w: {
            "message_count": 0,
            "unigrams": Counter(),
            "bigrams": Counter(),
            "trigrams": Counter()
        }
        for w in WINDOWS.keys()
    }

    for channel in target_channels:
        res = await mine_channel(client, channel, MAX_DAYS_ARG)
        if res:
            all_results[channel] = res
            for w in WINDOWS.keys():
                global_stats[w]["message_count"] += res[w]["message_count"]
                global_stats[w]["unigrams"].update(res[w]["unigrams"])
                global_stats[w]["bigrams"].update(res[w]["bigrams"])
                global_stats[w]["trigrams"].update(res[w]["trigrams"])

    await client.disconnect()
    print("\n🔒 Telegram client disconnected cleanly.")

    # Serialize JSON
    json_export = {
        "metadata": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "target_channels": target_channels,
            "max_days": MAX_DAYS_ARG,
            "windows": WINDOWS
        },
        "global_summary": {},
        "channels": {}
    }

    for w in WINDOWS.keys():
        json_export["global_summary"][w] = {
            "message_count": global_stats[w]["message_count"],
            "top_unigrams": global_stats[w]["unigrams"].most_common(150),
            "top_bigrams": global_stats[w]["bigrams"].most_common(150),
            "top_trigrams": global_stats[w]["trigrams"].most_common(100)
        }

    final_channels = existing_channels_data.copy()
    for ch, ch_data in all_results.items():
        final_channels[ch] = {}
        for w in WINDOWS.keys():
            final_channels[ch][w] = {
                "message_count": ch_data[w]["message_count"],
                "top_unigrams": ch_data[w]["unigrams"].most_common(100),
                "top_bigrams": ch_data[w]["bigrams"].most_common(100),
                "top_trigrams": ch_data[w]["trigrams"].most_common(60)
            }
    json_export["channels"] = final_channels

    json_path = os.path.join(OUTPUT_DIR, "vip_frequency_analysis.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(json_export, f, ensure_ascii=False, indent=2)
    print(f"💾 Structured analysis JSON saved to: {json_path}")

    # Generate Markdown Report
    md_path = os.path.join(OUTPUT_DIR, "VIP_FREQUENCY_REPORT.md")
    generate_markdown_report(all_results, global_stats, md_path)

    print("\n🎉 Mining & Corpus Frequency Analysis completed successfully!")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except SystemExit as se:
        sys.exit(se.code)
    except Exception as e:
        import traceback
        print(f"\n❌ FATAL UNHANDLED EXCEPTION IN MAIN: {e}")
        traceback.print_exc()
        sys.exit(1)
