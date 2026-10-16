import os
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
    """
    سرباز شماره صفر (Soldier 0):
    مدیر استراتژیک منابع و مدل‌های هوش مصنوعی گوگل.
    
    وظایف:
    ۱. استعلام دوره‌ای هر ۶ ساعت از گوگل (/v1beta/models) و چینش دو پشته نزولی:
       - FLASH_STACK: [جدیدترین فلش, دومین فلش جدید, سومین فلش جدید, ...]
       - LITE_STACK:  [جدیدترین لایت, دومین لایت جدید, سومین لایت جدید, ...]
    ۲. ذخیره و بارگذاری کش دیسک (Graceful Fallback) در صورت بروز اختلال شبکه.
    ۳. ماتریس تاب‌آوری دو بعدی (مدل × کلید):
       - برای هر مدل، ۴ شانس روی ۴ کلید با ۶۰ ثانیه کول‌داون.
       - در صورت عدم پاسخگویی، سقوط پله‌ای به مدل بعدی پشته.
    """
    def __init__(self, api_keys=None):
        self.api_keys = api_keys if api_keys is not None else GEMINI_API_KEYS
        self.current_key_idx = 0
        self.current_gemini_model = DEFAULT_GEMINI_MODEL
        self.latest_flash_lite_model = DEFAULT_GEMINI_MODEL
        self.last_used_model = DEFAULT_GEMINI_MODEL
        self.model_usage_stats = {}
        self.key_cooldown_until = {k: 0.0 for k in self.api_keys}
        self.key_locks = {k: asyncio.Lock() for k in self.api_keys}
        self.key_last_call_time = {k: 0.0 for k in self.api_keys}
        self.gemini_lock = asyncio.Lock()
        self.last_gemini_call_time = 0.0

        # پشته‌های پویا و کش دیسک برای سرباز شماره صفر
        self.cache_file = 'backend/model_cache.json'
        self.last_discovery_time = 0.0
        self.flash_stack = []
        self.lite_stack = []
        
        # بارگذاری اولیه از کش دیسک
        self.load_cached_stacks()
        print(f"🔑 [SOLDIER 0] Initialized Gemini Pool with {len(self.api_keys)} API keys. Lite Stack: {len(self.lite_stack)}, Flash Stack: {len(self.flash_stack)}")

    def get_api_key(self, allow_cooldown=False):
        if not self.api_keys:
            return None
        now = time.time()
        for i in range(len(self.api_keys)):
            idx = (self.current_key_idx + i) % len(self.api_keys)
            k = self.api_keys[idx]
            if self.key_cooldown_until.get(k, 0.0) <= now:
                self.current_key_idx = idx
                return k
        # If all keys are currently cooling down, return None unless explicitly requested
        if allow_cooldown:
            best_idx = min(range(len(self.api_keys)), key=lambda i: self.key_cooldown_until.get(self.api_keys[i], 0.0))
            self.current_key_idx = best_idx
            return self.api_keys[best_idx]
        return None

    def rotate_api_key(self, cooldown_seconds=60):
        if not self.api_keys:
            return None
        cur_key = self.api_keys[self.current_key_idx % len(self.api_keys)]
        if cooldown_seconds > 0:
            self.key_cooldown_until[cur_key] = time.time() + cooldown_seconds
        old_idx = self.current_key_idx
        self.current_key_idx = (self.current_key_idx + 1) % len(self.api_keys)
        new_key = self.get_api_key(allow_cooldown=False)
        print(f"🔄 [SOLDIER 0: KEY ROTATE] Switched Key: slot #{old_idx + 1} ➡️ slot #{self.current_key_idx + 1}/{len(self.api_keys)} (Cooldown {cooldown_seconds}s)")
        return new_key

    def _get_key_lock(self, key):
        if key not in self.key_locks:
            self.key_locks[key] = asyncio.Lock()
        return self.key_locks[key]

    def load_cached_stacks(self):
        if os.path.exists(self.cache_file):
            try:
                with open(self.cache_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self.flash_stack = data.get('flash_stack', [])
                    self.lite_stack = data.get('lite_stack', [])
                    self.last_discovery_time = data.get('last_discovery_time', 0.0)
                    if self.lite_stack:
                        self.latest_flash_lite_model = self.lite_stack[0]
                        self.current_gemini_model = self.lite_stack[0]
            except Exception as e:
                print(f"⚠️ [SOLDIER 0] Could not load model cache: {e}")

        # در صورت نبود کش دیسک، بارگذاری پیش‌فرض‌ها
        if not self.lite_stack:
            self.lite_stack = [m for m in GEMINI_MODELS_PRIORITY if 'lite' in m] or [DEFAULT_GEMINI_MODEL]
        if not self.flash_stack:
            self.flash_stack = [m for m in GEMINI_MODELS_PRIORITY if 'lite' not in m and 'pro' not in m] or ["gemini-2.5-flash", "gemini-2.0-flash"]

    def save_cached_stacks(self):
        try:
            data = {
                'flash_stack': self.flash_stack,
                'lite_stack': self.lite_stack,
                'last_discovery_time': self.last_discovery_time,
                'updated_at': time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())
            }
            with open(self.cache_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"⚠️ [SOLDIER 0] Could not save model cache: {e}")

    @staticmethod
    def parse_model_version_score(model_name):
        clean = model_name.lower().replace("models/", "").strip()
        nums = re.findall(r'(\d+(?:\.\d+)?)', clean)
        score = float(nums[0]) if nums else 0.0
        has_date = 1 if re.search(r'\d{2}-\d{2}|\d{4}', clean) else 0
        is_stable = 0 if ('preview' in clean or 'exp' in clean) else 1
        return (score, is_stable, has_date, clean)

    async def fetch_latest_google_models(self, force=False):
        """
        استعلام دوره‌ای هر ۶ ساعت (۲۱۶۰۰ ثانیه) یا به صورت اجباری.
        """
        now = time.time()
        six_hours = 6 * 3600
        if not force and (now - self.last_discovery_time) < six_hours and self.flash_stack and self.lite_stack:
            return self.lite_stack + self.flash_stack

        active_key = self.get_api_key()
        if not active_key:
            return self.lite_stack + self.flash_stack

        url = f"https://generativelanguage.googleapis.com/v1beta/models?key={active_key}"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        loop = asyncio.get_running_loop()

        def make_req():
            with urllib.request.urlopen(req, timeout=10) as response:
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

            # چینش از جدیدترین به قدیمی‌ترین
            flash_lite.sort(key=self.parse_model_version_score, reverse=True)
            flash.sort(key=self.parse_model_version_score, reverse=True)

            if flash_lite:
                self.lite_stack = flash_lite
                self.latest_flash_lite_model = flash_lite[0]
            if flash:
                self.flash_stack = flash

            self.last_discovery_time = now
            self.save_cached_stacks()

            print(f"🎖️ [SOLDIER 0: MODEL REGISTRY UPDATE] Discovery Completed (Next update in 6h):")
            print(f"   ⚡️ LITE_STACK ({len(self.lite_stack)} models): {self.lite_stack[:4]}")
            print(f"   ⚡️ FLASH_STACK ({len(self.flash_stack)} models): {self.flash_stack[:4]}")
            return self.lite_stack + self.flash_stack
        except Exception as e:
            print(f"⚠️ [SOLDIER 0] Discovery failed ({e}). Gracefully continuing with cached stacks.")
            return self.lite_stack + self.flash_stack

    async def call_soldier_content(self, payload, tier="lite", timeout=15, soldier_name="Soldier"):
        """
        موتور اجرایی اختصاصی سربازها:
        - انتخاب پشته (LITE_STACK یا FLASH_STACK)
        - امتحان مدل‌ها از جدیدترین به قدیمی‌ترین
        - امتحان ۴ کلید برای هر مدل قبل از سقوط به مدل بعدی پشته
        - ثبت دقیق زمان و مدل پاسخ‌دهنده
        """
        # اطمینان از سلامت استعلام
        await self.fetch_latest_google_models(force=False)

        stack = list(self.lite_stack if tier == "lite" else self.flash_stack)
        if not stack:
            stack = list(self.flash_stack if tier == "lite" else self.lite_stack) or [DEFAULT_GEMINI_MODEL]

        payload.setdefault("safetySettings", [
            {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"}
        ])

        num_keys = max(len(self.api_keys), 1)

        # پیمایش نزولی مدل‌ها در پشته (از جدیدترین به قدیمی‌ترین)
        for model_idx, model in enumerate(stack):
            # برای هر مدل، شانس روی کلیدهای معتبر
            for key_attempt in range(num_keys):
                cur_key = self.get_api_key(allow_cooldown=False)
                if not cur_key:
                    print(f"⏳ [SOLDIER 0 | {soldier_name}] All API keys currently in cooldown. Routing to Smart Queue...")
                    break

                t0 = time.time()
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

                    key_lock = self._get_key_lock(cur_key)
                    async with key_lock:
                        gap = time.time() - self.key_last_call_time.get(cur_key, 0.0)
                        if gap < 1.0:
                            await asyncio.sleep(1.0 - gap)
                        self.key_last_call_time[cur_key] = time.time()
                        data = await loop.run_in_executor(None, make_req)

                    candidates = data.get("candidates", [])
                    raw_text = ""
                    if candidates:
                        parts = candidates[0].get("content", {}).get("parts", [])
                        raw_text = "\n".join([p.get("text", "") for p in parts if p.get("text")]).strip()

                    if raw_text:
                        elapsed_sec = time.time() - t0
                        self.current_gemini_model = model
                        self.last_used_model = model
                        self.model_usage_stats[model] = self.model_usage_stats.get(model, 0) + 1
                        slot_idx = (self.current_key_idx % len(self.api_keys)) + 1 if self.api_keys else 1
                        print(f"✅ [SOLDIER 0: SUCCESS | {soldier_name}] Model {model} (Tier: {tier}, Key #{slot_idx}) responded in {elapsed_sec:.2f}s.")
                        return raw_text, model, elapsed_sec

                    # خالی بودن یا فیلتر سیفتی
                    finish_reason = candidates[0].get("finishReason", "UNKNOWN") if candidates else "NO_CANDIDATES"
                    print(f"⚠️ [SOLDIER 0 | {soldier_name}] Model {model} returned empty content ({finish_reason}). Key attempt {key_attempt+1}/{num_keys}")
                    self.rotate_api_key(cooldown_seconds=10)

                except urllib.error.HTTPError as e:
                    err_body = ""
                    try:
                        err_body = e.read().decode('utf-8', errors='ignore')
                    except Exception:
                        pass

                    if e.code == 403:
                        print(f"🛑 [SOLDIER 0 | {soldier_name}] Key {cur_key[:12]}... PERMISSION_DENIED / LEAKED (403). Permanently disabling key.")
                        self.key_cooldown_until[cur_key] = time.time() + (86400 * 30)
                        self.rotate_api_key(cooldown_seconds=0)
                    elif e.code == 429:
                        print(f"⚠️ [SOLDIER 0 | {soldier_name}] Quota error 429 on {model} (Key #{self.current_key_idx+1}). Rotating key...")
                        self.rotate_api_key(cooldown_seconds=60)
                        await asyncio.sleep(1.0)
                    elif e.code == 503:
                        print(f"⚠️ [SOLDIER 0 | {soldier_name}] Model {model} temporarily busy (503). Switching to next model...")
                        await asyncio.sleep(0.5)
                        break # Switch to next model in stack without penalizing key
                    elif e.code == 404:
                        print(f"⚠️ [SOLDIER 0 | {soldier_name}] Model {model} not found or deprecated (404). Dropping model from active cycle.")
                        break # بلافاصله برو سراغ مدل بعدی پشته
                    else:
                        print(f"⚠️ [SOLDIER 0 | {soldier_name}] HTTP {e.code} on {model}: {err_body[:100]}. Rotating key...")
                        self.rotate_api_key(cooldown_seconds=15)

                except Exception as e:
                    print(f"⚠️ [SOLDIER 0 | {soldier_name}] Request error on {model}: {e}. Rotating key...")
                    self.rotate_api_key(cooldown_seconds=15)

            # اگر تمام کلیدها برای این مدل ناموفق بودند، سقوط به مدل بعدی پشته
            if model_idx < len(stack) - 1:
                next_cand = stack[model_idx + 1]
                print(f"📉 [SOLDIER 0: MODEL FALLBACK | {soldier_name}] Model {model} exhausted. Falling back to #{model_idx+2}: {next_cand}")

        # اگر کل پشته تمام شد، تلاش نهایی روی پشته معکوس (Cross-Tier Rescue)
        alt_stack = self.flash_stack if tier == "lite" else self.lite_stack
        if alt_stack:
            rescue_model = alt_stack[0]
            print(f"🆘 [SOLDIER 0: RESCUE | {soldier_name}] Primary tier ({tier}) exhausted. Attempting cross-tier rescue with {rescue_model}...")
            cur_key = self.get_api_key(allow_cooldown=True)
            if cur_key:
                t0 = time.time()
                try:
                    url = f"https://generativelanguage.googleapis.com/v1beta/models/{rescue_model}:generateContent?key={cur_key}"
                    req = urllib.request.Request(url, data=json.dumps(payload).encode('utf-8'), headers={'Content-Type': 'application/json'}, method='POST')
                    loop = asyncio.get_running_loop()
                    def make_req():
                        with urllib.request.urlopen(req, timeout=timeout) as response:
                            return json.loads(response.read().decode('utf-8'))
                    key_lock = self._get_key_lock(cur_key)
                    async with key_lock:
                        gap = time.time() - self.key_last_call_time.get(cur_key, 0.0)
                        if gap < 1.0:
                            await asyncio.sleep(1.0 - gap)
                        self.key_last_call_time[cur_key] = time.time()
                        data = await loop.run_in_executor(None, make_req)
                    candidates = data.get("candidates", [])
                    if candidates:
                        parts = candidates[0].get("content", {}).get("parts", [])
                        raw_text = "\n".join([p.get("text", "") for p in parts if p.get("text")]).strip()
                        if raw_text:
                            elapsed_sec = time.time() - t0
                            print(f"✅ [SOLDIER 0: RESCUE SUCCESS | {soldier_name}] Rescued by {rescue_model} in {elapsed_sec:.2f}s.")
                            return raw_text, rescue_model, elapsed_sec
                except Exception as e:
                    print(f"❌ [SOLDIER 0] Cross-tier rescue failed: {e}")

        print(f"❌ [SOLDIER 0: ALL FAILED | {soldier_name}] All models and keys failed. Routing message to Smart Queue.")
        return None, None, 0.0

    async def call_gemini_content(self, payload, timeout=14, max_retries=6, return_model=False):
        """
        تابع سازگار با کدهای قدیمی که از موتور سرباز شماره صفر استفاده می‌کند.
        """
        raw_text, used_model, _ = await self.call_soldier_content(payload, tier="lite", timeout=timeout, soldier_name="LegacyBridge")
        if return_model:
            return raw_text, used_model
        return raw_text
