import os
import json
import time
import requests
import re
from datetime import datetime

# --- Config ---
CONFIG_FILE = 'backend/sentinel_config.json'
STATE_FILE = 'backend/sentinel_state.json'
BASELINE_FILE = 'backend/trend_baselines.json'

BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID')

class Sentinel:
    def __init__(self):
        self.config = self.load_json(CONFIG_FILE)
        self.state = self.load_json(STATE_FILE)
        self.baselines = self.load_json(BASELINE_FILE).get('baselines', {})
        self.session = requests.Session()
        # Mimic a browser to avoid instant block
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        })
        
        # In-memory deduplication for this run
        self.seen_messages = set()

    def load_json(self, path):
        if os.path.exists(path):
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except: pass
        return {}

    def save_state(self):
        with open(STATE_FILE, 'w', encoding='utf-8') as f:
            json.dump(self.state, f, ensure_ascii=False, indent=2)

    def scrape_node(self, node):
        url = f"https://t.me/s/{node}"
        try:
            resp = self.session.get(url, timeout=5)
            if resp.status_code != 200: return []
            return self.parse_html(resp.text, node)
        except Exception as e:
            print(f"⚠️ {node} unreachable: {e}")
            return []

    def parse_html(self, html, node):
        # Extract messages using Regex (Fast & dependency-free)
        # Look for the data-post attribute which contains the ID
        # Then grab the text content
        messages = []
        
        # Pattern to find message wrappers
        # <div class="tgme_widget_message_wrap" ... data-post="Channel/123">
        # ... <div class="tgme_widget_message_text">TEXT</div>
        
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, 'html.parser')
        
        last_id = self.state.get('last_seen', {}).get(node, 0)
        max_id = last_id
        
        for wrap in soup.select('.tgme_widget_message_wrap'):
            msg_div = wrap.select_one('.tgme_widget_message')
            if not msg_div: continue
            
            post_info = msg_div.get('data-post') # "Channel/123"
            if not post_info: continue
            
            try:
                msg_id = int(post_info.split('/')[-1])
            except: continue
            
            if msg_id > max_id:
                max_id = msg_id
                
            # Only process NEW messages
            if msg_id <= last_id:
                continue
                
            text_div = wrap.select_one('.tgme_widget_message_text')
            if text_div:
                text = text_div.get_text(separator=' ').strip()
                # Normalize
                text = text.replace('ي', 'ی').replace('ك', 'ک')
                
                messages.append({
                    'id': msg_id,
                    'node': node,
                    'text': text,
                    'link': f"https://t.me/{post_info}"
                })
                
        # Update state cursor
        if 'last_seen' not in self.state: self.state['last_seen'] = {}
        self.state['last_seen'][node] = max_id
        
        return messages

    def detect_anomalies(self, messages):
        alerts = []
        now = time.time()
        
        # Flatten alerts config
        watch_patterns = []
        conf = self.config.get('patterns', {})
        if isinstance(conf, dict):
            for k, v in conf.items():
                watch_patterns.extend(v)
        else:
            watch_patterns = conf
            
        # Count occurrences in THIS BATCH
        counts = {}
        
        for msg in messages:
            text = msg['text']
            # Dedup check (simple)
            if text in self.seen_messages: continue
            self.seen_messages.add(text)
            
            for pat in watch_patterns:
                if pat in text:
                    counts[pat] = counts.get(pat, 0) + 1
                    
        # Analyze Spikes
        for pat, count in counts.items():
            # 1. Check Baseline
            normal_rate_hourly = self.baselines.get(pat, 0.1) # Default 0.1/hr
            
            # We are checking only the last few minutes, but let's assume
            # if we see > 2 occurrences in a 3-minute scrape, that's HIGH.
            # Normal rate 1/hr = 0.05/3min. 
            # 2 occurrences >>> 0.05.
            
            # Simple Heuristic:
            # If (Count >= 2) AND (Count > Normal_Rate_Hourly * 2): ALERT
            # Meaning: It's happening 2x faster than the HOURLY rate, right now.
            
            if count >= 2 and count > (normal_rate_hourly * 2):
                alerts.append({
                    'pattern': pat,
                    'count': count,
                    'baseline': normal_rate_hourly
                })
                
        return alerts

    def send_alert(self, alerts, messages):
        if not BOT_TOKEN or not CHAT_ID: return
        
        for alert in alerts:
            pat = alert['pattern']
            
            # Find example links
            links = []
            for m in messages:
                if pat in m['text']:
                    links.append(f"- [{m['node']}]({m['link']})")
                    if len(links) >= 3: break
                    
            msg_text = (
                f"🚨 **SENTINEL ALERT: {pat}**\n\n"
                f"🔥 Velocity: {alert['count']} hits (last 3m)\n"
                f"📊 Normal Baseline: {alert['baseline']:.2f}/hr\n\n"
                f"Sources:\n" + "\n".join(links) + "\n\n"
                f"#TrendSentinel"
            )
            
            try:
                requests.post(
                    f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                    data={'chat_id': CHAT_ID, 'text': msg_text, 'parse_mode': 'Markdown', 'disable_web_page_preview': True}
                )
            except Exception as e:
                print(f"Failed to send alert: {e}")

    def run(self):
        print("👁️ Sentinel Eye: Scanning...")
        nodes = self.config.get('nodes', [])
        all_new_msgs = []
        
        for node in nodes:
            msgs = self.scrape_node(node)
            all_new_msgs.extend(msgs)
            time.sleep(1) # Polite delay
            
        if all_new_msgs:
            print(f"   Picked up {len(all_new_msgs)} new signals.")
            alerts = self.detect_anomalies(all_new_msgs)
            if alerts:
                print(f"   🚨 {len(alerts)} ALERTS TRIGGERED!")
                self.send_alert(alerts, all_new_msgs)
            else:
                print("   ✅ Situation Normal.")
        else:
            print("   💤 No new activity.")
            
        self.save_state()

if __name__ == "__main__":
    s = Sentinel()
    s.run()
