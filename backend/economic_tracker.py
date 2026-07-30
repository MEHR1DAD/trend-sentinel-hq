import json
import re
from datetime import datetime, timezone

import asyncio

ECONOMY_STATE_FILE = "backend/economy_state.json"
SUBSCRIBERS_FILE = "backend/subscribers.json"

class EconomicTracker:
    def __init__(self, bot, config):
        self.bot = bot
        self.economic_dollar = config.get("economic_dollar", [])
        self.economic_tether = config.get("economic_tether", [])
        self.economic_gold = config.get("economic_gold", [])
        self.lock = asyncio.Lock()
        
    def load_state(self):
        try:
            with open(ECONOMY_STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except:
            return {
                "dollar_fardaei": {"last_alerted_price": 0, "timestamp": ""},
                "dollar_naghdi": {"last_alerted_price": 0, "timestamp": ""},
                "tether": {"last_alerted_price": 0, "timestamp": ""},
                "gold_ounce": {"last_alerted_price": 0, "timestamp": ""},
                "gold_melted": {"last_alerted_price": 0, "timestamp": ""},
                "gold_coin": {"last_alerted_price": 0, "timestamp": ""},
                "gold_gram": {"last_alerted_price": 0, "timestamp": ""}
            }
            
    def save_state(self, state):
        with open(ECONOMY_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=4)
            
    def extract_prices(self, text):
        extracted = {}
        text = text.replace(',', '').replace('،', '')
        # Normalize Persian numerals
        persian_to_en = str.maketrans('۰۱۲۳۴۵۶۷۸۹', '0123456789')
        text = text.translate(persian_to_en)
        
        # 1. Melted Gold (مظنه / آبشده)
        match_melted = re.search(r'(?:مظنه|آبشده).*?(\d{4,8})', text)
        if match_melted:
            val = int(match_melted.group(1))
            if val < 100000: val *= 1000
            extracted['gold_melted'] = val

        # 2. Coin (سکه / امامی)
        match_coin = re.search(r'(?:سکه|امامی).*?(\d{4,8})', text)
        if match_coin:
            val = int(match_coin.group(1))
            if val < 100000: val *= 1000
            extracted['gold_coin'] = val

        # 3. Gram Gold (گرم)
        match_gram = re.search(r'(?:گرم|۱۸|18).*?(\d{4,8})', text)
        if match_gram:
            val = int(match_gram.group(1))
            if val < 100000: val *= 1000
            extracted['gold_gram'] = val

        # 4. Gold Ounce (انس طلا)
        match_ounce = re.search(r'(?:انس|اونس|ounce|gold).*?([234]\d{3}(?:\.\d+)?)', text.lower())
        if match_ounce:
            extracted["gold_ounce"] = float(match_ounce.group(1))

        # 5. Dollar & Tether
        numbers = re.findall(r'\b(?:[4-9]\d{4}|1[0-5]\d{4}|[4-9]\d\.\d{2,3}|1[0-5]\d\.\d{2,3})\b', text)
        if numbers:
            parsed_numbers = []
            for n in numbers:
                if '.' in n:
                    num = float(n)
                    if num < 200: num = int(num * 1000)
                    else: num = int(n.replace('.', ''))
                    parsed_numbers.append(num)
                else:
                    parsed_numbers.append(int(n))
                    
            if parsed_numbers:
                price = parsed_numbers[0]
                if "فردای" in text or "فردا" in text:
                    extracted["dollar_fardaei"] = price
                elif "نقد" in text or "تهران" in text or "امروز" in text:
                    extracted["dollar_naghdi"] = price
                if "تتر" in text or "usdt" in text.lower():
                    extracted["tether"] = price
                
                # Default fallback for dollar channels
                if not extracted and ("دلار" in text or "🇺🇸" in text):
                    extracted["dollar_fardaei"] = price
            
        return extracted
        
    async def process_economy_message(self, text, node_username, msg_date):
        # Ignore old messages (> 1 hour) for economy
        now_utc = datetime.now(timezone.utc)
        if (now_utc - msg_date).total_seconds() > 3600:
            return
            
        prices = self.extract_prices(text)
        if not prices:
            return
            
        async with self.lock:
            state = self.load_state()
            updates_made = False
            
            for asset, price in prices.items():
                last_price = state.get(asset, {}).get("last_alerted_price", 0)
                
                # Initial setup if 0
                if last_price == 0:
                    state[asset]["last_alerted_price"] = price
                    state[asset]["timestamp"] = now_utc.isoformat()
                    updates_made = True
                    continue
                    
                diff = price - last_price
                abs_diff = abs(diff)
            
                if abs_diff > 0:
                    is_urgent = False
                    unit = "تومان"
                    
                    # Check thresholds
                    threshold_met = False
                    
                    if asset == "gold_ounce":
                        if abs_diff >= 10:
                            threshold_met = True
                            asset_name = "انس جهانی طلا (Ounce Gold)"
                            unit = "دلار"
                    elif asset == "gold_melted":
                        if abs_diff >= 50000:
                            threshold_met = True
                            asset_name = "طلای آبشده (مظنه)"
                    elif asset == "gold_coin":
                        if abs_diff >= 100000:
                            threshold_met = True
                            asset_name = "سکه امامی"
                    elif asset == "gold_gram":
                        if abs_diff >= 1000000:
                            threshold_met = True
                            asset_name = "طلای ۱۸ عیار (گرم)"
                    else:
                        # Dollar & Tether
                        if abs_diff >= 500:
                            threshold_met = True
                            is_urgent = abs_diff >= 10000
                            asset_name = {
                                "dollar_fardaei": "دلار فردایی",
                                "dollar_naghdi": "دلار نقدی",
                                "tether": "تتر (USDT)"
                            }.get(asset, asset)
                    
                    if not threshold_met:
                        continue
                        
                    trend = "افزایش" if diff > 0 else "کاهش"
                    icon = "📈" if diff > 0 else "📉"
                    alert_icon = "🚨" if is_urgent else "🔕"
                    
                    # Format price differently if it's float
                    price_formatted = f"{price:,.1f}" if isinstance(price, float) else f"{price:,}"
                    diff_formatted = f"{abs_diff:,.1f}" if isinstance(abs_diff, float) else f"{abs_diff:,}"
                    
                    alert_text = (
                        f"{alert_icon} **SENTINEL ECONOMY:** {icon} {asset_name}\n\n"
                        f"جهش قیمت: **{price_formatted}** {unit}\n"
                        f"تغییر: {diff_formatted} {unit} {trend}\n"
                        f"منبع: [{node_username}](https://t.me/{node_username})\n\n"
                        f"#TrendSentinel #Economy"
                    )
                    
                    # Send alert
                    try:
                        with open(SUBSCRIBERS_FILE, "r") as f:
                            subs = set(json.load(f).get('subscribers', []))
                    except:
                        subs = set()
                        
                    import os
                    CHAT_ID = os.environ.get('CHAT_ID')
                    if CHAT_ID: subs.add(int(CHAT_ID))
                    
                    for sub in subs:
                        try:
                            await self.bot.send_message(sub, alert_text, link_preview=False, silent=not is_urgent)
                        except Exception as e:
                            print(f"Economy alert failed for {sub}: {e}")
                            
                    # Update state
                    state[asset]["last_alerted_price"] = price
                    state[asset]["timestamp"] = now_utc.isoformat()
                    updates_made = True
                    
            if updates_made:
                self.save_state(state)
