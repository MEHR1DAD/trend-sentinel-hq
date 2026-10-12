import re
import time
import json
import asyncio
from datetime import datetime, timezone
from backend.core.config import get_tehran_time_fa

class MirrorPipeline:
    """
    Subsystem for Mirror channels:
    - VahidOnline -> @VahidOnlineAI
    - iliaen -> @iliaenAI
    Handles post summarization, media album deduplication, edits, and catch-up.
    """
    def __init__(self, sentinel):
        self.sentinel = sentinel
        self.grouped_posted = {
            'VahidOnline': set(),
            'iliaen': set()
        }
        self.msg_locks = {
            'VahidOnline': {},
            'iliaen': {}
        }
        self.posted_map = {
            'VahidOnline': {},
            'iliaen': {}
        }
        self.needs_refresh = {
            'VahidOnline': set(),
            'iliaen': set()
        }

    def generate_heuristic_summary(self, text, channel_name="کانال"):
        clean = (text or "").strip()
        lines = [l.strip() for l in clean.split('\n') if l.strip()]
        first_line = lines[0] if lines else clean
        title = first_line[:60] + "..." if len(first_line) > 60 else first_line
        title = re.sub(r'^[⚡️🚨⚠️🔴📌\s\*\-]+', '', title).strip()
        return {
            "title": title or "چکیده رویداد",
            "summary": clean[:300] + "..." if len(clean) > 300 else clean,
            "is_ai": False
        }

    async def summarize_channel_post(self, text, channel_name="وحیدآنلاین", bot_name="وحیدآنلاین هوشمند"):
        clean_text = (text or "").strip()
        if not self.sentinel.gemini.get_api_key():
            return self.generate_heuristic_summary(clean_text, channel_name)
            
        prompt = (
            f"تو سردبیر، تحلیلگر و همکار هوشمند تحریریه برای کانال تلگرام '{bot_name}' هستی.\n"
            f"پست زیر از کانال منبع '{channel_name}' منتشر شده است:\n\n"
            f"{clean_text}\n\n"
            "با اتکا به دانش ژورنالیستی و درک زبانی خودت، این پست را برای مخاطب بازنویسی کن:\n"
            "۱. title: یک تیتر رسا، جذاب، صریح و مستقل همراه با فعل کامل بنویس که اصل واقعه را شفاف بیان کند.\n"
            "۲. summary: چکیده‌ای شفاف و شیوا بنویس. اولویت با ایجاز در کنار بیان کامل ابعاد موضوع است. "
            "اگر خبر دارای ابهام است، توصیه یا پیام هشداری خاصی دارد، یا نیاز به توضیح تکمیلی و پیش‌زمینه دارد، با دانش خودت ابعاد رویداد را برای مخاطب شفاف کن.\n\n"
            "پاسخ را منحصراً در قالب این شیء JSON بنویس:\n"
            "{\n"
            '  "title": "تیتر رسا و کامل فارسی",\n'
            '  "summary": "شرح و چکیده هوشمندانه"\n'
            "}"
        )
        
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "maxOutputTokens": 450,
                "temperature": 0.2
            }
        }
        
        raw_out = await self.sentinel.gemini.call_gemini_content(payload, timeout=14, max_retries=5)
        if raw_out:
            match = re.search(r'\{.*\}', raw_out, re.DOTALL)
            if match:
                try:
                    res = json.loads(match.group(0))
                    title = str(res.get("title", "")).strip()
                    summary = str(res.get("summary", "")).strip()
                    if title and summary:
                        return {"title": title, "summary": summary, "is_ai": True}
                except Exception:
                    pass
                    
            lines = [l.strip() for l in raw_out.split('\n') if l.strip()]
            if len(lines) >= 2:
                return {"title": lines[0].replace('#', '').strip(), "summary": "\n".join(lines[1:]), "is_ai": True}
            return {"title": "تحلیل خبر", "summary": raw_out, "is_ai": True}
            
        return self.generate_heuristic_summary(clean_text, channel_name)

    async def handle_message(self, source_node, target_channel, text, msg_id, raw_msg=None, is_edit=False, is_upgrade=False):
        # 1. Update high-water mark in db manager
        if not is_edit:
            self.sentinel.db.advance_watermark(source_node, msg_id)

        # 2. Check persistent dedup guard
        if not is_edit and not is_upgrade and self.sentinel.db.is_source_posted(target_channel, source_node, msg_id):
            print(f"🛑 [DEDUP GUARD] {source_node} msg {msg_id} already posted to {target_channel}. Aborting send.")
            return

        # 3. Deduplication for Album / Media Groups (grouped_id)
        grouped_set = self.grouped_posted.setdefault(source_node, set())
        grouped_id = getattr(raw_msg, 'grouped_id', None) if raw_msg else None
        if grouped_id and grouped_id in grouped_set and not is_edit:
            return
            
        clean_text = (text or "").strip()
        if not clean_text or len(clean_text) < 5:
            return
            
        if grouped_id:
            grouped_set.add(grouped_id)
            
        # 4. Sequential processing per msg_id
        locks = self.msg_locks.setdefault(source_node, {})
        if msg_id not in locks:
            if len(locks) > 200:
                locks.clear()
            locks[msg_id] = asyncio.Lock()
            
        posted_dict = self.posted_map.setdefault(source_node, {})
        channel_fa_name = "وحیدآنلاین" if source_node == 'VahidOnline' else "ایلیا"
        bot_name = f"{channel_fa_name} هوشمند ({target_channel.replace('@', '')})"
        
        async with locks[msg_id]:
            # Safeguard: if edit of ancient post (>2h old) not tracked, ignore
            if is_edit and not is_upgrade and msg_id not in posted_dict:
                orig_date = getattr(raw_msg, 'date', None)
                if orig_date:
                    now_utc = datetime.now(timezone.utc)
                    if (now_utc - orig_date).total_seconds() > 7200:
                        print(f"⏩ Skipped editing ancient post {msg_id} in {source_node} (>2h old and not tracked)")
                        return False

            res = await self.summarize_channel_post(clean_text, channel_name=channel_fa_name, bot_name=bot_name)
            title = res.get('title', 'چکیده خبر').strip()
            summary = res.get('summary', clean_text).strip()
            is_ai = res.get('is_ai', False)
            
            if is_upgrade and not is_ai and (title == "خلاصه خبر" or summary == clean_text):
                print(f"⏩ Upgrade deferred for {source_node} msg {msg_id}: no AI summary available.")
                return False
                
            link = f"https://t.me/{source_node}/{msg_id}"
            
            # Detect media type from raw_msg
            media_icon = ""
            link_action = f"مشاهده پست اصلی در کانال {channel_fa_name}"
            if raw_msg:
                if getattr(raw_msg, 'video', None):
                    media_icon = " 📹"
                    link_action = f"مشاهده ویدیو در کانال {channel_fa_name}"
                elif getattr(raw_msg, 'photo', None):
                    media_icon = " 📸"
                    link_action = f"مشاهده تصویر در کانال {channel_fa_name}"
                elif getattr(raw_msg, 'voice', None) or getattr(raw_msg, 'audio', None):
                    media_icon = " 🎙"
                    link_action = f"شنیدن فایل صوتی در کانال {channel_fa_name}"
                    
            if is_edit and not is_upgrade:
                time_fa = get_tehran_time_fa()
                post_content = (
                    f"⚡️ **{title}**{media_icon}\n\n"
                    f"{summary}\n\n"
                    f"🔗 [{link_action}]({link})\n\n"
                    f"🔄 **به‌روزرسانی شده (ساعت {time_fa}) | ویرایش و تکمیل متن خبر**\n\n"
                    f"📡 {target_channel}"
                )
            else:
                post_content = (
                    f"⚡️ **{title}**{media_icon}\n\n"
                    f"{summary}\n\n"
                    f"🔗 [{link_action}]({link})\n\n"
                    f"📡 {target_channel}"
                )
                
            try:
                if msg_id in posted_dict:
                    posted_id = posted_dict[msg_id]
                    try:
                        await self.sentinel.safe_edit_post(target_channel, posted_id, post_content)
                        print(f"✏️ Edited post {posted_id} in {target_channel} for {source_node} msg {msg_id}.")
                        return is_ai
                    except Exception as edit_err:
                        if "not modified" in str(edit_err).lower():
                            print(f"ℹ️ Post {posted_id} in {target_channel} is already up to date.")
                            return is_ai
                        else:
                            raise edit_err
                else:
                    sent = None
                    has_media = raw_msg and getattr(raw_msg, 'media', None)
                    if has_media:
                        try:
                            sent = await self.sentinel.bot.send_message(
                                target_channel, 
                                post_content, 
                                file=raw_msg.media, 
                                link_preview=False
                            )
                        except Exception as media_err:
                            print(f"⚠️ Media send failed for {source_node} msg {msg_id}: {media_err}. Retrying as text...")
                            sent = await self.sentinel.bot.send_message(target_channel, post_content, link_preview=False)
                    else:
                        sent = await self.sentinel.bot.send_message(target_channel, post_content, link_preview=False)
                        
                    if sent:
                        posted_dict[msg_id] = sent.id
                        self.sentinel.db.record_posted_message(target_channel, source_node, msg_id, sent.id)
                        print(f"🚀 Sent mirror post {sent.id} to {target_channel} for {source_node} msg {msg_id}.")
                        return is_ai
            except Exception as e:
                print(f"❌ Error in mirror pipeline for {source_node} msg {msg_id}: {e}")
                return False

    async def preload_history(self, reader_client, source_node, target_channel):
        client_to_use = reader_client or self.sentinel.bot
        try:
            count = 0
            posted_dict = self.posted_map.setdefault(source_node, {})
            needs_ref = self.needs_refresh.setdefault(source_node, set())
            
            async for msg in client_to_use.iter_messages(target_channel, limit=100):
                txt = msg.text or msg.message or ""
                urls = self.sentinel.extract_message_urls(msg)
                for u in urls:
                    match = re.search(rf'{source_node}/(\d+)', u, re.IGNORECASE)
                    if match:
                        src_id = int(match.group(1))
                        if src_id not in posted_dict:
                            posted_dict[src_id] = msg.id
                            count += 1
                        self.sentinel.db.record_posted_message(target_channel, source_node, src_id, msg.id)
                        if "خلاصه خبر" in txt and "⚡️" in txt:
                            needs_ref.add((src_id, msg.id))
                        break
            if count > 0:
                print(f"🔄 Preloaded {count} historical posts for {target_channel} edit tracking.")
        except Exception as e:
            print(f"⚠️ Could not preload {target_channel} history: {e}")

    async def catchup_unposted(self, reader_client, source_node, target_channel):
        if not reader_client:
            return
        try:
            posted_dict = self.posted_map.setdefault(source_node, {})
            needs_ref = self.needs_refresh.setdefault(source_node, set())
            
            # Fast scan of last 10 messages in target channel for generic fallback posts needing AI upgrade
            try:
                async for m in reader_client.iter_messages(target_channel, limit=10):
                    txt = m.text or m.message or ""
                    if "خلاصه خبر" in txt and "⚡️" in txt:
                        urls = self.sentinel.extract_message_urls(m)
                        for u in urls:
                            match = re.search(rf'{source_node}/(\d+)', u, re.IGNORECASE)
                            if match:
                                src_id = int(match.group(1))
                                posted_dict[src_id] = m.id
                                self.sentinel.db.record_posted_message(target_channel, source_node, src_id, m.id)
                                needs_ref.add((src_id, m.id))
                                break
            except Exception as scan_err:
                print(f"⚠️ Quick scan {target_channel} failed: {scan_err}")

            watermark = self.sentinel.db.get_watermark(source_node)
            kwargs = {'limit': 30}
            if watermark > 0:
                kwargs['min_id'] = watermark
            recent_msgs = []
            async for msg in reader_client.iter_messages(source_node, **kwargs):
                recent_msgs.append(msg)
            recent_msgs.reverse()
            
            for msg in recent_msgs:
                clean_text = (msg.message or msg.text or "").strip()
                if clean_text and len(clean_text) >= 5:
                    if msg.id not in posted_dict and not self.sentinel.db.is_source_posted(target_channel, source_node, msg.id):
                        print(f"⚡ Catching up missed {source_node} post {msg.id} (watermark was {watermark})...")
                        await self.handle_message(source_node, target_channel, clean_text, msg.id, raw_msg=msg, is_edit=False)
                        await asyncio.sleep(2)
                    # Also feed into general process_message
                    await self.sentinel.process_message(clean_text, source_node, msg.id, msg_date=msg.date, raw_msg=msg, skip_mirror_channels=True)

            # Re-summarize any posts that previously fell back to generic "خلاصه خبر"
            for src_id, posted_id in list(needs_ref):
                try:
                    orig_msgs = await reader_client.get_messages(source_node, ids=[src_id])
                    if orig_msgs and orig_msgs[0]:
                        orig_m = orig_msgs[0]
                        clean_text = (orig_m.message or orig_m.text or "").strip()
                        if clean_text:
                            print(f"🔄 Upgrading fallback post {src_id} (msg {posted_id}) in {target_channel} with full AI summary...")
                            upgraded_ok = await self.handle_message(source_node, target_channel, clean_text, src_id, raw_msg=orig_m, is_edit=False, is_upgrade=True)
                            if upgraded_ok:
                                needs_ref.discard((src_id, posted_id))
                            await asyncio.sleep(4)
                except Exception as ref_err:
                    print(f"⚠️ Failed to upgrade fallback post {src_id}: {ref_err}")
        except Exception as e:
            print(f"⚠️ Catchup for {source_node} failed: {e}")
