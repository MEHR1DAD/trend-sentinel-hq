#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
engine.py - Trend Sentinel v4 Pure-Python Deterministic Rule Engine
Implements the 3-Step Zero-AI Process:
  Step 1: Sieve / Filter (الک کردن پیام‌های فاقد نویز)
  Step 2: Destination Routing (مقصدیابی به دیده‌بان جنگ، اقتصاد، اعتراضات)
  Step 3: Alert Level (تعیین فوریت: فوری برای ایران، سایلنت برای منطقه)
"""

import os
import json
import re
from typing import Dict, List, Optional, Tuple

import config

class SentinelEngine:
    def __init__(self, pattern_matrix_path: str = config.PATTERN_MATRIX_PATH):
        self.pattern_path = pattern_matrix_path
        self.patterns = self._load_patterns()
        
        # Compile regex boundaries for precision
        self.war_patterns = self.patterns.get("war_patterns", [])
        self.economy_patterns = self.patterns.get("economy_patterns", [])
        self.protest_patterns = self.patterns.get("protest_patterns", [])
        self.geo_iran = self.patterns.get("geo_iran", [])
        self.geo_region = self.patterns.get("geo_region", [])
        self.kinetic_triggers = self.patterns.get("kinetic_triggers", [])
        self.ignored_show_titles = self.patterns.get("ignored_show_titles", ["اتاق جنگ با یاشار", "اتاق جنگ یاشار"])

    def _load_patterns(self) -> dict:
        if not os.path.exists(self.pattern_path):
            raise FileNotFoundError(f"Pattern matrix file not found at: {self.pattern_path}")
        with open(self.pattern_path, "r", encoding="utf-8") as f:
            return json.load(f)

    @staticmethod
    def normalize_text(text: str) -> str:
        """Standardizes Persian and Arabic text variants for exact matching."""
        if not text:
            return ""
        # Arabic character normalizations
        text = text.replace("ي", "ی").replace("ك", "ک").replace("ة", "ه").replace("ۀ", "ه")
        # Strip excessive invisible characters and zero-width spaces
        text = text.replace("\u200c", " ").replace("\u200f", " ").replace("\xa0", " ")
        # Lowercase Latin text
        text = text.lower()
        # Clean extra whitespace
        text = re.sub(r"\s+", " ", text).strip()
        return text

    def clean_for_matching(self, norm_text: str) -> str:
        """
        Masks show titles / branding (e.g. 'اتاق جنگ با یاشار')
        so they do not trigger false positive war or news patterns.
        """
        clean = norm_text
        for show in self.ignored_show_titles:
            norm_show = self.normalize_text(show)
            if norm_show in clean:
                clean = clean.replace(norm_show, " ")
        return re.sub(r"\s+", " ", clean).strip()

    def is_domestic_iran_protest(self, match_text: str, source_node: str = "") -> bool:
        """
        Enforces the Iran Geographic Rule for @DidebanEterazat:
        Only events taking place inside Iran (protests, labor strikes, civil actions,
        domestic Iranian courts/executions, Iranian prisons, and internet disruptions)
        are eligible for the Protest channel.
        Foreign/regional country events (e.g. Kuwait, Saudi, Iraq courts) are disqualified.
        """
        if source_node == "netblocks":
            return True

        # Internet disruptions
        if any(term in match_text for term in ["قطعی اینترنت", "اختلال اینترنت", "فیلترینگ", "blackout", "connectivity", "telecom outage", "disruption to internet"]):
            return True

        # Check if foreign/regional country is mentioned
        has_foreign_country = any(loc.lower() in match_text for loc in self.geo_region)

        domestic_iran_indicators = [
            "اوین", "قزلحصار", "قزل حصار", "رجایی شهر", "رجایی‌شهر", "دادگاه انقلاب", "دیوان عالی کشور",
            "زندانی سیاسی", "زندانیان سیاسی", "زندان تهران بزرگ", "فشافویه", "گشت ارشاد", "حجاب اجباری",
            "بازنشستگان", "هفت تپه", "هفت‌تپه", "شورای هماهنگی", "کارگران", "معلمان", "دانشجویان",
            "اعتصاب سراسری", "تجمع اعتراضی", "تجمعات اعتراضی", "اعتصاب کارگران", "اعتصاب بازاریان",
            "تعطیلی بازار", "شعار شبانه", "درگیری با معترضان", "بازداشت معترضان", "تیراندازی به معترضان",
            "قوه قضائیه", "سپاه ثارالله", "اطلاعات سپاه", "وزارت اطلاعات"
        ]
        has_domestic_indicator = any(term in match_text for term in domestic_iran_indicators)

        iran_cities = [loc.lower() for loc in self.geo_iran if loc not in ["ایران", "خلیج فارس", "تنگه هرمز"]]
        has_iran_city = any(city in match_text for city in iran_cities)

        if has_foreign_country:
            # If foreign country is present, require explicit Iranian city or domestic institution
            return has_iran_city or has_domestic_indicator

        has_iran = any(loc.lower() in match_text for loc in self.geo_iran)
        return has_iran or has_domestic_indicator

    # =========================================================================
    # STEP 1: SIEVE / FILTER (الک کردن)
    # =========================================================================
    def sieve(self, raw_text: str) -> Tuple[bool, str]:
        """
        Evaluates if the message is high-signal news or irrelevant noise/chatter.
        Returns: (is_signal, normalized_text)
        """
        if not raw_text or len(raw_text.strip()) < 8:
            return False, ""

        norm_text = self.normalize_text(raw_text)
        match_text = self.clean_for_matching(norm_text)

        # Check for matching patterns in any domain
        for p in self.war_patterns:
            if p.lower() in match_text:
                return True, norm_text
        for p in self.economy_patterns:
            if p.lower() in match_text:
                return True, norm_text
        if self.is_domestic_iran_protest(match_text):
            for p in self.protest_patterns:
                if p.lower() in match_text:
                    return True, norm_text

        # No pattern matched: Discard (Drop)
        return False, norm_text

    # =========================================================================
    # STEP 2: ROUTING / DESTINATION (مقصدیابی)
    # =========================================================================
    def route(self, norm_text: str, source_node: str = "") -> Optional[str]:
        """
        Determines the target destination channel (@DidebanJang, @DidehbanEghtesad, @DidebanEterazat).
        Handles multi-domain channels like @VahidOnline dynamically.
        Returns: Channel username string or None
        """
        # Node-specific fast routing defaults if unambiguous
        if source_node == "netblocks":
            # Netblocks is predominantly connectivity & censorship
            return config.CHANNELS["PROTEST"]

        match_text = self.clean_for_matching(norm_text)

        # Calculate lexical match scores
        war_score = 0
        economy_score = 0
        protest_score = 0

        matched_war = []
        matched_economy = []
        matched_protest = []

        for p in self.war_patterns:
            if p.lower() in match_text:
                # 2-grams and 3-grams have triple weight over single words
                weight = 3 if " " in p else 1
                war_score += weight
                matched_war.append(p)

        for p in self.economy_patterns:
            if p.lower() in match_text:
                weight = 3 if " " in p else 1
                economy_score += weight
                matched_economy.append(p)

        # Score protest patterns only if domestic Iran rule is satisfied
        if self.is_domestic_iran_protest(match_text, source_node):
            for p in self.protest_patterns:
                if p.lower() in match_text:
                    weight = 3 if " " in p else 1
                    protest_score += weight
                    matched_protest.append(p)

        # Score resolution
        scores = {
            config.CHANNELS["WAR"]: war_score,
            config.CHANNELS["ECONOMY"]: economy_score,
            config.CHANNELS["PROTEST"]: protest_score
        }

        # Select highest scoring channel
        best_channel = max(scores, key=scores.get)
        if scores[best_channel] > 0:
            return best_channel

        return None

    # =========================================================================
    # STEP 3: ALERT SEVERITY (تعیین سطح هشدار: فوری یا سایلنت)
    # =========================================================================
    def classify_severity(self, norm_text: str, target_channel: str) -> Tuple[str, bool]:
        """
        Enforces the rule:
          - Iran territory + kinetic event -> URGENT (Loud / Notification ON)
          - Regional Middle East / analytical news -> SILENT (Silent / Notification OFF)
        Returns: (severity_label, disable_notification)
        """
        match_text = self.clean_for_matching(norm_text)

        # 1. Geographic scoping
        has_iran = any(loc.lower() in match_text for loc in self.geo_iran)
        has_region = any(loc.lower() in match_text for loc in self.geo_region)

        # 2. Kinetic event detection
        is_kinetic = any(trig.lower() in match_text for trig in self.kinetic_triggers)

        # --- Decision Tree ---
        
        # Scenario A: War Channel (@DidebanJang)
        if target_channel == config.CHANNELS["WAR"]:
            # Strict Rule: Only kinetic war incidents on Iranian territorial soil/airspace are URGENT
            if has_iran and is_kinetic:
                return "URGENT", False
            else:
                # All regional conflicts (Lebanon, Gaza, Israel, Yemen, Syria), statements, threats -> SILENT
                return "SILENT", True

        # Scenario B: Protest & Infrastructure Channel (@DidebanEterazat)
        elif target_channel == config.CHANNELS["PROTEST"]:
            # Critical triggers: Nationwide internet blackout or major escalation
            if "blackout" in norm_text or "قطعی اینترنت" in norm_text or "سراسری" in norm_text:
                return "URGENT", False
            else:
                return "SILENT", True

        # Scenario C: Economy Channel (@DidehbanEghtesad)
        elif target_channel == config.CHANNELS["ECONOMY"]:
            # Extreme market shocks
            if "سقوط" in norm_text or "رکورد تازه" in norm_text or "جهش" in norm_text:
                return "URGENT", False
            else:
                # Routine daily market rates & quotes -> SILENT
                return "SILENT", True

        # Fallback default
        if has_iran and is_kinetic:
            return "URGENT", False
        return "SILENT", True

    # =========================================================================
    # UNIFIED EXECUTION PIPELINE
    # =========================================================================
    def process_message(self, raw_text: str, source_node: str = "") -> Optional[Dict]:
        """
        Full 3-step pipeline invocation for a single incoming message.
        Returns evaluation dictionary or None if dropped.
        """
        # Step 1: Sieve
        is_signal, norm_text = self.sieve(raw_text)
        if not is_signal:
            return None

        # Step 2: Route
        target_channel = self.route(norm_text, source_node)
        if not target_channel:
            return None

        # Step 3: Severity
        severity, disable_notif = self.classify_severity(norm_text, target_channel)

        return {
            "target_channel": target_channel,
            "tag": config.CHANNEL_TAGS.get(target_channel, ""),
            "severity": severity,
            "disable_notification": disable_notif,
            "source_node": source_node
        }
