import re
import time
import json
import asyncio
import urllib.request
import urllib.error
from backend.core.config import (
    GEMINI_API_KEYS,
    DEFAULT_GEMINI_MODEL,
    GEMINI_MODELS_PRIORITY
)

class GeminiClient:
    def __init__(self, api_keys=None):
        self.api_keys = api_keys if api_keys is not None else GEMINI_API_KEYS
        self.current_key_idx = 0
        self.current_gemini_model = DEFAULT_GEMINI_MODEL
        self.latest_flash_lite_model = DEFAULT_GEMINI_MODEL
        self.last_used_model = DEFAULT_GEMINI_MODEL
        self.model_usage_stats = {}
        self.gemini_lock = asyncio.Lock()
        self.last_gemini_call_time = 0.0

    def get_api_key(self):
        if not self.api_keys:
            return None
        return self.api_keys[self.current_key_idx % len(self.api_keys)]

    def rotate_api_key(self):
        if len(self.api_keys) > 1:
            self.current_key_idx = (self.current_key_idx + 1) % len(self.api_keys)
            print(f"🔄 Rotated to backup Gemini API Key #{self.current_key_idx + 1}/{len(self.api_keys)}")
            return self.get_api_key()
        return None

    @staticmethod
    def parse_model_version_score(model_name):
        clean = model_name.lower().replace("models/", "").strip()
        nums = re.findall(r'(\d+(?:\.\d+)?)', clean)
        score = float(nums[0]) if nums else 0.0
        has_date = 1 if re.search(r'\d{2}-\d{2}|\d{4}', clean) else 0
        is_stable = 0 if ('preview' in clean or 'exp' in clean) else 1
        return (score, is_stable, has_date, clean)

    async def fetch_latest_google_models(self):
        active_key = self.get_api_key()
        if not active_key:
            return []
            
        url = f"https://generativelanguage.googleapis.com/v1beta/models?key={active_key}"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        loop = asyncio.get_running_loop()
        
        def make_req():
            with urllib.request.urlopen(req, timeout=8) as response:
                return json.loads(response.read().decode('utf-8'))
                
        try:
            data = await loop.run_in_executor(None, make_req)
            models = data.get('models', [])
            
            valid = [
                m['name'].replace('models/', '').strip() 
                for m in models 
                if "generateContent" in m.get("supportedGenerationMethods", [])
                and not any(bad in m['name'].lower() for bad in ['tts', 'image', 'audio', 'embed', 'customtools', 'deep-research', 'realtime', 'live'])
            ]
            
            flash_lite = [m for m in valid if 'flash-lite' in m or 'flash_lite' in m]
            flash = [m for m in valid if ('flash' in m) and ('flash-lite' not in m and 'flash_lite' not in m)]
            pro = [m for m in valid if 'pro' in m and 'flash' not in m]
            
            flash_lite.sort(key=self.parse_model_version_score, reverse=True)
            flash.sort(key=self.parse_model_version_score, reverse=True)
            pro.sort(key=self.parse_model_version_score, reverse=True)
            
            if flash_lite:
                self.latest_flash_lite_model = flash_lite[0]
            elif flash:
                self.latest_flash_lite_model = flash[0]
                
            sorted_candidates = flash_lite + flash + pro
            print(f"📡 Dynamic Google Models Queried ({len(sorted_candidates)} candidates):")
            print(f"   ⚡️ Tier 1 (Flash-Lite): {flash_lite[:3]}")
            print(f"   ⚡️ Tier 2 (Flash): {flash[:3]}")
            print(f"   ⚡️ Tier 3 (Pro): {pro[:3]}")
            if sorted_candidates:
                self.current_gemini_model = sorted_candidates[0]
                return sorted_candidates
        except Exception as e:
            print(f"⚠️ Failed to query latest Google models: {e}")
            
        return GEMINI_MODELS_PRIORITY

    async def rotate_gemini_model_dynamic(self, failed_model=None):
        candidates = await self.fetch_latest_google_models()
        if not candidates:
            candidates = GEMINI_MODELS_PRIORITY
            
        curr = failed_model or self.current_gemini_model or candidates[0]
        
        flash_lite = [m for m in candidates if 'flash-lite' in m or 'flash_lite' in m]
        flash = [m for m in candidates if ('flash' in m) and ('flash-lite' not in m and 'flash_lite' not in m)]
        pro = [m for m in candidates if 'pro' in m and 'flash' not in m]
        stable_flash = [m for m in flash if any(v in m for v in ['2.5', '1.5'])]

        next_model = None
        if curr in flash_lite:
            if flash:
                next_model = flash[0]
            elif pro:
                next_model = pro[0]
        elif curr == (flash[0] if flash else None):
            if stable_flash and stable_flash[0] != curr:
                next_model = stable_flash[0]
            elif len(flash) > 1:
                next_model = flash[1]
            elif pro:
                next_model = pro[0]
        elif curr in flash:
            if pro:
                next_model = pro[0]
            elif stable_flash and stable_flash[0] != curr:
                next_model = stable_flash[0]
        elif curr in pro:
            if stable_flash:
                next_model = stable_flash[0]
            elif flash:
                next_model = flash[-1]
                
        if not next_model:
            if curr in candidates:
                idx = candidates.index(curr)
                for offset in range(1, len(candidates)):
                    cand = candidates[(idx + offset) % len(candidates)]
                    if cand != curr:
                        next_model = cand
                        break
        if not next_model:
            next_model = [c for c in candidates if c != curr][0] if len(candidates) > 1 else candidates[0]
            
        print(f"🔄 Dynamic Model Rotation (Cross-Tier Escalation): {curr} ➡️ {next_model}")
        self.current_gemini_model = next_model
        return next_model

    async def call_gemini_content(self, payload, timeout=14, max_retries=6, return_model=False):
        active_key = self.get_api_key()
        if not active_key:
            return (None, None) if return_model else None

        payload.setdefault("safetySettings", [
            {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"}
        ])
            
        model = self.latest_flash_lite_model or self.current_gemini_model or DEFAULT_GEMINI_MODEL
        
        for attempt in range(max_retries):
            if attempt == 2:
                model = "gemini-1.5-flash"
            elif attempt == 3:
                model = "gemini-1.5-flash-8b"
            elif attempt == 4:
                model = "gemini-2.5-flash"
            elif attempt == 5:
                model = "gemini-1.5-pro"
                
            cur_key = self.get_api_key()
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={cur_key}"
            try:
                req = urllib.request.Request(
                    url, 
                    data=json.dumps(payload).encode('utf-8'),
                    headers={'Content-Type': 'application/json'},
                    method='POST'
                )
                loop = asyncio.get_running_loop()
                def make_req():
                    with urllib.request.urlopen(req, timeout=timeout) as response:
                        return json.loads(response.read().decode('utf-8'))
                        
                async with self.gemini_lock:
                    gap = time.time() - self.last_gemini_call_time
                    if gap < 2.0:
                        await asyncio.sleep(2.0 - gap)
                    self.last_gemini_call_time = time.time()
                    data = await loop.run_in_executor(None, make_req)
                    
                candidates = data.get("candidates", [])
                raw_text = ""
                if candidates:
                    parts = candidates[0].get("content", {}).get("parts", [])
                    raw_text = "\n".join([p.get("text", "") for p in parts if p.get("text")]).strip()

                if raw_text:
                    self.current_gemini_model = model
                    self.last_used_model = model
                    self.model_usage_stats[model] = self.model_usage_stats.get(model, 0) + 1
                    if "flash-lite" in model or "flash_lite" in model:
                        self.latest_flash_lite_model = model
                    if return_model:
                        return raw_text, model
                    return raw_text
                
                finish_reason = candidates[0].get("finishReason", "UNKNOWN") if candidates else "NO_CANDIDATES"
                print(f"⚠️ Gemini empty/blocked response from model {model} (finishReason: {finish_reason}). Attempt {attempt+1}/{max_retries}")
                if attempt < max_retries - 1:
                    model = await self.rotate_gemini_model_dynamic(failed_model=model)
                else:
                    break
            except urllib.error.HTTPError as e:
                err_body = ""
                try:
                    err_body = e.read().decode('utf-8', errors='ignore')
                except Exception:
                    pass
                print(f"⚠️ Gemini API HTTP Error {e.code} ({e.reason}) on model {model} (Attempt {attempt+1}/{max_retries}): {err_body[:200]}")
                if e.code == 429 and "quota" in err_body.lower():
                    self.rotate_api_key()
                if attempt < max_retries - 1:
                    model = await self.rotate_gemini_model_dynamic(failed_model=model)
                    if e.code in (429, 503):
                        await asyncio.sleep(1.5)
                else:
                    break
            except Exception as e:
                print(f"⚠️ Gemini API error on model {model}: {e} (Attempt {attempt+1}/{max_retries})")
                if attempt < max_retries - 1:
                    model = await self.rotate_gemini_model_dynamic(failed_model=model)
                else:
                    break
                    
        return (None, None) if return_model else None
