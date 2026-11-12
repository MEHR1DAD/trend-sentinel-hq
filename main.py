#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
main.py - Trend Sentinel v5
Autonomous, Real-time Telegram Listener & Zero-AI Dispatcher.
Listens to 6 VIP news sources, filters through the Clause-Level Deterministic Engine,
and dispatches instantaneous alerts to @DidebanJang, @DidehbanEghtesad, and @DidebanEterazat.
"""

import os
import sys
import json
import time
import html
import re
import signal
import asyncio
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime, timezone
from typing import Optional
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.tl import types
from telethon.tl.types import InputPeerChannel
from telethon.errors import FloodWaitError

import config
from engine import SentinelEngine

class TrendSentinelV4:
    ICON_ORDER = ("📷", "🎥", "🔊", "📎", "📊", "📍", "👤")
    TEXT_ICON = "📝"

    def __init__(self):
        self.engine = SentinelEngine()
        self.start_time = time.time()
        self.messages_processed = 0
        self.alerts_dispatched = 0
        self.last_msg_text = "None"
        self.last_msg_time = "None"

        # Load peer cache
        self.peer_cache = self._load_peer_cache()

        # Load posted registry (Deduplication)
        self.posted_registry = self._load_registry()

        # Load channel state, urgent alert cooldowns, 60m sent alerts map & pending @Ali_bk_8 translations
        self.channel_state, self.urgent_alert_state, self.sent_alerts_map, self.pending_translations = self._load_channel_state()
        self.alerts_edited = 0
        self.alerts_deleted = 0
        self.album_cache = {}  # Bounded cache keyed by f"{source_node}_{grouped_id}" -> {msg_id: Message}

        # Anti-spam cooldown: 15 minutes between loud urgent alerts per channel
        self.URGENT_COOLDOWN_SEC = 15 * 60

        # Telethon client
        self.client = TelegramClient(
            StringSession(config.SESSION_STRING),
            config.API_ID,
            config.API_HASH
        )

    def _load_peer_cache(self) -> dict:
        if os.path.exists(config.PEER_CACHE_PATH):
            try:
                with open(config.PEER_CACHE_PATH, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                print(f"⚠️ Error reading peer cache: {e}")
        return {"node_peers": {}, "id_to_node": {}}

    def _load_registry(self) -> set:
        if os.path.exists(config.POSTED_REGISTRY_PATH):
            try:
                with open(config.POSTED_REGISTRY_PATH, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    return set(data.get("posted_keys", []))
            except Exception as e:
                print(f"⚠️ Error reading posted registry: {e}")
        return set()

    def _save_registry(self):
        try:
            # Keep only the latest 2500 entries to prevent file bloat
            recent_keys = list(self.posted_registry)[-2500:]
            with open(config.POSTED_REGISTRY_PATH, "w", encoding="utf-8") as f:
                json.dump({"posted_keys": recent_keys, "updated_at": datetime.now(timezone.utc).isoformat()}, f, indent=2)
        except Exception as e:
            print(f"⚠️ Error saving posted registry: {e}")

    def _load_channel_state(self) -> tuple:
        if os.path.exists(config.CHANNEL_STATE_PATH):
            try:
                with open(config.CHANNEL_STATE_PATH, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    return (
                        data.get("channels", {}),
                        data.get("last_urgent_alert", {}),
                        data.get("sent_alerts_map", {}),
                        data.get("pending_translations", {}),
                    )
            except Exception as e:
                print(f"⚠️ Error reading channel state: {e}")
        return {}, {}, {}, {}

    def _prune_sent_alerts(self):
        """Prunes alerts and pending translations older than the 60m window."""
        now = time.time()
        self.sent_alerts_map = {
            key: val
            for key, val in self.sent_alerts_map.items()
            if now - float(val.get("sent_timestamp", 0)) <= config.EDIT_DELETE_WINDOW_SEC
        }
        self.pending_translations = {
            key: val
            for key, val in self.pending_translations.items()
            if now - float(val.get("timestamp", 0)) <= config.EDIT_DELETE_WINDOW_SEC
        }

    def _save_channel_state(self):
        try:
            self._prune_sent_alerts()
            with open(config.CHANNEL_STATE_PATH, "w", encoding="utf-8") as f:
                json.dump({
                    "channels": self.channel_state,
                    "last_urgent_alert": self.urgent_alert_state,
                    "sent_alerts_map": self.sent_alerts_map,
                    "pending_translations": self.pending_translations,
                    "updated_at": datetime.now(timezone.utc).isoformat()
                }, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"⚠️ Error saving channel state: {e}")

    @staticmethod
    def _get_dispatches(alert_info: dict) -> list:
        """
        Normalizes sent_alerts_map entries into a list of dispatch records
        for backward compatibility between v4 single-target and v5 multi-target entries.
        """
        dispatches = alert_info.get("dispatches")
        if isinstance(dispatches, list) and dispatches:
            return dispatches
        target_ch = alert_info.get("target_channel")
        bot_msg_id = alert_info.get("bot_msg_id")
        if target_ch and bot_msg_id:
            synthesized = [{
                "target_channel": target_ch,
                "bot_msg_id": bot_msg_id,
                "status_icon": alert_info.get("status_icon", "🔕"),
                "last_formatted_text": alert_info.get("last_formatted_text", ""),
            }]
            alert_info["dispatches"] = synthesized
            return synthesized
        return []

    def resolve_alert_mode_and_icon(self, target_channel: str, is_engine_urgent: bool) -> tuple:
        """
        Anti-Spam 15-Minute Rule:
        - In each channel, an URGENT alert can only be sent loud once every 15 minutes.
        - Subsequent urgent alerts within 15 minutes are downgraded to SILENT and use the combined icon: 🚨🔕
        - Normal silent alerts use: 🔕
        Returns: (disable_notification: bool, status_icon: str)
        """
        if not is_engine_urgent:
            return True, "🔕"

        now = time.time()
        last_urgent_ts = float(self.urgent_alert_state.get(target_channel, 0))
        elapsed = now - last_urgent_ts

        if elapsed >= self.URGENT_COOLDOWN_SEC:
            # Cooldown passed: Allow loud alert with 🚨
            self.urgent_alert_state[target_channel] = now
            self._save_channel_state()
            return False, "🚨"
        else:
            # Within 15-min cooldown: Downgrade sound and use combined icon 🚨🔕
            return True, "🚨🔕"

    def get_last_read_id(self, node_name: str) -> int:
        clean_node = node_name.lstrip('@')
        ch_info = self.channel_state.get(clean_node, {})
        return int(ch_info.get("last_read_id", 0))

    def update_last_read_id(self, node_name: str, msg_id: int, date=None):
        clean_node = node_name.lstrip('@')
        current = self.get_last_read_id(clean_node)
        new_id = max(current, msg_id)
        date_str = date.strftime("%Y-%m-%d %H:%M:%S UTC") if date else datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

        self.channel_state[clean_node] = {
            "last_read_id": new_id,
            "last_read_time": date_str
        }
        self._save_channel_state()

    def update_session_report(self):
        uptime_min = int((time.time() - self.start_time) / 60)

        cursor_lines = []
        for ch in config.VIP_CHANNELS:
            st = self.channel_state.get(ch, {})
            lid = st.get("last_read_id", "None")
            ltime = st.get("last_read_time", "None")
            cursor_lines.append(f"- `@{ch}`: #{lid} ({ltime})")

        urgent_status_lines = []
        for dch in config.CHANNELS.values():
            lts = float(self.urgent_alert_state.get(dch, 0))
            if lts:
                elapsed_min = int((time.time() - lts) / 60)
                urgent_status_lines.append(f"- `{dch}`: Last loud alert {elapsed_min}m ago")
            else:
                urgent_status_lines.append(f"- `{dch}`: No loud alert yet")

        edit_watcher_lines = []
        now = time.time()
        for k, v in self.sent_alerts_map.items():
            mins_ago = int((now - float(v.get("sent_timestamp", now))) / 60)
            icons = v.get("media_icons", self.TEXT_ICON)
            dispatches = self._get_dispatches(v)
            dest_summary = ", ".join(f"{d.get('target_channel')} (#{d.get('bot_msg_id')})" for d in dispatches)
            edit_watcher_lines.append(f"- `{k}` -> {dest_summary} ({icons}, {mins_ago}m ago)")

        if not edit_watcher_lines:
            edit_watcher_lines = ["- *(No active posts in 60m edit/delete window)*"]

        report_content = (
            f"# Sentinel Session Report (v{config.VERSION} Zero-Race Engine)\n\n"
            f"- **Uptime:** {uptime_min} minutes\n"
            f"- **Messages Processed:** {self.messages_processed}\n"
            f"- **Alerts Dispatched:** {self.alerts_dispatched}\n"
            f"- **Alerts Edited:** {self.alerts_edited}\n"
            f"- **Alerts Deleted:** {self.alerts_deleted}\n"
            f"- **Active VIP Nodes:** {len(config.VIP_CHANNELS)} channels\n"
            f"- **Last Message Text:** {self.last_msg_text[:120]}\n"
            f"- **Last Message Time:** {self.last_msg_time}\n\n"
            f"### 📌 حافظه وضعیت کانال‌ها (Channel Last Read Memory):\n"
            + "\n".join(cursor_lines) + "\n\n"
            f"### ⏱️ وضعیت بازه ۱۵ دقیقه‌ای هشدار فوری (Anti-Spam 15m Cooldown):\n"
            + "\n".join(urgent_status_lines) + "\n\n"
            f"### 🔄 پیام‌های تحت پایش برای ویرایش و حذف (Active 60m Edit/Delete Watchers):\n"
            + "\n".join(edit_watcher_lines) + "\n"
        )
        try:
            with open(config.SESSION_REPORT_PATH, "w", encoding="utf-8") as f:
                f.write(report_content)
        except Exception as e:
            print(f"⚠️ Error updating session report: {e}")

    def get_input_peer(self, node_name: str):
        clean_name = node_name.lstrip('@')
        peers = self.peer_cache.get("node_peers", {})
        for key, val in peers.items():
            if key.lstrip('@').lower() == clean_name.lower():
                cid = val.get("channel_id")
                ah = val.get("access_hash")
                if cid and ah:
                    return InputPeerChannel(channel_id=int(cid), access_hash=int(ah))
        return None

    @classmethod
    def detect_media_icons(cls, messages) -> str:
        """
        Inspects Telegram Message objects (single message or album siblings) and returns
        source content icon(s): 📷 photo, 🎥 video/GIF, 🔊 voice/audio, 📎 file/document,
        📊 poll, 📍 location, 👤 contact. Returns 📝 only for text-only posts (including
        URL web previews) when no media attachment is present.
        """
        if messages is None:
            messages = []
        elif not isinstance(messages, (list, tuple)):
            messages = [messages]

        found = set()
        for msg in messages:
            media = getattr(msg, "media", None)
            if not media or isinstance(media, (types.MessageMediaEmpty, types.MessageMediaWebPage)):
                continue

            if isinstance(media, types.MessageMediaPhoto):
                found.add("📷")
            elif isinstance(media, types.MessageMediaDocument):
                doc = getattr(media, "document", None)
                attrs = getattr(doc, "attributes", None) or []
                mime = (getattr(doc, "mime_type", "") or "").lower()

                has_video = any(isinstance(a, types.DocumentAttributeVideo) for a in attrs)
                has_animated = any(isinstance(a, types.DocumentAttributeAnimated) for a in attrs)
                has_audio = any(isinstance(a, types.DocumentAttributeAudio) for a in attrs)
                has_sticker = any(isinstance(a, types.DocumentAttributeSticker) for a in attrs)

                if has_video or has_animated or mime.startswith("video/"):
                    found.add("🎥")
                elif has_audio or mime.startswith("audio/"):
                    found.add("🔊")
                elif not has_sticker:
                    found.add("📎")
            elif isinstance(media, types.MessageMediaPoll):
                found.add("📊")
            elif isinstance(media, (types.MessageMediaGeo, types.MessageMediaGeoLive, types.MessageMediaVenue)):
                found.add("📍")
            elif isinstance(media, types.MessageMediaContact):
                found.add("👤")
            elif isinstance(media, types.MessageMediaPaidMedia):
                ext = getattr(media, "extended_media", None) or []
                paid_matched = False
                for item in ext:
                    sub = getattr(item, "media", None)
                    if isinstance(sub, types.MessageMediaPhoto):
                        found.add("📷")
                        paid_matched = True
                    elif isinstance(sub, types.MessageMediaDocument):
                        found.add("🎥")
                        paid_matched = True
                if not paid_matched:
                    found.add("📎")

        ordered = [icon for icon in cls.ICON_ORDER if icon in found]
        return "".join(ordered) if ordered else cls.TEXT_ICON

    def _record_album_message(self, source_node: str, msg) -> Optional[str]:
        """Caches album sibling messages by (source_node, grouped_id) and returns cache key if grouped."""
        grouped_id = getattr(msg, "grouped_id", None)
        if not grouped_id:
            return None
        cache_key = f"{source_node}_{grouped_id}"
        group_dict = self.album_cache.setdefault(cache_key, {})
        group_dict[msg.id] = msg
        if len(self.album_cache) > 200:
            oldest_key = next(iter(self.album_cache))
            self.album_cache.pop(oldest_key, None)
        return cache_key

    def _find_alert_by_group(self, source_node: str, grouped_id) -> Optional[tuple]:
        """Finds an active (<60m) dispatched alert entry matching (source_node, grouped_id)."""
        if not grouped_id:
            return None
        for key, info in self.sent_alerts_map.items():
            if info.get("source_node") == source_node and info.get("grouped_id") == grouped_id:
                return key, info
        return None

    @staticmethod
    def sanitize_and_format_alert(raw_text: str, source_node: str, msg_id: int, status_icon: str, media_icons: str = "📝") -> str:
        """
        Sanitizes raw text, removes asterisks, strips inline & trailing promotional watermarks
        (including @VahidHeadline, @VahidOOnLine, and '📡 @VahidOnline'),
        and formats with clean inline status icon and hyperlinked source attribution.
        """
        lines = [l.strip() for l in raw_text.split('\n')]

        # 1. Pop watermarks and promotional links from the bottom
        while lines:
            last = lines[-1]
            if not last:
                lines.pop()
                continue
            is_watermark = (
                re.match(r'^#[a-zA-Z0-9_\u0600-\u06FF]+(\s+#[a-zA-Z0-9_\u0600-\u06FF]+)*$', last) or
                re.match(r'^https?://t\.me/[\w/]+$', last) or
                re.match(r'^📡?\s*@[\w_]+(\s+@[\w_]+)*$', last) or
                re.match(r'^📡?\s*منبع:?.*$', last) or
                last in ["📡", "🔗", "📌", "📢"] or
                't.me/' in last or
                'عضویت در کانال' in last or
                'کانال تلگرام' in last or
                'لینک کانال' in last
            )
            if is_watermark:
                lines.pop()
            else:
                break

        # 2. Strip raw asterisks, trailing and inline channel watermarks
        # Strips @WarRoom, @withyashar, @VahidOnline, @VahidHeadline, etc. (including links and brackets)
        watermark_channels = r'WarRoom|War_Room|withyashar|VahidHeadline|VahidOOnLine|VahidOnline|Ali_bk_8|iliaen|kianmeli1|netblocks'
        trailing_watermark_pattern = re.compile(
            rf'\s*[\(\[]?\s*(?:https?://)?(?:t\.me/|@)(?:{watermark_channels})\b\s*[\)\]]?\s*$',
            re.IGNORECASE
        )
        inline_watermarks_pattern = re.compile(
            rf'\s*[\(\[]?\s*(?:https?://)?(?:t\.me/|@)(?:{watermark_channels})\b\s*[\)\]]?',
            re.IGNORECASE
        )
        cleaned_lines = []
        for line in lines:
            c_line = re.sub(r'\*+', '', line).strip()
            c_line = trailing_watermark_pattern.sub('', c_line).strip()
            c_line = inline_watermarks_pattern.sub('', c_line).strip()
            cleaned_lines.append(c_line)

        # Pop any trailing empty lines again
        while cleaned_lines and not cleaned_lines[-1]:
            cleaned_lines.pop()

        if not cleaned_lines:
            return ""

        # 3. Group into clean paragraphs with HTML escaping
        paragraphs = []
        current_p = []
        for line in cleaned_lines:
            if line:
                current_p.append(html.escape(line))
            else:
                if current_p:
                    paragraphs.append(' '.join(current_p))
                    current_p = []
        if current_p:
            paragraphs.append(' '.join(current_p))

        if not paragraphs:
            return ""

        # 4. Prefix status icon on the first paragraph/headline
        first_p = paragraphs[0].strip()
        formatted_first_p = f"{status_icon} {first_p}"

        all_body = [formatted_first_p] + paragraphs[1:]
        body_text = "\n\n".join(all_body)

        # 5. Clean single-line hyperlinked source footer with source content icon(s)
        clean_node = source_node.lstrip('@')
        source_url = f"https://t.me/{clean_node}/{msg_id}"
        icons_prefix = media_icons or "📝"
        footer = f'{icons_prefix} منبع: <a href="{source_url}">@{clean_node}</a>'

        return f"{body_text}\n\n{footer}"

    def send_bot_alert(self, target_channel: str, html_text: str, disable_notif: bool):
        """Dispatches formatted alert to destination Telegram channel via Bot API. Returns bot_msg_id or None."""
        if not config.BOT_TOKEN:
            print("⚠️ TELEGRAM_BOT_TOKEN is not configured!")
            return None

        url = f"https://api.telegram.org/bot{config.BOT_TOKEN}/sendMessage"
        payload = {
            "chat_id": target_channel,
            "text": html_text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
            "disable_notification": disable_notif
        }

        try:
            data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=10) as response:
                if response.status == 200:
                    resp_json = json.loads(response.read().decode("utf-8"))
                    return resp_json.get("result", {}).get("message_id")
        except Exception as e:
            # Fallback: if HTML parsing failed, strip HTML tags and retry with plain text
            print(f"⚠️ HTML send failed: {e}. Retrying without parse_mode...")
            try:
                plain_text = re.sub(r'<[^>]+>', '', html_text)
                payload["text"] = plain_text
                del payload["parse_mode"]
                data = json.dumps(payload).encode("utf-8")
                req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=10) as response:
                    if response.status == 200:
                        resp_json = json.loads(response.read().decode("utf-8"))
                        return resp_json.get("result", {}).get("message_id")
            except Exception as e2:
                print(f"❌ Failed fallback alert to {target_channel}: {e2}")
        return None

    def edit_bot_alert(self, target_channel: str, bot_msg_id: int, html_text: str) -> bool:
        """
        Updates an already dispatched alert in the target channel via Telegram Bot API editMessageText.
        Edits are always silent.
        """
        if not config.BOT_TOKEN:
            print("⚠️ TELEGRAM_BOT_TOKEN is not configured!")
            return False

        url = f"https://api.telegram.org/bot{config.BOT_TOKEN}/editMessageText"
        payload = {
            "chat_id": target_channel,
            "message_id": bot_msg_id,
            "text": html_text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True
        }

        try:
            data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=10) as response:
                if response.status == 200:
                    return True
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="ignore")
            # If content hasn't changed or matches exactly, Telegram returns 400 'message is not modified'
            if "message is not modified" in err_body:
                print(f"ℹ️ Message {bot_msg_id} in {target_channel} is already up to date.")
                return True
            print(f"⚠️ HTML edit failed: {e} ({err_body}). Retrying without parse_mode...")
            try:
                plain_text = re.sub(r'<[^>]+>', '', html_text)
                payload["text"] = plain_text
                del payload["parse_mode"]
                data = json.dumps(payload).encode("utf-8")
                req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=10) as response:
                    if response.status == 200:
                        return True
            except urllib.error.HTTPError as e2:
                err_body2 = e2.read().decode("utf-8", errors="ignore")
                if "message is not modified" in err_body2:
                    return True
                print(f"❌ Failed fallback edit to {target_channel}: {e2} ({err_body2})")
            except Exception as e2:
                print(f"❌ Failed fallback edit to {target_channel}: {e2}")
        except Exception as e:
            print(f"⚠️ Edit request error: {e}")
        return False

    def delete_bot_alert(self, target_channel: str, bot_msg_id: int) -> bool:
        """
        Deletes an already dispatched alert in the target channel via Telegram Bot API deleteMessage.
        """
        if not config.BOT_TOKEN:
            print("⚠️ TELEGRAM_BOT_TOKEN is not configured!")
            return False

        url = f"https://api.telegram.org/bot{config.BOT_TOKEN}/deleteMessage"
        payload = {
            "chat_id": target_channel,
            "message_id": bot_msg_id
        }

        try:
            data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=10) as response:
                if response.status == 200:
                    return True
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="ignore")
            if "message to delete not found" in err_body.lower():
                print(f"ℹ️ Message {bot_msg_id} in {target_channel} was already deleted.")
                return True
            print(f"❌ Failed delete in {target_channel} (#{bot_msg_id}): {e} ({err_body})")
        except Exception as e:
            print(f"⚠️ Delete request error for {target_channel} (#{bot_msg_id}): {e}")
        return False

    def _resolve_msgs_for_post(self, msg, source_node: str, album_msgs: Optional[list] = None) -> list:
        """Returns the list of Telegram Message objects representing a post (single or album siblings)."""
        if album_msgs is not None:
            return list(album_msgs)
        cache_key = self._record_album_message(source_node, msg)
        if cache_key and cache_key in self.album_cache:
            return list(self.album_cache[cache_key].values())
        return [msg]

    async def _sync_grouped_media_if_dispatched(self, source_node: str, grouped_id) -> bool:
        """
        If an alert for this (source_node, grouped_id) has already been dispatched within 60m,
        recalculates combined media icons across all cached album items and silently updates the alert.
        Returns True if the group already has an active alert, False otherwise.
        """
        match = self._find_alert_by_group(source_node, grouped_id)
        if not match:
            return False

        dedup_key, alert_info = match
        now = time.time()
        elapsed = now - float(alert_info.get("sent_timestamp", 0))
        if elapsed > config.EDIT_DELETE_WINDOW_SEC:
            return True

        album_msgs = list(self.album_cache.get(f"{source_node}_{grouped_id}", {}).values())
        if not album_msgs:
            return True

        new_icons = self.detect_media_icons(album_msgs)
        display_text = alert_info.get("output_text") or alert_info.get("raw_text") or next(
            ((getattr(m, "raw_text", None) or getattr(m, "text", None) or "") for m in album_msgs if getattr(m, "raw_text", None) or getattr(m, "text", None)),
            ""
        )
        if not display_text:
            return True

        primary_msg_id = int(alert_info.get("msg_id"))
        dispatches = self._get_dispatches(alert_info)
        changed = False
        for d in dispatches:
            target_ch = d["target_channel"]
            bot_msg_id = d["bot_msg_id"]
            status_icon = d.get("status_icon", alert_info.get("status_icon", "🔕"))
            formatted_text = self.sanitize_and_format_alert(display_text, source_node, primary_msg_id, status_icon, media_icons=new_icons)
            if not formatted_text or formatted_text == d.get("last_formatted_text"):
                continue
            print(f"🖼️ [MEDIA SYNC] Updating album icons ({new_icons}) on {target_ch} (bot_msg_id: {bot_msg_id})")
            if self.edit_bot_alert(target_ch, bot_msg_id, formatted_text):
                d["last_formatted_text"] = formatted_text
                self.alerts_edited += 1
                changed = True

        if changed:
            alert_info["media_icons"] = new_icons
            if dispatches:
                alert_info["last_formatted_text"] = dispatches[0].get("last_formatted_text", "")
            self._save_channel_state()
            self.update_session_report()
        return True

    async def handle_edited_message(self, msg, source_node: str, album_msgs: Optional[list] = None):
        """
        Handles edited messages (including media changes & late Persian translations on @Ali_bk_8).
        Rule:
          1. If an untranslated pending post receives its Persian translation within 60m,
             evaluate and dispatch it immediately as a fresh alert.
          2. If an alert was already dispatched within the last 60m, synchronize text,
             severity icon, and media icons across all target channels in silence.
        """
        dedup_key = f"{source_node}_{msg.id}"
        if dedup_key not in self.sent_alerts_map:
            grouped_id = getattr(msg, "grouped_id", None)
            match = self._find_alert_by_group(source_node, grouped_id) if grouped_id else None
            if not match:
                # Late translation check for pending @Ali_bk_8 posts or unposted fresh edits within 60m
                is_pending = dedup_key in self.pending_translations
                if (is_pending or source_node == "Ali_bk_8") and dedup_key not in self.posted_registry:
                    msg_text = getattr(msg, "raw_text", None) or getattr(msg, "text", None) or ""
                    if msg_text and msg_text.strip():
                        eval_probe = self.engine.process_message(msg_text, source_node)
                        if eval_probe and eval_probe.get("status") == "MATCHED":
                            print(f"🌐 [TRANSLATION READY] @{source_node}/#{msg.id} edited with Persian text -> dispatching")
                            await self.handle_new_message(msg, source_node, album_msgs=album_msgs)
                        elif eval_probe is None and is_pending:
                            self.pending_translations.pop(dedup_key, None)
                            self._save_channel_state()
                return
            self._record_album_message(source_node, msg)
            dedup_key, alert_info = match
        else:
            alert_info = self.sent_alerts_map[dedup_key]

        primary_msg_id = int(alert_info.get("msg_id", msg.id))
        now = time.time()
        elapsed = now - float(alert_info.get("sent_timestamp", 0))

        if elapsed > config.EDIT_DELETE_WINDOW_SEC:
            print(f"⏳ [EDIT IGNORED] @{source_node} #{primary_msg_id} was dispatched {int(elapsed/60)}m ago (>60m).")
            return

        msg_text = getattr(msg, "raw_text", None) or getattr(msg, "text", None) or ""
        if (not msg_text or not msg_text.strip()) and msg.id != primary_msg_id:
            msg_text = alert_info.get("raw_text", "")
        if not msg_text or not msg_text.strip():
            return

        msgs_for_media = self._resolve_msgs_for_post(msg, source_node, album_msgs)
        media_icons = self.detect_media_icons(msgs_for_media)

        eval_res = self.engine.process_message(msg_text, source_node)
        if eval_res and eval_res.get("status") == "MATCHED":
            display_text = eval_res.get("output_text") or msg_text
            meta_by_ch = {m["target_channel"]: m for m in eval_res.get("dispatches_meta", [])}
        else:
            if source_node == "Ali_bk_8" and self.engine.has_persian_text(msg_text):
                display_text = self.engine.extract_persian_text(msg_text)
            else:
                display_text = alert_info.get("output_text") or msg_text
            meta_by_ch = {}

        dispatches = self._get_dispatches(alert_info)
        any_edited = False
        for d in dispatches:
            target_ch = d["target_channel"]
            bot_msg_id = d["bot_msg_id"]
            orig_icon = d.get("status_icon", alert_info.get("status_icon", "🔕"))

            if target_ch in meta_by_ch:
                if meta_by_ch[target_ch].get("severity") == "URGENT":
                    status_icon = "🚨" if orig_icon == "🚨" else "🚨🔕"
                else:
                    status_icon = "🔕"
            elif eval_res and eval_res.get("status") == "MATCHED":
                if eval_res.get("severity") == "URGENT":
                    status_icon = "🚨" if orig_icon == "🚨" else "🚨🔕"
                else:
                    status_icon = "🔕"
            else:
                status_icon = orig_icon

            formatted_text = self.sanitize_and_format_alert(display_text, source_node, primary_msg_id, status_icon, media_icons=media_icons)
            if not formatted_text or d.get("last_formatted_text") == formatted_text:
                continue

            print(f"🔄 [EDIT SYNC] Syncing edited post to {target_ch} (bot_msg_id: {bot_msg_id}, source: @{source_node}/#{primary_msg_id}, icons: {media_icons})")
            if self.edit_bot_alert(target_ch, bot_msg_id, formatted_text):
                d["status_icon"] = status_icon
                d["last_formatted_text"] = formatted_text
                self.alerts_edited += 1
                any_edited = True

        edit_date = getattr(msg, "edit_date", None)
        edit_ts = (edit_date.replace(tzinfo=timezone.utc).timestamp() if not edit_date.tzinfo else edit_date.timestamp()) if edit_date else now

        if any_edited:
            alert_info["last_edit_timestamp"] = max(float(alert_info.get("last_edit_timestamp", 0)), edit_ts)
            alert_info["media_icons"] = media_icons
            alert_info["raw_text"] = msg_text
            alert_info["output_text"] = display_text
            if dispatches:
                alert_info["status_icon"] = dispatches[0].get("status_icon", "🔕")
                alert_info["last_formatted_text"] = dispatches[0].get("last_formatted_text", "")
            self._save_channel_state()
            self.update_session_report()
        elif edit_date:
            alert_info["last_edit_timestamp"] = max(float(alert_info.get("last_edit_timestamp", 0)), edit_ts)

    async def handle_deleted_message(self, msg_id: int, source_node: str):
        """
        Handles deleted messages from VIP sources.
        Rule: If an alert was sent to Sentinel channels within the last 60 minutes,
        delete all corresponding alerts from destination channels immediately.
        """
        dedup_key = f"{source_node}_{msg_id}"
        if dedup_key in self.pending_translations:
            self.pending_translations.pop(dedup_key, None)
            self._save_channel_state()

        if dedup_key not in self.sent_alerts_map:
            return

        alert_info = self.sent_alerts_map[dedup_key]
        now = time.time()
        elapsed = now - float(alert_info.get("sent_timestamp", 0))

        if elapsed > config.EDIT_DELETE_WINDOW_SEC:
            print(f"⏳ [DELETE IGNORED] @{source_node} #{msg_id} was dispatched {int(elapsed/60)}m ago (>60m).")
            return

        dispatches = self._get_dispatches(alert_info)
        all_deleted = True
        for d in dispatches:
            target_ch = d["target_channel"]
            bot_msg_id = d["bot_msg_id"]
            print(f"🗑️ [DELETE SYNC] Removing alert in {target_ch} (bot_msg_id: {bot_msg_id}, deleted source: @{source_node}/#{msg_id})")
            if self.delete_bot_alert(target_ch, bot_msg_id):
                self.alerts_deleted += 1
            else:
                all_deleted = False

        if all_deleted or dispatches:
            self.sent_alerts_map.pop(dedup_key, None)
            self._save_channel_state()
            self.update_session_report()

    async def sync_active_tracked_alerts(self, source_entities: dict):
        """
        Verifies active tracked alerts AND pending @Ali_bk_8 translation posts (within 60m)
        against VIP source channels in batched RPCs.
        Catches missed edits, late Persian translations, media attachment changes, and deletions.
        """
        if (not self.sent_alerts_map and not self.pending_translations) or not self.client.is_connected():
            return

        now = time.time()
        alerts_by_node = {}
        pending_by_node = {}

        for alert_info in list(self.sent_alerts_map.values()):
            sent_ts = float(alert_info.get("sent_timestamp", 0))
            if now - sent_ts > config.EDIT_DELETE_WINDOW_SEC:
                continue
            node = alert_info.get("source_node")
            msg_id = alert_info.get("msg_id")
            if node and msg_id and node in source_entities:
                alerts_by_node.setdefault(node, []).append((int(msg_id), alert_info))

        for p_key, p_info in list(self.pending_translations.items()):
            ts = float(p_info.get("timestamp", 0))
            if now - ts > config.EDIT_DELETE_WINDOW_SEC:
                continue
            node = p_info.get("source_node")
            msg_id = p_info.get("msg_id")
            if node and msg_id and node in source_entities:
                pending_by_node.setdefault(node, []).append((p_key, int(msg_id), p_info))

        active_nodes = set(alerts_by_node.keys()) | set(pending_by_node.keys())
        for node in active_nodes:
            entity = source_entities.get(node)
            if not entity:
                continue

            items = alerts_by_node.get(node, [])
            p_items = pending_by_node.get(node, [])

            ids_to_fetch = set()
            for msg_id, alert_info in items:
                if alert_info.get("grouped_id"):
                    ids_to_fetch.update(range(max(1, msg_id - 9), msg_id + 10))
                else:
                    ids_to_fetch.add(msg_id)
            for _, msg_id, p_info in p_items:
                if p_info.get("grouped_id"):
                    ids_to_fetch.update(range(max(1, msg_id - 9), msg_id + 10))
                else:
                    ids_to_fetch.add(msg_id)

            if not ids_to_fetch:
                continue

            try:
                fetched_msgs = await self.client.get_messages(entity, ids=sorted(ids_to_fetch))
                if fetched_msgs is None:
                    fetched_msgs = []
                elif not isinstance(fetched_msgs, list):
                    fetched_msgs = [fetched_msgs]

                existing_by_id = {m.id: m for m in fetched_msgs if m is not None}

                # Check pending translation items first
                for p_key, msg_id, p_info in p_items:
                    fetched_msg = existing_by_id.get(msg_id)
                    if fetched_msg is None:
                        self.pending_translations.pop(p_key, None)
                        self._save_channel_state()
                        continue
                    grouped_id = p_info.get("grouped_id")
                    album_msgs = (
                        [m for m in fetched_msgs if m is not None and getattr(m, "grouped_id", None) == grouped_id] or [fetched_msg]
                        if grouped_id else [fetched_msg]
                    )
                    if grouped_id:
                        self.album_cache[f"{node}_{grouped_id}"] = {m.id: m for m in album_msgs}
                    await self.handle_edited_message(fetched_msg, node, album_msgs=album_msgs)

                # Check already dispatched tracked alerts
                for msg_id, alert_info in items:
                    fetched_msg = existing_by_id.get(msg_id)
                    if fetched_msg is None:
                        print(f"🗑️ [SYNC] Source post @{node}/#{msg_id} deleted -> syncing removal")
                        await self.handle_deleted_message(msg_id, node)
                        continue

                    grouped_id = alert_info.get("grouped_id")
                    if grouped_id:
                        album_msgs = [
                            m for m in fetched_msgs
                            if m is not None and getattr(m, "grouped_id", None) == grouped_id
                        ]
                        if not album_msgs:
                            album_msgs = [fetched_msg]
                        self.album_cache[f"{node}_{grouped_id}"] = {m.id: m for m in album_msgs}
                    else:
                        album_msgs = [fetched_msg]

                    current_icons = self.detect_media_icons(album_msgs)
                    stored_icons = alert_info.get("media_icons")
                    icons_changed = (stored_icons is not None and current_icons != stored_icons)

                    latest_edit_ts = 0.0
                    for m in album_msgs:
                        ed = getattr(m, "edit_date", None)
                        if ed:
                            ts = ed.replace(tzinfo=timezone.utc).timestamp() if not ed.tzinfo else ed.timestamp()
                            if ts > latest_edit_ts:
                                latest_edit_ts = ts

                    last_seen_edit = float(alert_info.get("last_edit_timestamp", alert_info["sent_timestamp"]))
                    edited_since_seen = (latest_edit_ts > last_seen_edit)

                    if icons_changed or edited_since_seen:
                        print(f"🔄 [SYNC] Detected update on @{node}/#{msg_id} (edit={edited_since_seen}, icons_changed={icons_changed} -> {current_icons})")
                        await self.handle_edited_message(fetched_msg, node, album_msgs=album_msgs)
            except FloodWaitError as e:
                print(f"⏳ FloodWait during sync on @{node}: wait {e.seconds}s")
            except Exception as e:
                print(f"⚠️ Edit/delete sync note on @{node}: {e}")

    @staticmethod
    def _resolve_event_node(event, id_to_node_map: dict) -> Optional[str]:
        """Resolves canonical VIP source node name from a Telethon event."""
        chat = getattr(event, "chat", None)
        sender_id = getattr(event, "chat_id", None) or (getattr(chat, "id", None) if chat else None)
        if not sender_id:
            return None
        clean_id = int(str(sender_id).removeprefix("-100"))
        return id_to_node_map.get(clean_id) or id_to_node_map.get(sender_id)

    async def handle_new_message(self, event_or_msg, source_node: str, album_msgs: Optional[list] = None):
        """Processes an incoming message through the 3-step engine and dispatches if matched."""
        # Use raw_text to bypass Telethon's markdown asterisks, or text as fallback
        msg_text = getattr(event_or_msg, "raw_text", None) or event_or_msg.text or ""
        msg_id = event_or_msg.id
        msg_date = event_or_msg.date
        grouped_id = getattr(event_or_msg, "grouped_id", None)

        self.messages_processed += 1
        self.last_msg_text = msg_text.replace("\n", " ").strip()
        self.last_msg_time = msg_date.strftime("%Y-%m-%d %H:%M:%S UTC") if msg_date else "now"

        # Unique deduplication key & album guard
        dedup_key = f"{source_node}_{msg_id}"
        if dedup_key in self.posted_registry:
            return
        if grouped_id and self._find_alert_by_group(source_node, grouped_id):
            return

        # 60-Minute Stale Message Guard: Only process news from the last 60 minutes
        if msg_date:
            msg_ts = msg_date.replace(tzinfo=timezone.utc).timestamp() if not msg_date.tzinfo else msg_date.timestamp()
            age_sec = time.time() - msg_ts
            if age_sec > config.MAX_MESSAGE_AGE_SEC:
                print(f"⏳ [STALE DROP] @{source_node}/#{msg_id} is {int(age_sec / 60)}m old (>60m limit) - alert skipped.")
                return

        # 3-Step Deterministic Engine Evaluation
        eval_res = self.engine.process_message(msg_text, source_node)
        if not eval_res:
            # Dropped by Sieve (Noise / Foreign @netblocks / Irrelevant)
            return

        if eval_res.get("status") == "PENDING_TRANSLATION":
            self.pending_translations[dedup_key] = {
                "source_node": source_node,
                "msg_id": msg_id,
                "timestamp": time.time(),
                "grouped_id": grouped_id,
            }
            self._save_channel_state()
            print(f"⏳ [WAIT TRANSLATION] @{source_node}/#{msg_id} lacks Persian text - awaiting edit within 60m.")
            return

        self.pending_translations.pop(dedup_key, None)
        display_text = eval_res.get("output_text") or msg_text

        # Detect source content media icons across single message or album siblings
        msgs_for_media = self._resolve_msgs_for_post(event_or_msg, source_node, album_msgs)
        media_icons = self.detect_media_icons(msgs_for_media)

        dispatches_meta = eval_res.get("dispatches_meta") or [{
            "target_channel": eval_res["target_channel"],
            "severity": eval_res.get("severity", "SILENT"),
        }]

        sent_dispatches = []
        for meta in dispatches_meta:
            target_ch = meta["target_channel"]
            is_engine_urgent = (meta.get("severity") == "URGENT")

            # Anti-Spam 15-Minute Rule per target channel: resolve sound and icon (🚨, 🚨🔕, or 🔕)
            disable_notif, status_icon = self.resolve_alert_mode_and_icon(target_ch, is_engine_urgent)

            # Format Final Alert using the exact user-specified typographic template
            alert_body = self.sanitize_and_format_alert(display_text, source_node, msg_id, status_icon, media_icons=media_icons)
            if not alert_body:
                continue

            mode_str = f"[{status_icon}] ({media_icons}, notif={'OFF' if disable_notif else 'ON'})"
            print(f"🚀 [DISPATCH] {mode_str} -> {target_ch} (Source: @{source_node} | Msg: {msg_id})")

            bot_msg_id = self.send_bot_alert(target_ch, alert_body, disable_notif)
            if bot_msg_id:
                self.alerts_dispatched += 1
                sent_dispatches.append({
                    "target_channel": target_ch,
                    "bot_msg_id": bot_msg_id,
                    "status_icon": status_icon,
                    "last_formatted_text": alert_body,
                })

        if sent_dispatches:
            self.posted_registry.add(dedup_key)
            self._save_registry()
            primary = sent_dispatches[0]
            # Register in sent_alerts_map for 60-minute edit & delete synchronization across all targets
            self.sent_alerts_map[dedup_key] = {
                "target_channel": primary["target_channel"],
                "bot_msg_id": primary["bot_msg_id"],
                "status_icon": primary["status_icon"],
                "last_formatted_text": primary["last_formatted_text"],
                "dispatches": sent_dispatches,
                "sent_timestamp": time.time(),
                "source_node": source_node,
                "msg_id": msg_id,
                "media_icons": media_icons,
                "grouped_id": grouped_id,
                "raw_text": msg_text,
                "output_text": display_text,
            }
            self._save_channel_state()
            self.update_session_report()

    async def run(self):
        print("==================================================")
        print(f"🛰️ TREND SENTINEL V{config.VERSION} (ZERO-RACE & DUAL-SYNC ENGINE)")
        print(f"Target Nodes: {config.VIP_CHANNELS}")
        print(f"Destinations: {list(config.CHANNELS.values())}")
        print(f"Max Runtime: {config.MAX_RUNTIME_SEC}s")
        print("==================================================")

        # 1. Connect MTProto Client
        await self.client.connect()
        if not await self.client.is_user_authorized():
            print("❌ Error: Telegram session string is not authorized.")
            await self.client.disconnect()
            sys.exit(1)

        me = await self.client.get_me()
        print(f"🔐 Authenticated as: {me.first_name} (ID: {me.id})")

        # 2. Resolve VIP Entities
        source_entities = {}
        for node in config.VIP_CHANNELS:
            peer = self.get_input_peer(node)
            if peer:
                source_entities[node] = peer
                print(f"🎯 Resolved @{node} via cached InputPeerChannel")
            else:
                try:
                    entity = await self.client.get_entity(node)
                    source_entities[node] = entity
                    print(f"✅ Resolved @{node} directly")
                except Exception as e:
                    print(f"⚠️ Could not resolve @{node}: {e}")

        # 3. Channel Cursor Sync & Catch-up
        print("📥 Synchronizing channel cursors and scanning for new messages...")
        for node, entity in source_entities.items():
            last_id = self.get_last_read_id(node)
            if last_id == 0:
                # First run: set cursor to latest post to avoid replaying history
                try:
                    async for msg in self.client.iter_messages(entity, limit=1):
                        self.update_last_read_id(node, msg.id, msg.date)
                        print(f"📌 Initialized cursor for @{node} at #{msg.id}")
                except Exception as e:
                    print(f"⚠️ Cursor init note on @{node}: {e}")
            else:
                # Catch-up: iterate only messages with id > last_id in forward chronological order!
                new_count = 0
                try:
                    caught_msgs = [msg async for msg in self.client.iter_messages(entity, min_id=last_id, reverse=True)]
                    for msg in caught_msgs:
                        self.update_last_read_id(node, msg.id, msg.date)
                        self._record_album_message(node, msg)
                    for msg in caught_msgs:
                        if msg.text:
                            await self.handle_new_message(msg, node)
                        elif getattr(msg, "grouped_id", None):
                            await self._sync_grouped_media_if_dispatched(node, msg.grouped_id)
                        new_count += 1
                    if new_count:
                        print(f"📥 Caught up {new_count} new messages from @{node} (cursor advanced to #{self.get_last_read_id(node)})")
                except Exception as e:
                    print(f"⚠️ Catch-up note on @{node}: {e}")

        # Sync recent edits, pending @Ali_bk_8 translations & deletions on active tracked alerts (within 60m)
        if self.sent_alerts_map or self.pending_translations:
            print("🔄 Checking active tracked alerts for edits, translations & deletions during handover...")
            await self.sync_active_tracked_alerts(source_entities)

        self.update_session_report()

        # 4. Event Listener for Live Streaming
        print("👁️ Listening for live events across VIP channels...")

        entity_list = list(source_entities.values())
        id_to_node_map = {}
        for node, ent in source_entities.items():
            ent_id = getattr(ent, "channel_id", getattr(ent, "id", None))
            if ent_id:
                id_to_node_map[ent_id] = node

        @self.client.on(events.NewMessage(chats=entity_list))
        async def event_handler(event):
            node_name = self._resolve_event_node(event, id_to_node_map)
            if not node_name:
                return

            msg_id = event.message.id
            last_id = self.get_last_read_id(node_name)
            if msg_id <= last_id:
                # Already read/processed
                return

            self.update_last_read_id(node_name, msg_id, event.message.date)
            grouped_id = getattr(event.message, "grouped_id", None)
            if grouped_id:
                self._record_album_message(node_name, event.message)
                if await self._sync_grouped_media_if_dispatched(node_name, grouped_id):
                    return
                if not event.message.text:
                    return
                await asyncio.sleep(config.ALBUM_DEBOUNCE_SEC)
                if await self._sync_grouped_media_if_dispatched(node_name, grouped_id):
                    return
                await self.handle_new_message(event.message, node_name)
            else:
                await self.handle_new_message(event.message, node_name)

        @self.client.on(events.MessageEdited(chats=entity_list))
        async def edit_event_handler(event):
            node_name = self._resolve_event_node(event, id_to_node_map)
            if not node_name:
                return
            await self.handle_edited_message(event.message, node_name)

        @self.client.on(events.MessageDeleted(chats=entity_list))
        async def delete_event_handler(event):
            node_name = self._resolve_event_node(event, id_to_node_map)
            if not node_name:
                return
            for deleted_id in (event.deleted_ids or []):
                await self.handle_deleted_message(deleted_id, node_name)

        # 5. Controlled Execution Loop with Graceful Handoff & Signal Trapping
        def sig_handler(signum, frame):
            print(f"\n⚠️ Received signal {signum}, initiating graceful shutdown...")
            raise KeyboardInterrupt

        try:
            signal.signal(signal.SIGTERM, sig_handler)
            signal.signal(signal.SIGINT, sig_handler)
        except Exception:
            pass

        last_sync_time = time.time()
        try:
            while time.time() - self.start_time < config.MAX_RUNTIME_SEC:
                await asyncio.sleep(config.POLL_INTERVAL_SEC)
                if (self.sent_alerts_map or self.pending_translations) and (time.time() - last_sync_time >= config.SYNC_INTERVAL_SEC):
                    last_sync_time = time.time()
                    await self.sync_active_tracked_alerts(source_entities)
            print("\n⏱️ Max runtime reached. Gracefully rotating session for next runner...")
        except (asyncio.CancelledError, KeyboardInterrupt):
            print("\n⚠️ Received interrupt/cancellation signal...")
        finally:
            self._save_channel_state()
            self._save_registry()
            self.update_session_report()
            if self.client.is_connected():
                await self.client.disconnect()
            print("🔒 Clean shutdown completed.")

if __name__ == "__main__":
    sentinel = TrendSentinelV4()
    asyncio.run(sentinel.run())
