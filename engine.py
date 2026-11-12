#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
engine.py - Trend Sentinel v5 Pure-Python Deterministic Rule & Clause Engine
Implements the Zero-AI 3-Step Pipeline with:
  - Clause-level spatial binding & Persian morphological word-boundary matching
  - Bidirectional Iran strike detection (inbound/domestic soil & outbound foreign strikes)
  - Foreign vs domestic Strait of Hormuz differentiation
  - Irrealis, historical, debunk, and ordinary-accident blockers
  - @Ali_bk_8 Persian-only matching & output extraction
  - @netblocks & Persian Iran-only internet disruption cross-posting (@DidebanEterazat + @DidebanJang)
"""

import os
import json
import re
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import config

_ARABIC_SCRIPT_RE = re.compile(r"[\u0600-\u06FF]")
_PERSIAN_UNIQUE_CHARS_RE = re.compile(r"[پچژگ\u06A9\u06CC]")
_PERSIAN_FUNCTION_WORDS_RE = re.compile(
    r"(?<![\u0600-\u06FF])(?:است|شد|شده|شود|شدند|می|نیز|های|در|به|از|که|این|آن|را|با|برای|هم|بر|تا|گفت|کرد|دارد|هستند|بود|می‌شود|می‌کند)(?![\u0600-\u06FF])"
)
_DIGIT_TRANS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")


class SentinelEngine:
    def __init__(self, pattern_matrix_path: str = config.PATTERN_MATRIX_PATH):
        self.pattern_path = pattern_matrix_path
        self.patterns = self._load_patterns()
        self._regex_cache: Dict[str, re.Pattern] = {}

        # Core routing lexicons
        self.war_patterns = self.patterns.get("war_patterns", [])
        self.economy_patterns = self.patterns.get("economy_patterns", [])
        self.protest_patterns = self.patterns.get("protest_patterns", [])

        # Urgency & internet lexicons
        self.economy_urgent_triggers = self.patterns.get("economy_urgent_triggers_fa", ["سقوط", "رکورد تازه", "جهش"])
        self.protest_urgent_triggers = self.patterns.get(
            "protest_urgent_triggers_fa",
            ["قطعی اینترنت", "قطع اینترنت", "قطع سراسری اینترنت", "اختلال اینترنت", "اعتصاب سراسری"],
        )
        self.internet_iran_patterns_fa = self.patterns.get("internet_iran_patterns_fa", [])
        self.internet_iran_patterns_en = self.patterns.get("internet_iran_patterns_en", [])

        # Geographic tiers
        self.geo_iran_land = self.patterns.get("geo_iran_land", [])
        self.geo_iran_country_contexts = self.patterns.get("geo_iran_country_contexts", [])
        self.geo_maritime = self.patterns.get("geo_maritime", ["تنگه هرمز", "خلیج فارس", "دریای عمان"])
        self.geo_iran = self.patterns.get("geo_iran", [])
        self.geo_iran_en = self.patterns.get("geo_iran_en", ["iran", "tehran"])
        self.geo_foreign_en = self.patterns.get("geo_foreign_en", [])
        self.geo_region = self.patterns.get("geo_region", [])

        # Kinetic & directional lexicons
        self.kinetic_strong_triggers = self.patterns.get("kinetic_strong_triggers", self.patterns.get("kinetic_triggers", []))
        self.kinetic_casualty_companions = self.patterns.get("kinetic_casualty_companions", ["کشته", "مجروح", "زخمی"])
        self.outbound_attack_verbs = self.patterns.get("outbound_attack_verbs", ["شلیک", "پرتاب", "حمله", "هدف قرار داد"])
        self.outbound_foreign_targets = self.patterns.get("outbound_foreign_targets", [])
        self.foreign_attackers = self.patterns.get("foreign_attackers", [])
        self.iran_actors = self.patterns.get("iran_actors", ["ایران", "سپاه"])

        # Negative / Blocker lexicons
        self.non_combat_debunks = self.patterns.get("non_combat_debunks", [])
        self.ordinary_accidents = self.patterns.get("ordinary_accidents", [])
        self.irrealis_threat_blockers = self.patterns.get("irrealis_threat_blockers", [])
        self.historical_markers = self.patterns.get("historical_markers", [])
        self.ignored_show_titles = self.patterns.get(
            "ignored_show_titles", ["اتاق جنگ با یاشار", "اتاق جنگ یاشار", "دیدبان اتاق جنگ"]
        )

        # Cross-source event deduplication lexicons (v6)
        self.dedup_phrase_replacements = sorted(
            [
                (self._dedup_norm(k), self._dedup_norm(v))
                for k, v in self.patterns.get("dedup_phrase_replacements", {}).items()
                if k and v
            ],
            key=lambda kv: len(kv[0]),
            reverse=True,
        )
        self.dedup_synonyms = {
            self._dedup_norm(k): self._dedup_norm(v)
            for k, v in self.patterns.get("dedup_synonyms", {}).items()
            if k and v
        }
        self.dedup_stopwords = {
            self._dedup_norm(w) for w in self.patterns.get("dedup_stopwords", []) if w
        }
        self.dedup_low_weight_terms = {
            self._stem_persian_token(self._dedup_norm(w))
            for w in self.patterns.get("dedup_low_weight_terms", [])
            if w
        }
        self.dedup_specific_locations = [
            self._dedup_norm(loc) for loc in self.patterns.get("dedup_specific_locations", []) if loc
        ]
        self._dedup_location_patterns = [
            (loc, re.compile(rf"(?<![\u0600-\u06FFa-z0-9_]){re.escape(loc)}(?![\u0600-\u06FFa-z0-9_])"))
            for loc in self.dedup_specific_locations
        ]

    def _load_patterns(self) -> dict:
        if not os.path.exists(self.pattern_path):
            raise FileNotFoundError(f"Pattern matrix file not found at: {self.pattern_path}")
        with open(self.pattern_path, "r", encoding="utf-8") as f:
            return json.load(f)

    # =========================================================================
    # TEXT NORMALIZATION & LANGUAGE / SCRIPT SEPARATION
    # =========================================================================
    @staticmethod
    def normalize_text(text: str) -> str:
        """Standardizes Persian/Arabic script variants, strips diacritics, and normalizes whitespace."""
        if not text:
            return ""
        # Strip Arabic diacritics
        text = re.sub(r"[\u064B-\u065F\u0670]", "", text)
        # Arabic to Persian letter normalization
        text = text.replace("ي", "ی").replace("ك", "ک").replace("ة", "ه").replace("ۀ", "ه")
        # Strip invisible directional/zero-width chars
        text = text.replace("\u200c", " ").replace("\u200f", " ").replace("\u200e", " ").replace("\xa0", " ")
        # Normalize Persian/Arabic digits to ASCII for uniform year/numeric checks
        text = text.translate(_DIGIT_TRANS)
        text = text.lower()
        text = re.sub(r"\s+", " ", text).strip()
        return text

    @classmethod
    def _is_persian_line(cls, raw_line: str) -> bool:
        """Detects whether a raw line actually contains Persian text (vs pure English or pure Arabic)."""
        stripped = (raw_line or "").strip()
        if not stripped or not _ARABIC_SCRIPT_RE.search(stripped):
            return False
        if _PERSIAN_UNIQUE_CHARS_RE.search(stripped):
            return True
        norm_line = cls.normalize_text(stripped)
        return bool(_PERSIAN_FUNCTION_WORDS_RE.search(norm_line))

    @classmethod
    def has_persian_text(cls, raw_text: str) -> bool:
        """Returns True if the post contains meaningful Persian text."""
        if not raw_text:
            return False
        for line in raw_text.splitlines():
            if cls._is_persian_line(line):
                return True
        return False

    @classmethod
    def extract_persian_text(cls, raw_text: str) -> str:
        """
        Extracts only the Persian paragraphs/lines from a bilingual/multilingual post
        (while keeping Latin terms like 'F-35' embedded inside Persian lines).
        """
        if not raw_text:
            return ""
        kept_lines: List[str] = []
        for line in raw_text.splitlines():
            s = line.strip()
            if not s:
                if kept_lines and kept_lines[-1] != "":
                    kept_lines.append("")
                continue
            if cls._is_persian_line(s):
                kept_lines.append(s)
        while kept_lines and kept_lines[-1] == "":
            kept_lines.pop()
        return "\n".join(kept_lines)

    def clean_for_matching(self, norm_text: str) -> str:
        """Masks channel/show titles so branding never causes false war triggers."""
        clean = norm_text
        for show in self.ignored_show_titles:
            norm_show = self.normalize_text(show)
            if norm_show and norm_show in clean:
                clean = clean.replace(norm_show, " ")
        return re.sub(r"\s+", " ", clean).strip()

    # =========================================================================
    # MORPHOLOGICAL WORD-BOUNDARY MATCHING & CLAUSE SPLITTING
    # =========================================================================
    def _get_boundary_regex(self, term: str) -> Optional[re.Pattern]:
        norm_term = self.normalize_text(term)
        if not norm_term:
            return None
        cached = self._regex_cache.get(norm_term)
        if cached is not None:
            return cached

        # Allow optional Persian plural suffixes ('ها', 'های', 'هایی') on the trailing token
        # while strictly blocking derivational adjectives like '-ی' (ایرانی، موشکی، تروریستی).
        escaped = re.escape(norm_term)
        has_persian = bool(_ARABIC_SCRIPT_RE.search(norm_term))
        suffix = r"(?:ها(?:ی|یی)?)?" if has_persian else ""
        pattern = re.compile(rf"(?<![\u0600-\u06FFa-z0-9_]){escaped}{suffix}(?![\u0600-\u06FFa-z0-9_])")
        self._regex_cache[norm_term] = pattern
        return pattern

    def _contains_term(self, norm_text: str, term: str) -> bool:
        rx = self._get_boundary_regex(term)
        return bool(rx and rx.search(norm_text))

    def _contains_any(self, norm_text: str, terms: List[str]) -> bool:
        return any(self._contains_term(norm_text, t) for t in terms)

    def _matched_terms(self, norm_text: str, terms: List[str]) -> List[str]:
        return [t for t in terms if self._contains_term(norm_text, t)]

    def _split_sentences_and_clauses(self, raw_text: str) -> List[Tuple[str, List[str]]]:
        """
        Breaks raw text into normalized sentences and comma/colon sub-clauses,
        propagating short city headers (e.g. 'تهران:') into the immediately following clause.
        Returns list of (norm_sentence, [norm_clauses...]).
        """
        if not raw_text:
            return []
        # Split on line breaks or sentence punctuation (preserving decimals like 6.4 or ۶.۴)
        raw_sentences = [
            s.strip()
            for s in re.split(r"(?:\n|(?<![\d۰-۹٠-٩])\.(?![\d۰-۹٠-٩])|[!؟؛])+", raw_text)
            if s.strip()
        ]
        results: List[Tuple[str, List[str]]] = []
        pending_header_city = ""

        for raw_sent in raw_sentences:
            norm_sent = self.clean_for_matching(self.normalize_text(raw_sent))
            if not norm_sent:
                continue

            sub_parts = [p.strip() for p in re.split(r"[،:]+", norm_sent) if p.strip()]
            # If the whole short line was a pure city header (e.g. "تهران:" on its own line)
            if len(sub_parts) == 1 and len(sub_parts[0].split()) <= 3 and self._contains_any(sub_parts[0], self.geo_iran_land):
                pending_header_city = sub_parts[0]
                continue

            if pending_header_city:
                norm_sent = f"{pending_header_city} {norm_sent}"
                if sub_parts:
                    sub_parts[0] = f"{pending_header_city} {sub_parts[0]}"
                pending_header_city = ""

            # Also propagate inline "City: ..." prefix within sub_parts of the same sentence
            if len(sub_parts) >= 2 and len(sub_parts[0].split()) <= 3 and self._contains_any(sub_parts[0], self.geo_iran_land):
                sub_parts[1] = f"{sub_parts[0]} {sub_parts[1]}"

            results.append((norm_sent, sub_parts or [norm_sent]))

        return results

    def _has_historical_year(self, norm_text: str) -> bool:
        """Detects references to past years (Gregorian < current year, Jalali < current Jalali year)."""
        now_greg = datetime.now().year
        now_jalali = now_greg - 621

        # Explicit "سال YYYY"
        for m in re.finditer(r"(?<![\u0600-\u06FF])سال\s+(\d{4})(?!\d)", norm_text):
            yr = int(m.group(1))
            if (1900 <= yr < now_greg) or (1300 <= yr < now_jalali):
                return True

        # Standalone recognizable past century years like 1983, 1388
        for m in re.finditer(r"(?<!\d)(19[4-9]\d|13[4-9]\d)(?!\d)", norm_text):
            yr = int(m.group(1))
            if yr < now_jalali or (1940 <= yr <= 1999):
                return True

        return False

    # =========================================================================
    # IRAN GEOGRAPHY & INTERNET DISRUPTION HELPERS
    # =========================================================================
    def _has_iran_internet_en(self, norm_text: str) -> bool:
        """Strict check for @netblocks English posts: must concern Iran's internet, not foreign countries."""
        if not self._contains_any(norm_text, self.internet_iran_patterns_en + self.internet_iran_patterns_fa):
            return False

        has_explicit_iran_binding = bool(
            re.search(
                r"(?:(?:in|across|of|for|over|within)\s+#?iran\b|#?iran(?:'s|\s+(?:internet|connectivity|network|telecom|blackout|disruption|shutdown|cities))|\btehran\b|\bisfahan\b|\bshiraz\b|\bmashhad\b|\btabriz\b)",
                norm_text,
            )
        )
        if has_explicit_iran_binding:
            return True

        has_iran_token = self._contains_any(norm_text, self.geo_iran_en + ["ایران", "تهران"])
        if not has_iran_token:
            return False

        # Reject if outage is explicitly located in a foreign country without explicit Iran binding
        foreign_subject = False
        for fc in self.geo_foreign_en:
            if re.search(rf"(?:in|across|of|for)\s+#?{re.escape(fc)}\b|#?{re.escape(fc)}(?:'s|\s+(?:internet|connectivity|network|telecom|blackout|disruption|shutdown))", norm_text):
                foreign_subject = True
                break
        return not foreign_subject

    def is_iran_internet_disruption(self, match_text: str, source_node: str = "") -> bool:
        """
        Determines whether a post reports a genuine disruption/outage/drop of Iran's internet.
        These events are routed simultaneously to @DidebanEterazat and @DidebanJang as URGENT.
        """
        if source_node == "netblocks":
            return self._has_iran_internet_en(match_text)

        if not self._contains_any(match_text, self.internet_iran_patterns_fa):
            return False

        # Must belong to Iran (reject reports about foreign internet outages)
        has_iran_ref = (
            self._contains_any(match_text, self.geo_iran_land)
            or self._contains_any(match_text, self.geo_iran_country_contexts)
            or self._contains_term(match_text, "ایران")
            or self._contains_term(match_text, "کشور")
        )
        has_foreign = self._contains_any(match_text, self.geo_region)
        if has_foreign and not (self._contains_any(match_text, self.geo_iran_land) or self._contains_any(match_text, self.geo_iran_country_contexts)):
            return False
        if has_foreign and self._contains_term(match_text, "ایران") and not self._contains_any(match_text, self.geo_iran_land):
            # Guard against "ایران درباره قطعی اینترنت در عراق گزارش داد" unless Iranian internet is explicitly named
            return bool(re.search(r"اینترنت\s+(?:در\s+)?ایران|اینترنت\s+کشور|در\s+ایران", match_text))
        return has_iran_ref or not has_foreign

    def is_domestic_iran_protest(self, match_text: str, source_node: str = "") -> bool:
        """
        Enforces the Iran Geographic Rule for @DidebanEterazat:
        Only events inside Iran (protests, strikes, domestic prisons/courts, Iran internet disruptions)
        are eligible. Foreign country events (including foreign @netblocks outages) are disqualified.
        """
        if source_node == "netblocks":
            return self._has_iran_internet_en(match_text)

        if self._contains_any(match_text, self.internet_iran_patterns_fa):
            return self.is_iran_internet_disruption(match_text, source_node)

        has_foreign_country = self._contains_any(match_text, self.geo_region)

        domestic_iran_indicators = [
            "اوین", "قزلحصار", "قزل حصار", "رجایی شهر", "زندان لاکان", "دادگاه انقلاب", "دیوان عالی کشور",
            "زندانی سیاسی", "زندانیان سیاسی", "زندان تهران بزرگ", "فشافویه", "گشت ارشاد", "حجاب اجباری",
            "بازنشستگان", "هفت تپه", "شورای هماهنگی", "کارگران", "معلمان", "دانشجویان",
            "اعتصاب سراسری", "تجمع اعتراضی", "تجمعات اعتراضی", "اعتصاب کارگران", "اعتصاب بازاریان",
            "تعطیلی بازار", "شعار شبانه", "درگیری با معترضان", "بازداشت معترضان", "تیراندازی به معترضان",
            "قوه قضائیه", "سپاه ثارالله", "اطلاعات سپاه", "وزارت اطلاعات"
        ]
        has_domestic_indicator = self._contains_any(match_text, domestic_iran_indicators)
        has_iran_land = self._contains_any(match_text, self.geo_iran_land)

        if has_foreign_country:
            return has_iran_land or has_domestic_indicator

        has_iran = has_iran_land or self._contains_term(match_text, "ایران")
        return has_iran or has_domestic_indicator

    # =========================================================================
    # CHANNEL TEXT PREPROCESSING (@Ali_bk_8 vs @netblocks vs Persian nodes)
    # =========================================================================
    def _prepare_source_text(self, raw_text: str, source_node: str) -> Tuple[Optional[str], Optional[str]]:
        """
        Prepares (eval_raw_text, output_raw_text) for the given source channel.
        Returns (None, "PENDING_TRANSLATION") when @Ali_bk_8 has no Persian translation yet.
        """
        if source_node == "netblocks":
            return raw_text, raw_text

        if source_node == "Ali_bk_8":
            if not self.has_persian_text(raw_text):
                return None, "PENDING_TRANSLATION"
            persian_only = self.extract_persian_text(raw_text)
            if not persian_only:
                return None, "PENDING_TRANSLATION"
            return persian_only, persian_only

        # All other channels are evaluated as Persian sources; if multilingual, keep evaluation Persian-only
        if self.has_persian_text(raw_text):
            return self.extract_persian_text(raw_text), raw_text
        return None, None

    # =========================================================================
    # STEP 1 & 2: SIEVE + ROUTING (single or multi-destination)
    # =========================================================================
    def _evaluate_routes(self, eval_raw_text: str, source_node: str) -> Tuple[List[str], bool]:
        """
        Evaluates matching channels for prepared text.
        Returns (target_channels, is_iran_internet_crosspost).
        """
        norm_text = self.normalize_text(eval_raw_text)
        match_text = self.clean_for_matching(norm_text)

        if not norm_text or len(eval_raw_text.strip()) < 8:
            return [], False

        # Dedicated path for @netblocks: ONLY Iran internet disruptions, cross-posted to PROTEST + WAR
        if source_node == "netblocks":
            if self.is_iran_internet_disruption(match_text, source_node):
                return [config.CHANNELS["PROTEST"], config.CHANNELS["WAR"]], True
            return [], False

        # Persian Iran internet disruption -> cross-post to BOTH @DidebanEterazat and @DidebanJang
        if self.is_iran_internet_disruption(match_text, source_node):
            return [config.CHANNELS["PROTEST"], config.CHANNELS["WAR"]], True

        # Standard single-channel weighted scoring
        war_score = 0
        economy_score = 0
        protest_score = 0

        for p in self.war_patterns:
            if self._contains_term(match_text, p):
                war_score += 3 if " " in p else 1

        for p in self.economy_patterns:
            if self._contains_term(match_text, p):
                economy_score += 3 if " " in p else 1

        if self.is_domestic_iran_protest(match_text, source_node):
            for p in self.protest_patterns:
                if self._contains_term(match_text, p):
                    protest_score += 3 if " " in p else 1

        scores = {
            config.CHANNELS["WAR"]: war_score,
            config.CHANNELS["ECONOMY"]: economy_score,
            config.CHANNELS["PROTEST"]: protest_score,
        }
        best_channel = max(scores, key=scores.get)
        if scores[best_channel] > 0:
            return [best_channel], False
        return [], False

    def sieve(self, raw_text: str, source_node: str = "") -> Tuple[bool, str]:
        """Compatibility sieve method using v5 source-aware rules."""
        eval_text, status = self._prepare_source_text(raw_text, source_node)
        if not eval_text:
            return False, ""
        routes, _ = self._evaluate_routes(eval_text, source_node)
        return bool(routes), self.normalize_text(eval_text)

    def route(self, norm_text: str, source_node: str = "") -> Optional[str]:
        """Compatibility single-channel route method."""
        routes, _ = self._evaluate_routes(norm_text, source_node)
        return routes[0] if routes else None

    # =========================================================================
    # STEP 3: STRICT CLAUSE-LEVEL ALERT SEVERITY
    # =========================================================================
    def _is_blocked_sentence(self, norm_sent: str) -> bool:
        """Checks whether a sentence is a debunk, routine accident, threat/conditional, or historical memorial."""
        if self._contains_any(norm_sent, self.non_combat_debunks):
            return True
        if self._contains_any(norm_sent, self.ordinary_accidents):
            return True
        if self._contains_any(norm_sent, self.irrealis_threat_blockers):
            return True
        if self._contains_any(norm_sent, self.historical_markers) or self._has_historical_year(norm_sent):
            return True
        return False

    def _clause_has_iran_land_binding(self, norm_clause: str) -> bool:
        """Checks whether a clause genuinely refers to Iranian mainland/islands/airspace (not nationality 'ایرانی')."""
        if self._contains_any(norm_clause, self.geo_iran_land):
            return True
        if self._contains_any(norm_clause, self.geo_iran_country_contexts):
            return True
        return False

    def _clause_is_inbound_or_domestic_iran_event(self, norm_clause: str, norm_sent: str) -> bool:
        """
        True when a genuine kinetic/security/disaster event occurs on Iranian mainland/airspace.
        Guards 'کشته'/'مجروح' so they NEVER trigger URGENT without a real incident in the same sentence.
        """
        has_strong_kinetic = self._contains_any(norm_clause, self.kinetic_strong_triggers)
        has_casualty_only = (not has_strong_kinetic) and self._contains_any(norm_clause, self.kinetic_casualty_companions)

        if has_casualty_only:
            # Require a real kinetic/disaster companion anywhere in the same sentence (not a foreign country only)
            if not self._contains_any(norm_sent, self.kinetic_strong_triggers):
                return False

        if not (has_strong_kinetic or has_casualty_only):
            return False

        # Case 1: Explicit Iranian city/province/island/facility/airspace in the same clause
        if self._clause_has_iran_land_binding(norm_clause):
            # Guard against outbound drill/fire INTO Strait of Hormuz unless a real land place was attacked
            if self._contains_any(norm_clause, self.geo_maritime) and not self._contains_any(norm_clause, self.geo_iran_land):
                return False
            # Guard against "ایران به سمت اسرائیل موشک شلیک کرد" being misclassified as inbound if it matched 'از ایران'
            if (
                not self._contains_any(norm_clause, self.geo_iran_land)
                and self._contains_term(norm_clause, "از ایران")
                and self._contains_any(norm_clause, self.outbound_foreign_targets)
            ):
                # Still URGENT via outbound path, handled explicitly below
                return True
            return True

        # Case 2: Sentence header or adjacent clause has Iran land location and this clause has strong kinetic without foreign location
        if not self._contains_any(norm_clause, self.geo_region) and not self._contains_any(norm_clause, self.geo_maritime):
            if self._contains_any(norm_sent, self.geo_iran_land):
                return True

        return False

    def _clause_is_outbound_iran_strike(self, norm_sent: str) -> bool:
        """
        True when Iran/IRGC launches a real military strike FROM Iran TOWARD an external foreign target
        (excluding Strait of Hormuz / Persian Gulf maritime launches).
        """
        has_iran_actor = self._contains_any(norm_sent, self.iran_actors)
        has_outbound_verb = self._contains_any(norm_sent, self.outbound_attack_verbs)
        has_foreign_target = self._contains_any(norm_sent, self.outbound_foreign_targets) or self._contains_any(norm_sent, self.geo_region)

        if not (has_iran_actor and has_outbound_verb and has_foreign_target):
            return False

        # Disqualify launches specifically toward Strait of Hormuz / Persian Gulf by Iran
        if self._contains_any(norm_sent, self.geo_maritime) and not self._contains_any(norm_sent, self.outbound_foreign_targets):
            return False

        # Must also involve real kinetic strike context (missile/drone/strike/target hit)
        has_weapon_or_hit = (
            self._contains_term(norm_sent, "موشک")
            or self._contains_term(norm_sent, "پهپاد")
            or self._contains_term(norm_sent, "هدف قرار داد")
            or self._contains_term(norm_sent, "حمله موشکی")
            or self._contains_term(norm_sent, "حمله پهپادی")
            or self._contains_term(norm_sent, "حمله کرد")
            or self._contains_term(norm_sent, "حمله هوایی")
            or self._contains_term(norm_sent, "بمباران")
            or self._contains_term(norm_sent, "اصابت")
        )
        return has_weapon_or_hit

    def _clause_is_foreign_attack_on_hormuz(self, norm_sent: str) -> bool:
        """
        True ONLY when a foreign attacker strikes/fires toward the Strait of Hormuz or Persian Gulf.
        Iranian launches toward Hormuz or unattributed Hormuz explosions stay SILENT.
        """
        if not self._contains_any(norm_sent, self.geo_maritime):
            return False

        has_foreign_attacker = self._contains_any(norm_sent, self.foreign_attackers)
        if not has_foreign_attacker:
            return False

        # Guard against statements where Iran is the attacker against US assets in Hormuz (e.g. "جنگنده‌های آمریکایی نتوانستند... تجهیزات آمریکا منهدم شد")
        if re.search(r"سخنگوی\s+سپاه|نیروی\s+دریایی\s+سپاه|سپاه\s+پاسداران", norm_sent):
            if not re.search(r"حمله\s+(?:آمریکا|اسرائیل|آمریکایی|اسرائیلی|از\s+خارج)|شلیک\s+(?:آمریکا|اسرائیل)|توسط\s+(?:آمریکا|اسرائیل)", norm_sent):
                return False

        # Require explicit foreign-initiated attack construction or direction toward Hormuz/Iran
        foreign_attack_pattern = bool(
            re.search(
                r"(?:حمله|حملات|بمباران|شلیک|پرتاب|اصابت)\s+(?:توسط\s+|از\s+سوی\s+|از\s+|هوایی\s+|موشکی\s+|پهپادی\s+|نیرو(?:\s*های)?\s+|جنگنده(?:\s*های)?\s+|ناو(?:\s*های)?\s+|پهپاد(?:\s*های)?\s+|موشک(?:\s*های)?\s+){0,3}(?:آمریکا|آمریکایی|ایالات متحده|اسرائیل|اسرائیلی|انگلیس|بریتانیا|سنتکام|خارج)",
                norm_sent,
            )
            or re.search(
                r"(?:آمریکا|ایالات متحده|اسرائیل|انگلیس|سنتکام|ناو(?:\s*های)?\s+آمریکا(?:یی)?|جنگنده(?:\s*های)?\s+آمریکا(?:یی)?).{0,45}(?:حمله|بمباران|شلیک|پرتاب|موشک|هدف\s+قرار\s+داد)",
                norm_sent,
            )
        )
        return foreign_attack_pattern

    def _is_war_urgent(self, eval_raw_text: str, is_iran_internet: bool = False) -> bool:
        if is_iran_internet:
            return True

        sentences = self._split_sentences_and_clauses(eval_raw_text)
        if not sentences:
            return False

        # Post-level debunk / maneuver / ordinary accident suppression:
        # e.g., Sentence 1 reports an explosion sound, Sentence 2 clarifies it was a drill or gas leak.
        full_norm = self.clean_for_matching(self.normalize_text(eval_raw_text))
        if self._contains_any(full_norm, self.non_combat_debunks) or self._contains_any(full_norm, self.ordinary_accidents):
            return False

        for norm_sent, clauses in sentences:
            if self._is_blocked_sentence(norm_sent):
                continue

            # Path 1: Inbound / Domestic kinetic, terror, quake, or plane crash on Iranian mainland/airspace
            for clause in clauses:
                if self._clause_is_inbound_or_domestic_iran_event(clause, norm_sent):
                    return True

            # Path 2: Outbound Iranian strike against foreign targets (excluding Hormuz)
            if self._clause_is_outbound_iran_strike(norm_sent):
                return True

            # Path 3: Foreign attack against Strait of Hormuz / Persian Gulf
            if self._clause_is_foreign_attack_on_hormuz(norm_sent):
                return True

        return False

    def classify_severity(
        self,
        eval_raw_text: str,
        target_channel: str,
        is_iran_internet: bool = False,
    ) -> Tuple[str, bool]:
        """
        Determines alert sound severity for a given target channel.
        Returns: (severity_label, disable_notification)
        """
        norm_text = self.clean_for_matching(self.normalize_text(eval_raw_text))

        if target_channel == config.CHANNELS["WAR"]:
            if self._is_war_urgent(eval_raw_text, is_iran_internet=is_iran_internet):
                return "URGENT", False
            return "SILENT", True

        elif target_channel == config.CHANNELS["PROTEST"]:
            if is_iran_internet or self._contains_any(norm_text, self.protest_urgent_triggers):
                return "URGENT", False
            return "SILENT", True

        elif target_channel == config.CHANNELS["ECONOMY"]:
            if self._contains_any(norm_text, self.economy_urgent_triggers):
                return "URGENT", False
            return "SILENT", True

        return "SILENT", True

    # =========================================================================
    # UNIFIED EXECUTION PIPELINE
    # =========================================================================
    def process_message(self, raw_text: str, source_node: str = "") -> Optional[Dict]:
        """
        Full 3-step pipeline for a single incoming message.
        Returns:
          - None: dropped noise / non-Iran netblocks / irrelevant chatter
          - {"status": "PENDING_TRANSLATION"}: @Ali_bk_8 post without Persian translation yet
          - {"status": "MATCHED", "target_channels": [...], "dispatches_meta": [...], "output_text": ..., ...}
        """
        eval_raw_text, output_raw_text = self._prepare_source_text(raw_text, source_node)
        if eval_raw_text is None:
            if output_raw_text == "PENDING_TRANSLATION":
                return {"status": "PENDING_TRANSLATION", "source_node": source_node}
            return None

        target_channels, is_iran_internet = self._evaluate_routes(eval_raw_text, source_node)
        if not target_channels:
            return None

        dispatches_meta = []
        any_urgent = False
        for ch in target_channels:
            sev, disable_notif = self.classify_severity(eval_raw_text, ch, is_iran_internet=is_iran_internet)
            if sev == "URGENT":
                any_urgent = True
            dispatches_meta.append({
                "target_channel": ch,
                "tag": config.CHANNEL_TAGS.get(ch, ""),
                "severity": sev,
                "disable_notification": disable_notif,
            })

        primary = dispatches_meta[0]
        return {
            "status": "MATCHED",
            "target_channel": primary["target_channel"],
            "target_channels": target_channels,
            "dispatches_meta": dispatches_meta,
            "tag": primary["tag"],
            "severity": "URGENT" if any_urgent else "SILENT",
            "disable_notification": not any_urgent,
            "source_node": source_node,
            "output_text": output_raw_text,
            "is_iran_internet": is_iran_internet,
        }

    # =========================================================================
    # CROSS-SOURCE EVENT DEDUPLICATION & FINGERPRINTING (v6)
    # =========================================================================
    @classmethod
    def _dedup_norm(cls, text: str) -> str:
        """Normalizes Persian/Arabic alef variants, strips all Arabic/Latin punctuation, and normalizes whitespace."""
        norm = cls.normalize_text(text)
        norm = norm.replace("أ", "ا").replace("إ", "ا").replace("آ", "ا").replace("ؤ", "و").replace("ئ", "ی")
        # Strip Arabic/Persian punctuation (U+060C comma, U+061B semicolon, U+061F ?, U+0640 tatweel, etc.) & symbols
        norm = re.sub(r"[\u060C\u061B\u061F\u066B\u066C\u06D4\u0640«»\"'`.,:;!?@#$%^&*()\[\]{}<>/\\|~+=_-]+", " ", norm)
        norm = re.sub(r"[^\w\s\u0600-\u06FF]", " ", norm)
        return re.sub(r"\s+", " ", norm).strip()

    @staticmethod
    def _stem_persian_token(token: str) -> str:
        """Safe plural suffix stripping (preserves roots like ایران / سازمان / تولید / تصمیم)."""
        if len(token) > 4:
            stemmed = re.sub(r"(?:هایی|های|ها)$", "", token)
            if len(stemmed) >= 3:
                return stemmed
        return token

    @staticmethod
    def strip_promotional_noise(raw_text: str) -> str:
        """Removes @mentions, URLs, hashtags, and footer watermarks for clean length/fingerprint comparison."""
        if not raw_text:
            return ""
        text = re.sub(r"https?://\S+", " ", raw_text)
        text = re.sub(r"@[\w_]+", " ", text)
        text = re.sub(r"#[a-zA-Z0-9_\u0600-\u06FF]+", " ", text)
        text = re.sub(r"\*+", "", text)
        return re.sub(r"\s+", " ", text).strip()

    def build_event_fingerprint(self, raw_text: str, source_node: str = "") -> Dict:
        """
        Builds a pure-Python weighted event fingerprint containing:
          - token_weights: distinctive entity/number anchors (weight 3) vs boilerplate terms (weight 1)
          - anchors: set of weight-3 tokens
          - specific_locations: extracted sub-national cities, bases, islands, fronts
        """
        clean_node = (source_node or "").lstrip("@").lower()
        body = raw_text or ""
        if clean_node == "ali_bk_8" and self.has_persian_text(body):
            body = self.extract_persian_text(body) or body

        stripped = self.strip_promotional_noise(body)
        norm = self._dedup_norm(self.clean_for_matching(self.normalize_text(stripped)))

        for phrase_src, phrase_dst in self.dedup_phrase_replacements:
            if phrase_src in norm:
                norm = norm.replace(phrase_src, phrase_dst)

        specific_locations = set()
        for loc, pat in self._dedup_location_patterns:
            if pat.search(norm):
                specific_locations.add(loc)

        token_weights: Dict[str, int] = {}
        anchors = set()
        numbers = set()

        for raw_tok in norm.split():
            tok = raw_tok.strip("_")
            if not tok:
                continue
            is_num = tok.isdigit()
            if len(tok) <= 1 and not is_num:
                continue
            if tok in self.dedup_stopwords:
                continue

            if not is_num:
                tok = self.dedup_synonyms.get(tok, tok)
                tok = self._stem_persian_token(tok)
                tok = self.dedup_synonyms.get(tok, tok)
                if len(tok) <= 1 or tok in self.dedup_stopwords:
                    continue

            if is_num:
                if tok in {"0", "1"}:
                    continue
                weight = 3
                numbers.add(tok)
                anchors.add(tok)
            elif tok in self.dedup_low_weight_terms:
                weight = 1
            else:
                weight = 3
                anchors.add(tok)

            if token_weights.get(tok, 0) < weight:
                token_weights[tok] = weight

        return {
            "source_node": clean_node,
            "is_english_netblocks": (clean_node == "netblocks"),
            "token_weights": token_weights,
            "anchors": anchors,
            "numbers": numbers,
            "specific_locations": specific_locations,
            "char_len": len(stripped),
        }

    def is_duplicate_event(self, new_fp: Dict, existing_fp: Dict) -> bool:
        """
        Determines whether two news posts describe the same real-world event using
        rare-entity weighted overlap + specific-location conflict veto.
        """
        if not new_fp or not existing_fp:
            return False

        # Never merge/drop @netblocks English reports against Persian posts
        if new_fp.get("is_english_netblocks") != existing_fp.get("is_english_netblocks"):
            return False

        w1 = new_fp.get("token_weights") or {}
        w2 = existing_fp.get("token_weights") or {}
        if not w1 or not w2:
            return False

        locs1 = new_fp.get("specific_locations") or set()
        locs2 = existing_fp.get("specific_locations") or set()
        if locs1 and locs2 and not (locs1 & locs2):
            return False

        shared_tokens = set(w1.keys()) & set(w2.keys())
        if not shared_tokens:
            return False

        shared_weight = sum(min(w1[t], w2[t]) for t in shared_tokens)
        total1 = sum(w1.values())
        total2 = sum(w2.values())
        min_total_weight = min(total1, total2)
        max_total_weight = max(total1, total2)
        if min_total_weight <= 0:
            return False

        weighted_overlap = shared_weight / min_total_weight
        weight_ratio = max_total_weight / min_total_weight
        shared_anchors = (new_fp.get("anchors") or set()) & (existing_fp.get("anchors") or set())

        src1 = new_fp.get("source_node", "")
        src2 = existing_fp.get("source_node", "")
        if src1 and src1 == src2:
            # Same-source: block only near-exact duplicates, preserve follow-up dispatches
            return weighted_overlap >= 0.88 and weight_ratio <= 2.0 and len(shared_anchors) >= 3

        # Guard against a massive omnibus roundup (e.g. 16-min speech summary) swallowing a 1-line breaking alert
        if weight_ratio > 4.5 and weighted_overlap < 0.85:
            return False

        # Complementary Rule: breaking news sharing a specific location + incident/kinetic event
        shared_locs = locs1 & locs2
        if shared_locs and weight_ratio <= 3.5:
            incident_tokens = {
                "انفجار", "حمله", "شلیک", "برخورد", "موشک", "پهپاد", "پدافند",
                "اتش", "سقوط", "اصابت", "پرتاب", "تیراندازی", "توقیف", "کشته", "زخمی", "تلفات"
            }
            if (shared_tokens & incident_tokens) and weighted_overlap >= 0.45 and len(shared_anchors) >= 1:
                return True

        if weighted_overlap >= 0.55 and len(shared_anchors) >= 3:
            return True
        if weighted_overlap >= 0.65 and len(shared_anchors) >= 2:
            return True
        if weight_ratio <= 4.0 and weighted_overlap >= 0.40 and len(shared_anchors) >= 7:
            return True
        if weight_ratio <= 2.5 and weighted_overlap >= 0.34 and len(shared_anchors) >= 11:
            return True
        return False

    def should_upgrade_text(self, current_text: str, candidate_text: str) -> bool:
        """Returns True if candidate_text is meaningfully richer/longer than current_text."""
        cur_clean = self.strip_promotional_noise(current_text)
        cand_clean = self.strip_promotional_noise(candidate_text)
        cur_len = len(cur_clean)
        cand_len = len(cand_clean)
        if cand_len <= cur_len:
            return False
        if cur_len < 120 and (cand_len - cur_len) >= 30:
            return True
        return cand_len >= cur_len * 1.25 and (cand_len - cur_len) >= 40
