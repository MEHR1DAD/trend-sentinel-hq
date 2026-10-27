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

        # Load channel state & urgent alert cooldowns
        self.channel_state, self.urgent_alert_state = self._load_channel_state()

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
                    return data.get("channels", {}), data.get("last_urgent_alert", {})
            except Exception as e:
                print(f"⚠️ Error reading channel state: {e}")
        return {}, {}

    def _save_channel_state(self):
        try:
            with open(config.CHANNEL_STATE_PATH, "w", encoding="utf-8") as f:
                json.dump({
                    "channels": self.channel_state,
                    "last_urgent_alert": self.urgent_alert_state,
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

        report_content = (
            f"# Sentinel Session Report (v4 Clean Engine)\n\n"
            f"- **Uptime:** {uptime_min} minutes\n"
            f"- **Messages Processed:** {self.messages_processed}\n"
            f"- **Alerts Dispatched:** {self.alerts_dispatched}\n"
            f"- **Active VIP Nodes:** {len(config.VIP_CHANNELS)} channels\n"
            f"- **Last Message Text:** {self.last_msg_text[:120]}\n"
            f"- **Last Message Time:** {self.last_msg_time}\n\n"
            f"### 📌 حافظه وضعیت کانال‌ها (Channel Last Read Memory):\n"
            + "\n".join(cursor_lines) + "\n\n"
            f"### ⏱️ وضعیت بازه ۱۵ دقیقه‌ای هشدار فوری (Anti-Spam 15m Cooldown):\n"
            + "\n".join(urgent_status_lines) + "\n"
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
        Sanitizes raw text, removes asterisks & trailing promotional watermarks,
        and formats with clean inline status icon and hyperlinked source attribution.
        """
        lines = [l.strip() for l in raw_text.split('\n')]

        # 1. Strip raw asterisks from each line
        cleaned_lines = []
        for line in lines:
            c_line = re.sub(r'\*+', '', line).strip()
            cleaned_lines.append(c_line)

        # 2. Pop watermarks and promotional links from the bottom
        while cleaned_lines:
            last = cleaned_lines[-1]
            if not last:
                cleaned_lines.pop()
                continue
            is_watermark = (
                re.match(r'^#[a-zA-Z0-9_\u0600-\u06FF]+(\s+#[a-zA-Z0-9_\u0600-\u06FF]+)*$', last) or
                re.match(r'^https?://t\.me/[\w/]+$', last) or
                re.match(r'^@[\w_]+$', last) or
                't.me/' in last or
                'عضویت در کانال' in last or
                'کانال تلگرام' in last or
                'لینک کانال' in last
            )
            if is_watermark:
                cleaned_lines.pop()
            else:
                break

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

    def send_bot_alert(self, target_channel: str, html_text: str, disable_notif: bool) -> bool:
        """Dispatches formatted alert to destination Telegram channel via Bot API."""
        if not config.BOT_TOKEN:
            print("⚠️ TELEGRAM_BOT_TOKEN is not configured!")
            return False

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
                    return True
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
                        return True
            except Exception as e2:
                print(f"❌ Failed fallback alert to {target_channel}: {e2}")
        return False

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

        success = self.send_bot_alert(target_ch, alert_body, disable_notif)
        if success:
            self.alerts_dispatched += 1
            self.posted_registry.add(dedup_key)
            self._save_registry()
            self.update_session_report()

    async def run(self):
        print("==================================================")
        print("🛰️ TREND SENTINEL V4 (DETERMINISTIC BYPASS ENGINE)")
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
            sender_id = getattr(event.chat, "id", None)
            clean_id = int(str(sender_id).replace("-100", "")) if sender_id else 0
            node_name = id_to_node_map.get(clean_id) or id_to_node_map.get(sender_id) or "VIP"

            msg_id = event.message.id
            last_id = self.get_last_read_id(node_name)
            if msg_id <= last_id:
                # Already read/processed
                return

            self.update_last_read_id(node_name, msg_id, event.message.date)
            await self.handle_new_message(event.message, node_name)

        # 5. Controlled Execution Loop with Graceful Handoff
        while time.time() - self.start_time < config.MAX_RUNTIME_SEC:
            await asyncio.sleep(config.POLL_INTERVAL_SEC)

        print("\n⏱️ Max runtime reached. Gracefully rotating session for next runner...")
        self.update_session_report()
        await self.client.disconnect()
        print("🔒 Clean shutdown completed.")

if __name__ == "__main__":
    sentinel = TrendSentinelV4()
    asyncio.run(sentinel.run())
