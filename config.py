#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
config.py - Trend Sentinel v4 Configuration
Central configuration module for source nodes, destination channels, credentials, and constants.
"""

import os

# --- Credentials ---
API_ID = int(os.environ.get("TELEGRAM_API_ID", "24738417"))
API_HASH = os.environ.get("TELEGRAM_API_HASH", "57b2ba5e345eb65e47bcfaa53360805a")
SESSION_STRING = os.environ.get("TELEGRAM_SESSION_GENERAL", "")
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")

# --- 6 Active VIP Source Channels ---
VIP_CHANNELS = [
    "withyashar",
    "Ali_bk_8",
    "VahidOnline",
    "iliaen",
    "netblocks",
    "kianmeli1"
]

# --- 3 Sentinel Destination Channels & Tags ---
CHANNELS = {
    "WAR": "@DidebanJang",
    "ECONOMY": "@DidehbanEghtesad",
    "PROTEST": "@DidebanEterazat"
}

CHANNEL_TAGS = {
    "@DidebanJang": "#دیده‌بان_جنگ",
    "@DidehbanEghtesad": "#دیده‌بان_اقتصاد",
    "@DidebanEterazat": "#دیده‌بان_اعتراضات"
}

# --- Operational Settings ---
MAX_RUNTIME_SEC = int(os.environ.get("MAX_RUNTIME_SEC", 26 * 60))  # 26-minute graceful handoff (avoids 30m cron collision)
POLL_INTERVAL_SEC = 2.0  # Fast polling interval

# --- File Paths ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PATTERN_MATRIX_PATH = os.path.join(BASE_DIR, "pattern_matrix.json")
PEER_CACHE_PATH = os.path.join(BASE_DIR, "telegram_peer_cache.json")
POSTED_REGISTRY_PATH = os.path.join(BASE_DIR, "posted_registry.json")
CHANNEL_STATE_PATH = os.path.join(BASE_DIR, "channel_state.json")
SESSION_REPORT_PATH = os.path.join(BASE_DIR, "session_report.md")
