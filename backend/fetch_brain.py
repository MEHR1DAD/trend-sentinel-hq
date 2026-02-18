import requests
import json
import os
import time
from collections import defaultdict
from datetime import datetime

# URLs of our Distributed Workers
SOURCES = [
    "https://mehr1dad.github.io/finance-optimizer-worker/market_data.json", # Vahid
    "https://mehr1dad.github.io/tech-market-monitor/market_data_tech.json", # Tech
    "https://mehr1dad.github.io/crypto-market-watch/market_data_crypto.json", # Crypto
    "https://mehr1dad.github.io/python-utils-collection/data_shard_h.json", # History
    "https://mehr1dad.github.io/python-utils-collection/data_shard_global.json" # Global
]

BASELINE_FILE = 'backend/trend_baselines.json'

def fetch_all_data():
    all_items = []
    print("🧠 Sentinel Brain: Fetching distributed knowledge...")
    
    for url in SOURCES:
        try:
            print(f"  ⬇️  Downloading {url.split('/')[-1]}...")
            resp = requests.get(url, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                print(f"     ✅ Got {len(data)} items.")
                all_items.extend(data)
            else:
                print(f"     ⚠️ Failed: {resp.status_code}")
        except Exception as e:
            print(f"     ❌ Error: {e}")
            
    print(f"✅ Total Knowledge Base: {len(all_items)} items.")
    return all_items

def calculate_baselines(items):
    # Simplified Word Frequency Analysis
    # We want to know: "How often does 'War' appear per hour normally?"
    
    # 1. Tokenize (Simple)
    word_counts = defaultdict(int)
    total_hours_analyzed = 24 * 7 # Assuming data covers roughly a week? 
    # Actually, let's look at the data range.
    
    if not items:
        return {}

    # Sort to find range
    dates = []
    for i in items:
        if 'date' in i:
            try:
                # "2024-05-20T..."
                dt = datetime.fromisoformat(i['date'].replace('Z', '+00:00'))
                dates.append(dt)
            except: 
                pass
                
    if not dates: return {}
    
    min_date = min(dates)
    max_date = max(dates)
    duration = (max_date - min_date).total_seconds() / 3600
    if duration < 1: duration = 1
    
    print(f"📊 Analyzing data range: {duration:.1f} hours")
    
    ignore = {'در', 'به', 'از', 'که', 'می', 'این', 'است', 'را', 'با', 'های', 'برای', 'آن', 'با', 'او'}
    
    for item in items:
        text = item.get('text', '')
        # Simple normalization
        text = text.replace('ي', 'ی').replace('ك', 'ک')
        words = text.split()
        for w in words:
            if len(w) > 2 and w not in ignore:
                word_counts[w] += 1
                
    baselines = {}
    for w, count in word_counts.items():
        if count > 5: # Noise filter
            # Rate = occurrences per hour
            baselines[w] = round(count / duration, 4)
            
    return baselines

def main():
    data = fetch_all_data()
    baselines = calculate_baselines(data)
    
    output = {
        "updated_at": time.time(),
        "baselines": baselines
    }
    
    os.makedirs(os.path.dirname(BASELINE_FILE), exist_ok=True)
    with open(BASELINE_FILE, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
        
    print(f"💾 Saved {len(baselines)} baseline patterns to {BASELINE_FILE}")

if __name__ == "__main__":
    main()
