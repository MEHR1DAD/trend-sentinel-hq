import os
import re
import time
import json
import asyncio
from datetime import datetime, timezone
from backend.core.config import (
    CHANNEL_CANONICAL_TAGS,
    COUNTRY_THEATRES,
    FOREIGN_KEYWORDS,
    IRAN_KEYWORDS,
    BOT_TOKEN,
    get_tehran_time_fa,
    get_tehran_datetime_fa,
    format_update_status_line,
    to_fa_num,
    detect_msg_media_type,
    get_media_icon_from_type,
    classify_war_urgency,
    safe_extract_json
)

class RadarPipeline:
    """
    Subsystem for General Radar & Multi-Source Consensus:
    - 3-channel consensus detection across standard monitored channels
    - Cross-channel semantic merging & deduplication
    - Anomaly detection and routing to @DidebanJang, @DidehbanEghtesad, @DidebanEterazat
    - Message deletion tracking & automated cleanup
    """
    def __init__(self, sentinel, threads=None):
        self.sentinel = sentinel
        self.threads = threads or getattr(sentinel, 'threads', None)

    # ========================================================
    # --- THREAD & ANCHOR MANAGEMENT (DELEGATED TO THREADS) ---
    # ========================================================

    @staticmethod
    def normalize_text_for_anchors(text):
        from backend.core.thread_manager import ThreadAlertManager
        return ThreadAlertManager.normalize_text_for_anchors(text)

    def extract_message_anchors(self, text, title=""):
        if self.threads:
            return self.threads.extract_message_anchors(text, title)
        from backend.core.thread_manager import ThreadAlertManager
        return ThreadAlertManager(gemini=self.sentinel.gemini, db=self.sentinel.db).extract_message_anchors(text, title)

    async def evaluate_semantic_relation(self, existing_title, existing_summary, new_text, is_same_source=False, has_country_theatre=False):
        return await self.threads.evaluate_semantic_relation(
            existing_title=existing_title,
            existing_summary=existing_summary,
            new_text=new_text,
            is_same_source=is_same_source,
            has_country_theatre=has_country_theatre
        )

    async def check_and_merge_with_active_alert(self, text, node, msg_id, target_channel, candidate_title="", candidate_summary="", raw_msg=None, category='WAR', is_iran_related=False, candidate_sources=None):
        return await self.threads.check_and_merge_with_active_alert(
            text=text,
            node=node,
            msg_id=msg_id,
            target_channel=target_channel,
            candidate_title=candidate_title,
            candidate_summary=candidate_summary,
            raw_msg=raw_msg,
            category=category,
            is_iran_related=is_iran_related,
            candidate_sources=candidate_sources
        )

    def schedule_thread_debounce(self, thread_key):
        return self.threads.schedule_thread_debounce(thread_key)

    async def flush_thread_debounce(self, thread_key, delay=300):
        return await self.threads.flush_thread_debounce(thread_key, delay=delay)

    async def flush_all_pending_debounces(self):
        return await self.threads.flush_all_pending_debounces()

    async def synthesize_thread_alert_content(self, thread_text):
        return await self.threads.synthesize_thread_alert_content(thread_text)

    def build_alert_post_content(self, thread_data):
        return self.threads.build_alert_post_content(thread_data)

    async def find_root_message(self, node, raw_msg, max_depth=5):
        if not raw_msg:
            return None, []
            
        reply_to_id = getattr(raw_msg, 'reply_to_msg_id', None)
        if not reply_to_id and getattr(raw_msg, 'reply_to', None):
            reply_to_id = getattr(raw_msg.reply_to, 'reply_to_msg_id', None)
            
        if not reply_to_id:
            return raw_msg.id, [raw_msg.id]
            
        cache_key = f"{node}_{reply_to_id}"
        if cache_key in self.sentinel.reply_to_root_map:
            return self.sentinel.reply_to_root_map[cache_key], [raw_msg.id]

        thread_key = f"{node}_{reply_to_id}"
        if thread_key in self.sentinel.thread_alerts:
            return reply_to_id, [reply_to_id, raw_msg.id]

        curr_id = reply_to_id
        chain = [raw_msg.id]
        depth = 0
        while curr_id and depth < max_depth:
            chain.insert(0, curr_id)
            ck = f"{node}_{curr_id}"
            if ck in self.sentinel.reply_to_root_map:
                return self.sentinel.reply_to_root_map[ck], chain
            if ck in self.sentinel.thread_alerts:
                return curr_id, chain
                
            if not self.sentinel.client:
                break
                
            try:
                parent_msgs = await self.sentinel.client.get_messages(node, ids=[curr_id])
                if not parent_msgs or not parent_msgs[0]:
                    break
                parent = parent_msgs[0]
                next_reply_id = getattr(parent, 'reply_to_msg_id', None)
                if not next_reply_id:
                    return curr_id, chain
                curr_id = next_reply_id
                depth += 1
            except Exception as e:
                print(f"⚠️ Error tracing parent for {node}_{curr_id}: {e}")
                break
                
        return curr_id, chain

    # ==========================================
    # --- DELETION MANAGEMENT ---
    # ==========================================

    async def handle_message_deleted(self, node_username, deleted_ids):
        if not deleted_ids:
            return
            
        print(f"🗑️ Handling MessageDeleted event for node={node_username}: {deleted_ids}")
        for del_id in deleted_ids:
            del_int = int(del_id) if str(del_id).isdigit() else del_id
            del_str = str(del_id)
            
            for tk, thread_data in list(self.sentinel.thread_alerts.items()):
                if not isinstance(thread_data, dict):
                    continue
                sources = thread_data.get('sources', [])
                matching = [
                    s for s in sources 
                    if (s.get('msg_id') in (del_int, del_str) or str(s.get('msg_id')) == del_str)
                    and (not node_username or node_username in ('unknown', None) or s.get('node') == node_username)
                ]
                if matching:
                    target_ch = thread_data.get('channel', '@DidebanJang')
                    alert_mid = thread_data.get('alert_msg_id')
                    thread_data['sources'] = [s for s in sources if s not in matching]
                    if 'replies' in thread_data:
                        thread_data['replies'] = [r for r in thread_data['replies'] if r.get('msg_id') not in (del_int, del_str) and str(r.get('msg_id')) != del_str]
                    
                    if not thread_data['sources']:
                        self.sentinel.thread_alerts.pop(tk, None)
                        task = self.sentinel.debounce_tasks.pop(tk, None)
                        if task and not task.done():
                            task.cancel()
                        if alert_mid:
                            to_delete = [alert_mid]
                            if thread_data.get('last_notified_msg_id'):
                                to_delete.append(thread_data['last_notified_msg_id'])
                            try:
                                await self.sentinel.bot.delete_messages(target_ch, to_delete)
                                print(f"🗑️ Successfully deleted alert {alert_mid} in {target_ch} (all source posts deleted by admin).")
                            except Exception as e:
                                print(f"⚠️ Error deleting alert {alert_mid} in {target_ch}: {e}")
                    else:
                        updated_text = self.build_alert_post_content(thread_data)
                        try:
                            await self.sentinel.safe_edit_post(target_ch, alert_mid, updated_text)
                            print(f"✏️ Refreshed alert {alert_mid} in {target_ch} after removing deleted source {del_id}.")
                        except Exception as e:
                            print(f"⚠️ Error editing alert after source deletion: {e}")
                    self.sentinel.save_persisted_state()

            # VIP alerts
            for msg_key in list(self.sentinel.vip_alert_history.keys()):
                if msg_key.endswith(f"_{del_int}") or msg_key.endswith(f"_{del_str}"):
                    if node_username and not msg_key.startswith(f"{node_username}_"):
                        continue
                    info = self.sentinel.vip_alert_history.pop(msg_key, None)
                    if info and info.get('sent_msg_id'):
                        sent_mid = info['sent_msg_id']
                        target_ch = info.get('target_channel', '@DidebanJang')
                        try:
                            await self.sentinel.bot.delete_messages(target_ch, [sent_mid])
                            print(f"🗑️ Successfully deleted VIP alert {sent_mid} in {target_ch} (source {msg_key} deleted).")
                        except Exception as e:
                            print(f"⚠️ Error deleting VIP alert {sent_mid} in {target_ch}: {e}")
                    self.sentinel.save_persisted_state()

    async def verify_recent_messages_existence(self):
        if not self.sentinel.client or not self.sentinel.client.is_connected():
            return
            
        from telethon.tl.types import MessageEmpty
        now = time.time()
        
        # Check active monitored VIP threads
        recent_ids_by_node = {}
        for k, v in self.sentinel.thread_alerts.items():
            if (now - v.get('timestamp', 0)) <= 7200:
                nd = v.get('node')
                root_id = v.get('root_id')
                if nd and root_id:
                    recent_ids_by_node.setdefault(nd, set()).add(int(root_id) if str(root_id).isdigit() else root_id)
                for r in v.get('replies', []):
                    rep_id = r.get('msg_id')
                    if nd and rep_id:
                        recent_ids_by_node.setdefault(nd, set()).add(int(rep_id) if str(rep_id).isdigit() else rep_id)

        for nd, ids in recent_ids_by_node.items():
            try:
                target_peer = self.sentinel.get_node_input_peer(nd) or nd
                msgs = await self.sentinel.client.get_messages(target_peer, ids=check_ids)
                deleted_ids = []
                for cid, m in zip(check_ids, msgs):
                    if m is None or isinstance(m, MessageEmpty) or (not getattr(m, 'message', None) and not getattr(m, 'media', None)):
                        deleted_ids.append(cid)
                if deleted_ids:
                    print(f"🔍 Poller fallback detected deleted messages in {nd}: {deleted_ids}")
                    await self.handle_message_deleted(nd, deleted_ids)
            except Exception:
                pass

    # ==========================================
    # --- PATTERN MATCHING & CONSENSUS ---
    # ==========================================

    def is_old_news(self, text, node):
        if not text:
            return False
            
        canon = (node or '').lower()
        if canon in ['vahidonline', 'iliaen', 'withyashar', 'alibk3']:
            return False

        citizen_indicators = [
            "پیام دریافتی", "دریافتی:", "پیام‌های دریافتی", "پیامهای دریافتی", 
            "ارسالی:", "پیام:", "از پیام‌ها:", "از پیامها:"
        ]
        if any(ind in text for ind in citizen_indicators):
            return False
            
        # Only true historical archival / anniversary markers without breaking news context
        text_clean = text.lower()
        is_anniversary = any(w in text_clean for w in ["در چنین روزی در سال", "سالگرد درگذشت", "سالروز درگذشت", "خاطره‌بازی"])
        if is_anniversary and not any(k in text_clean for k in ["انفجار", "حمله", "موشک", "پهپاد", "بمباران", "آژیر", "فوری"]):
            return True
            
        return False

    def match_pattern(self, text, pattern):
        try:
            diacritics_regex = re.compile(r'[\u064B-\u065F\u0670]')
            clean_text = diacritics_regex.sub('', text).replace('\u200c', ' ')
            clean_pattern = diacritics_regex.sub('', pattern).replace('\u200c', ' ')
            
            if clean_pattern == 'حمله':
                if re.search(r'حمله\s+(قلبی|عصبی|تنفسی|پانیک)', clean_text):
                    non_medical = re.sub(r'حمله\s+(قلبی|عصبی|تنفسی|پانیک)', '', clean_text)
                    if not re.search(r'(?<![آ-یa-zA-Z0-9_])حمله(?![آ-یa-zA-Z0-9_])', non_medical):
                        return False

            esc_pattern = re.escape(clean_pattern)
            regex = r'(?<![آ-یa-zA-Z0-9_])' + esc_pattern + r'(?![آ-یa-zA-Z0-9_])'
            return bool(re.search(regex, clean_text))
        except:
            return pattern in text

    def calculate_jaccard(self, text1, text2):
        set1 = set(text1.split())
        set2 = set(text2.split())
        if not set1 or not set2:
            return 0.0
        return len(set1.intersection(set2)) / len(set1.union(set2))

    def purge_old_messages(self):
        now = time.time()
        # نگهداری ۱۵ دقیقه‌ای پیام‌ها (۹۰۰ ثانیه) و محدودسازی طول بافر به ۵۰۰ مورد برای امکان‌پذیری اجماع رسانه‌های مختلف
        while self.sentinel.recent_messages and (
            (now - self.sentinel.recent_messages[0]['timestamp']) > 900 or len(self.sentinel.recent_messages) > 500
        ):
            self.sentinel.recent_messages.popleft()

    def get_message_patterns(self, text, is_citizen=False):
        config_patterns = self.sentinel.config.get('patterns', {})
        incidents = config_patterns.get('incidents', [])
        economy = config_patterns.get('economy', [])
        locations = config_patterns.get('locations', [])
        status = config_patterns.get('status', [])
        incident_mappings = config_patterns.get('incident_mappings', {})
        generic_locations = config_patterns.get('generic_locations', [])
        
        found_incidents = [i for i in incidents if self.match_pattern(text, i)]
        found_economy = [e for e in economy if self.match_pattern(text, e)]
        found_locations = [l for l in locations if self.match_pattern(text, l)]
        found_status = [s for s in status if self.match_pattern(text, s)]
        
        resolved_incidents = set()
        for inc in found_incidents:
            mapped = False
            for canonical, synonyms in incident_mappings.items():
                if inc in synonyms or inc == canonical:
                    resolved_incidents.add(canonical)
                    mapped = True
                    break
            if not mapped:
                resolved_incidents.add(inc)
                
        resolved_status = set()
        for s in found_status:
            mapped = False
            for canonical, synonyms in incident_mappings.items():
                if s in synonyms or s == canonical:
                    resolved_status.add(canonical)
                    mapped = True
                    break
            if not mapped:
                resolved_status.add(s)

        specific_cities = [l for l in found_locations if l not in generic_locations]
        generic_locs = [l for l in found_locations if l in generic_locations]
        has_foreign = any(fk in text for fk in self.sentinel.foreign_keywords)
        
        if specific_cities:
            merged_cities = "، ".join(specific_cities)
            if generic_locs:
                merged_generics = " و ".join(generic_locs)
                final_loc_str = f"{merged_generics} {merged_cities}"
            else:
                final_loc_str = merged_cities
        elif generic_locs:
            final_loc_str = "، ".join(generic_locs) if is_citizen else ""
        else:
            final_loc_str = ""
            
        patterns = []
        if final_loc_str:
            if resolved_incidents:
                def inc_priority(inc):
                    sev = self.sentinel.incident_severities.get(inc, "IMPORTANT")
                    return 0 if sev == "URGENT" else 1
                sorted_incidents = sorted(list(resolved_incidents), key=inc_priority)
                
                if len(sorted_incidents) == 1:
                    inc_title = sorted_incidents[0]
                elif len(sorted_incidents) == 2:
                    inc_title = f"{sorted_incidents[0]} و {sorted_incidents[1]}"
                else:
                    inc_title = f"{sorted_incidents[0]}، {sorted_incidents[1]} و {sorted_incidents[2]}"
                    
                pat = f"{inc_title} در {final_loc_str}"
                if has_foreign or any(fk in final_loc_str for fk in self.sentinel.foreign_keywords):
                    pat += "||FOREIGN||"
                patterns.append(pat)
            elif is_citizen:
                pat = f"گزارش شهروندی در {final_loc_str}"
                if has_foreign or any(fk in final_loc_str for fk in self.sentinel.foreign_keywords):
                    pat += "||FOREIGN||"
                patterns.append(pat)
        elif is_citizen:
            if resolved_incidents:
                inc_title = " و ".join(list(resolved_incidents)[:2])
                pat = f"گزارش شهروندی: {inc_title}"
            else:
                pat = "گزارش فوری دریافتی شهروندان"
            
            if has_foreign:
                pat += "||FOREIGN||"
            patterns.append(pat)
            
        for s in resolved_status:
            pat = s
            if has_foreign:
                pat += "||FOREIGN||"
            patterns.append(pat)
            
        for e in found_economy:
            pat = e + "||ECONOMY||"
            patterns.append(pat)
            
        return list(set(patterns))

    # ==========================================
    # --- ANOMALY CONSENSUS & SEND ALERT ---
    # ==========================================

    async def detect_anomalies(self):
        channel_pools = {}
        
        for msg in self.sentinel.recent_messages:
            text = msg['text']
            is_citizen_report = msg.get('is_citizen_report', False)
            patterns_in_msg = self.get_message_patterns(text, is_citizen=is_citizen_report)
            
            for pat in patterns_in_msg:
                if pat not in channel_pools:
                    channel_pools[pat] = {}
                channel_pools[pat][msg['node']] = msg['link']
                
        now = time.time()
        while self.sentinel.recent_alert_sources and (now - self.sentinel.recent_alert_sources[0][0]) > 900:
            self.sentinel.recent_alert_sources.popleft()
            
        for pat, channels in channel_pools.items():
            is_foreign = "||FOREIGN||" in pat
            clean_pat = pat.replace("||FOREIGN||", "")
            
            distinct_channel_count = len(channels)
            if distinct_channel_count < 3:
                continue
            
            normal_rate = self.sentinel.baselines.get(clean_pat, 0.1)
            
            if clean_pat in self.sentinel.last_alert_time and (now - self.sentinel.last_alert_time[clean_pat]) < 1800:
                continue
                
            source_links = [f"- [{node}]({link})" for node, link in channels.items() if node and node != 'unknown' and re.match(r'^[A-Za-z0-9_]+$', str(node))]
            current_sources = set(channels.values())
            is_duplicate_story = False
            for prev_time, prev_sources in self.sentinel.recent_alert_sources:
                common_sources = current_sources.intersection(prev_sources)
                if len(common_sources) >= 2 or (len(current_sources) > 0 and len(common_sources) / len(current_sources) >= 0.5):
                    is_duplicate_story = True
                    break
                    
            if is_duplicate_story:
                continue
                
            self.sentinel.last_alert_time[clean_pat] = now
            self.sentinel.recent_alert_sources.append((now, current_sources))
            
            parsed_anomaly_sources = []
            for ch_node, ch_link in channels.items():
                if not ch_node or str(ch_node).lower() in ('radar', 'unknown', 'sentinel'):
                    continue
                m_sl = re.search(r'https://t\.me/([^/]+)/(\d+)', ch_link)
                if m_sl:
                    parsed_anomaly_sources.append({
                        'node': m_sl.group(1),
                        'msg_id': int(m_sl.group(2)),
                        'media_type': 'text',
                        'link': ch_link,
                        'timestamp': time.time()
                    })

            raw_texts = [msg['text'] for msg in self.sentinel.recent_messages if msg['node'] in channels and msg['link'] == channels[msg['node']]]
            combined_text = "\n---\n".join(raw_texts[:3])

            # -------------------------------------------------------------------
            # انتقال انحصاری و رسمی رویداد اجماع به خط لوله تحریریه چندعامله (سرباز ۳ و ۴)
            # رادار هیچ‌گونه حق ارسال مستقیم به تلگرام ندارد (Single Point of Dispatch)
            # -------------------------------------------------------------------
            if hasattr(self.sentinel, 'vip_pipeline') and self.sentinel.vip_pipeline:
                print(f"📡 [RADAR CONSENSUS] 3-Channel consensus reached for '{clean_pat}' ({len(channels)} sources). Delegating exclusively to Multi-Agent Editorial Pipeline...")
                await self.sentinel.vip_pipeline.handle_consensus_event(
                    combined_text=combined_text,
                    sources=parsed_anomaly_sources,
                    clean_pat=clean_pat
                )
            else:
                print(f"⚠️ [RADAR CONSENSUS] vip_pipeline not available to process consensus for '{clean_pat}'.")

    async def send_alert(self, pattern, count, normal_rate, context_msgs, is_silent=False, target_channel=None, alert_title=None, custom_icon=None, summary=None):
        """
        [DISPATCHER LOCKOUT]: این متد برای جلوگیری از هرگونه ارسال موازی و دور زدن سردبیر به طور قطعی مسدود است.
        تمام انتشارات کانال‌ها منحصراً و متمرکز از طریق VipMultiAgentEditorialPipeline انجام می‌شود.
        """
        print("🛑 [DISPATCHER LOCKOUT] Direct send_alert in Radar is permanently disabled. Parallel dispatch blocked.")
        return None, None, ""

    async def append_ai_summary(self, target_channel, message_id, original_text, raw_texts):
        """
        [DEPRECATED]: ویرایش تاخیری حذف شده و متون از ابتدا کامل، پخته و نهایی منتشر می‌شوند.
        """
        return None

    async def process_radar_consensus(self, text, node, msg_id, is_edit=False, msg_date=None, raw_msg=None):
        if msg_date:
            now_utc = datetime.now(timezone.utc)
            if (now_utc - msg_date).total_seconds() > 10800:
                return
                
        self.purge_old_messages()
        if not text:
            return
            
        self.sentinel.total_msgs_processed += 1
        self.sentinel.last_msg_text = text[:100] + "..." if len(text) > 100 else text
        self.sentinel.last_msg_time = time.strftime("%Y-%m-%d %H:%M:%S")
        
        text = text.replace('ي', 'ی').replace('ك', 'ک')
        
        is_citizen_report = any(ind in text for ind in [
            "پیام دریافتی", "دریافتی:", "پیام‌های دریافتی", "پیامهای دریافتی", 
            "ارسالی:", "پیام:", "از پیام‌ها:", "از پیامها:"
        ])
        
        patterns_in_msg = self.get_message_patterns(text, is_citizen=is_citizen_report)
        has_economy = any("||ECONOMY||" in p for p in patterns_in_msg)
        
        if (node or '').lower() not in ['vahidonline', 'iliaen', 'withyashar', 'alibk3'] and not has_economy and self.is_old_news(text, node):
            return
        
        is_same_node_duplicate = False
        for rm in self.sentinel.recent_messages:
            # فقط تکرار پیام در همان کانال واحد فیلتر می‌شود؛ تکرار توسط کانال‌های دیگر برای تشکیل اجماع ضروری است
            if rm.get('node') == node and self.calculate_jaccard(text, rm['text']) > 0.75:
                is_same_node_duplicate = True
                break
                
        if is_same_node_duplicate:
            return
        
        msg_obj = {
            'text': text,
            'node': node,
            'timestamp': time.time(),
            'link': f"https://t.me/{node}/{msg_id}",
            'is_citizen_report': is_citizen_report
        }
        self.sentinel.recent_messages.append(msg_obj)
        await self.detect_anomalies()


