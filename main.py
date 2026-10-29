#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
main.py - Trend Sentinel v4
Autonomous, Real-time Telegram Listener & Zero-AI Dispatcher.
Listens to 6 VIP news sources, filters through the 3-Step Deterministic Engine,
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
from datetime import datetime, timezone
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.tl.types import InputPeerChannel
from telethon.errors import FloodWaitError

import config
from engine import SentinelEngine

class TrendSentinelV4:
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

        # Load channel state, urgent alert cooldowns & 60m sent alerts map
        self.channel_state, self.urgent_alert_state, self.sent_alerts_map = self._load_channel_state()
        self.alerts_edited = 0

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
                        data.get("sent_alerts_map", {})
                    )
            except Exception as e:
                print(f"⚠️ Error reading channel state: {e}")
        return {}, {}, {}

    def _prune_sent_alerts(self):
        """Prunes alerts older than 60 minutes (3600s) to keep state lean and enforce 60m edit window."""
        now = time.time()
        pruned = {}
        for key, val in self.sent_alerts_map.items():
            ts = float(val.get("sent_timestamp", 0))
            if now - ts <= 3600:
                pruned[key] = val
        self.sent_alerts_map = pruned

    def _save_channel_state(self):
        try:
            self._prune_sent_alerts()
            with open(config.CHANNEL_STATE_PATH, "w", encoding="utf-8") as f:
                json.dump({
                    "channels": self.channel_state,
                    "last_urgent_alert": self.urgent_alert_state,
                    "sent_alerts_map": self.sent_alerts_map,
                    "updated_at": datetime.now(timezone.utc).isoformat()
                }, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"⚠️ Error saving channel state: {e}")

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
            edit_watcher_lines.append(f"- `{k}` -> `{v.get('target_channel')}` (bot msg #{v.get('bot_msg_id')}, {mins_ago}m ago)")

        if not edit_watcher_lines:
            edit_watcher_lines = ["- *(No active posts in 60m edit window)*"]

        report_content = (
            f"# Sentinel Session Report (v{config.VERSION} Zero-Race Engine)\n\n"
            f"- **Uptime:** {uptime_min} minutes\n"
            f"- **Messages Processed:** {self.messages_processed}\n"
            f"- **Alerts Dispatched:** {self.alerts_dispatched}\n"
            f"- **Alerts Edited:** {self.alerts_edited}\n"
            f"- **Active VIP Nodes:** {len(config.VIP_CHANNELS)} channels\n"
            f"- **Last Message Text:** {self.last_msg_text[:120]}\n"
            f"- **Last Message Time:** {self.last_msg_time}\n\n"
            f"### 📌 حافظه وضعیت کانال‌ها (Channel Last Read Memory):\n"
            + "\n".join(cursor_lines) + "\n\n"
            f"### ⏱️ وضعیت بازه ۱۵ دقیقه‌ای هشدار فوری (Anti-Spam 15m Cooldown):\n"
            + "\n".join(urgent_status_lines) + "\n\n"
            f"### 🔄 پیام‌های تحت پایش برای ویرایش (Active 60m Edit Watchers):\n"
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

    @staticmethod
    def sanitize_and_format_alert(raw_text: str, source_node: str, msg_id: int, status_icon: str) -> str:
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

        # 2. Strip raw asterisks and inline source watermarks (@VahidHeadline, @VahidOOnLine, etc.)
        inline_watermarks_pattern = re.compile(
            r'\s*@(VahidHeadline|VahidOOnLine|VahidOnline|withyashar|Ali_bk_8|iliaen|kianmeli1|netblocks)\b',
            re.IGNORECASE
        )
        cleaned_lines = []
        for line in lines:
            c_line = re.sub(r'\*+', '', line).strip()
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

        # 5. Clean single-line hyperlinked source footer
        clean_node = source_node.lstrip('@')
        source_url = f"https://t.me/{clean_node}/{msg_id}"
        footer = f'📡 منبع: <a href="{source_url}">@{clean_node}</a>'

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

    async def handle_edited_message(self, msg, source_node: str):
        """
        Handles edited messages from VIP sources.
        Rule: If an alert was sent to Sentinel channels within the last 60 minutes,
        synchronize the edit to the destination channel in silence.
        """
        msg_id = msg.id
        dedup_key = f"{source_node}_{msg_id}"

        if dedup_key not in self.sent_alerts_map:
            # Not an alert we dispatched, or dispatched >60m ago
            return

        alert_info = self.sent_alerts_map[dedup_key]
        now = time.time()
        elapsed = now - alert_info["sent_timestamp"]

        if elapsed > 3600:
            # Older than 60 minutes: ignore edit as per rule
            print(f"⏳ [EDIT IGNORED] @{source_node} #{msg_id} was dispatched {int(elapsed/60)}m ago (>60m).")
            return

        msg_text = getattr(msg, "raw_text", None) or msg.text or ""
        if not msg_text or not msg_text.strip():
            return

        # Determine icon (edits are always in silence)
        eval_res = self.engine.process_message(msg_text, source_node)
        orig_icon = alert_info.get("status_icon", "🔕")

        if eval_res:
            if eval_res.get("severity") == "URGENT":
                status_icon = "🚨" if orig_icon == "🚨" else "🚨🔕"
            else:
                status_icon = "🔕"
        else:
            status_icon = orig_icon

        formatted_text = self.sanitize_and_format_alert(msg_text, source_node, msg_id, status_icon)
        if not formatted_text:
            return

        target_ch = alert_info["target_channel"]
        bot_msg_id = alert_info["bot_msg_id"]

        print(f"🔄 [EDIT SYNC] Syncing edited post to {target_ch} (bot_msg_id: {bot_msg_id}, source: @{source_node}/#{msg_id})")
        success = self.edit_bot_alert(target_ch, bot_msg_id, formatted_text)
        if success:
            edit_date = getattr(msg, "edit_date", None)
            if edit_date:
                edit_ts = edit_date.replace(tzinfo=timezone.utc).timestamp() if not edit_date.tzinfo else edit_date.timestamp()
            else:
                edit_ts = now
            alert_info["last_edit_timestamp"] = edit_ts
            alert_info["status_icon"] = status_icon
            self.alerts_edited += 1
            self._save_channel_state()
            self.update_session_report()

    async def handle_new_message(self, event_or_msg, source_node: str):
        """Processes an incoming message through the 3-step engine and dispatches if matched."""
        # Use raw_text to bypass Telethon's markdown asterisks, or text as fallback
        msg_text = getattr(event_or_msg, "raw_text", None) or event_or_msg.text or ""
        msg_id = event_or_msg.id
        msg_date = event_or_msg.date

        self.messages_processed += 1
        self.last_msg_text = msg_text.replace("\n", " ").strip()
        self.last_msg_time = msg_date.strftime("%Y-%m-%d %H:%M:%S UTC") if msg_date else "now"

        # Unique deduplication key
        dedup_key = f"{source_node}_{msg_id}"
        if dedup_key in self.posted_registry:
            return

        # 3-Step Pure Engine Evaluation
        eval_res = self.engine.process_message(msg_text, source_node)
        if not eval_res:
            # Dropped by Sieve (Noise/Irrelevant)
            return

        target_ch = eval_res["target_channel"]
        is_engine_urgent = (eval_res.get("severity") == "URGENT")

        # Anti-Spam 15-Minute Rule: resolve sound and icon (🚨, 🚨🔕, or 🔕)
        disable_notif, status_icon = self.resolve_alert_mode_and_icon(target_ch, is_engine_urgent)

        # Format Final Alert using the exact user-specified typographic template
        alert_body = self.sanitize_and_format_alert(msg_text, source_node, msg_id, status_icon)
        if not alert_body:
            return

        mode_str = f"[{status_icon}] (notif={'OFF' if disable_notif else 'ON'})"
        print(f"🚀 [DISPATCH] {mode_str} -> {target_ch} (Source: @{source_node} | Msg: {msg_id})")

        bot_msg_id = self.send_bot_alert(target_ch, alert_body, disable_notif)
        if bot_msg_id:
            self.alerts_dispatched += 1
            self.posted_registry.add(dedup_key)
            self._save_registry()
            # Register in sent_alerts_map for 60-minute edit synchronization
            self.sent_alerts_map[dedup_key] = {
                "target_channel": target_ch,
                "bot_msg_id": bot_msg_id,
                "sent_timestamp": time.time(),
                "status_icon": status_icon,
                "source_node": source_node,
                "msg_id": msg_id
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
                    async for msg in self.client.iter_messages(entity, min_id=last_id, reverse=True):
                        self.update_last_read_id(node, msg.id, msg.date)
                        if msg.text:
                            await self.handle_new_message(msg, node)
                        new_count += 1
                    if new_count:
                        print(f"📥 Caught up {new_count} new messages from @{node} (cursor advanced to #{self.get_last_read_id(node)})")
                except Exception as e:
                    print(f"⚠️ Catch-up note on @{node}: {e}")

        # Catch-up recent edits on active tracked alerts (within 60m)
        if self.sent_alerts_map:
            print("🔄 Checking active tracked alerts for edits during handover...")
            for key, alert_info in list(self.sent_alerts_map.items()):
                now = time.time()
                if now - alert_info.get("sent_timestamp", 0) > 3600:
                    continue
                node = alert_info.get("source_node")
                msg_id = alert_info.get("msg_id")
                entity = source_entities.get(node)
                if not entity or not msg_id:
                    continue
                try:
                    fetched_msg = await self.client.get_messages(entity, ids=msg_id)
                    if fetched_msg and fetched_msg.edit_date:
                        edit_ts = fetched_msg.edit_date.replace(tzinfo=timezone.utc).timestamp() if not fetched_msg.edit_date.tzinfo else fetched_msg.edit_date.timestamp()
                        last_seen_edit = alert_info.get("last_edit_timestamp", alert_info["sent_timestamp"])
                        if edit_ts > last_seen_edit:
                            print(f"🔄 Detected edit during handover for @{node}/#{msg_id}")
                            await self.handle_edited_message(fetched_msg, node)
                except Exception as e:
                    print(f"⚠️ Edit check note on @{node}/#{msg_id}: {e}")

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
            sender_id = getattr(event.chat, "id", None) or getattr(event, "chat_id", None)
            clean_id = int(str(sender_id).replace("-100", "")) if sender_id else 0
            node_name = id_to_node_map.get(clean_id) or id_to_node_map.get(sender_id) or "VIP"

            msg_id = event.message.id
            last_id = self.get_last_read_id(node_name)
            if msg_id <= last_id:
                # Already read/processed
                return

            self.update_last_read_id(node_name, msg_id, event.message.date)
            await self.handle_new_message(event.message, node_name)

        @self.client.on(events.MessageEdited(chats=entity_list))
        async def edit_event_handler(event):
            sender_id = getattr(event.chat, "id", None) or getattr(event, "chat_id", None)
            clean_id = int(str(sender_id).replace("-100", "")) if sender_id else 0
            node_name = id_to_node_map.get(clean_id) or id_to_node_map.get(sender_id) or "VIP"
            await self.handle_edited_message(event.message, node_name)

        # 5. Controlled Execution Loop with Graceful Handoff & Signal Trapping
        def sig_handler(signum, frame):
            print(f"\n⚠️ Received signal {signum}, initiating graceful shutdown...")
            raise KeyboardInterrupt

        try:
            signal.signal(signal.SIGTERM, sig_handler)
            signal.signal(signal.SIGINT, sig_handler)
        except Exception:
            pass

        try:
            while time.time() - self.start_time < config.MAX_RUNTIME_SEC:
                await asyncio.sleep(config.POLL_INTERVAL_SEC)
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
