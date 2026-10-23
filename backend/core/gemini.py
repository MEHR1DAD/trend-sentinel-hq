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
    ۱. استعلام دوره‌ای هر ۱۲ ساعت (۰۰:۰۰ و ۱۲:۰۰ UTC) از گوگل (/v1beta/models) و تست زنده سلامت (Active Health Check).
    ۲. چینش ۳ پشته نزولی:
       - LITE_STACK:  [جدیدترین لایت, دومین لایت جدید, ...]
       - FLASH_STACK: [جدیدترین فلش, دومین فلش جدید, ...]
       - PRO_STACK:   [جدیدترین پرو, دومین پرو جدید, ...]
    ۳. غربالگری مدل‌های دپریکیت شده (مثل ۴۰۴ مدل ۲.۵) با پینگ ۱ توکنی زنده.
    ۴. ماتریس تاب‌آوری دو بعدی (مدل × کلید):
       - تفکیک قطعی خطای کوتا ۴۲۹ روی جفت (مدل، کلید)
       - چرخش کلید ۱ -> کلید ۲ -> کلید ۳ روی همان مدل
       - سقوط پله‌ای به مدل بعدی و سپس پشته بعدی
    """
    def __init__(self, api_keys=None):
        self.api_keys = api_keys if api_keys is not None else GEMINI_API_KEYS
        self.current_key_idx = 0
        self.current_gemini_model = DEFAULT_GEMINI_MODEL
        self.latest_flash_lite_model = DEFAULT_GEMINI_MODEL
        self.last_used_model = DEFAULT_GEMINI_MODEL
        self.model_usage_stats = {}
        self.key_cooldown_until = {k: 0.0 for k in self.api_keys}
        self.key_model_cooldown = {}  # Map: (model, key) -> timestamp
        self.key_locks = {k: asyncio.Lock() for k in self.api_keys}
        self.key_last_call_time = {k: 0.0 for k in self.api_keys}
        self.gemini_lock = asyncio.Lock()
        self.last_gemini_call_time = 0.0
        self.model_cooldown_until = {}

        # پشته‌های پویا و کش دیسک برای سرباز شماره صفر
        self.cache_file = 'backend/model_cache.json'
        self.last_discovery_time = 0.0
        self.pro_stack = []
        self.flash_stack = []
        self.lite_stack = []
        
        # بارگذاری اولیه از کش دیسک
        self.load_cached_stacks()
        print(f"🔑 [SOLDIER 0] Initialized Gemini Pool with {len(self.api_keys)} API keys. Lite: {len(self.lite_stack)}, Flash: {len(self.flash_stack)}, Pro: {len(self.pro_stack)}")

    def get_api_key(self, allow_cooldown=False):
        if not self.api_keys:
            return None
        now = time.time()
        for i in range(len(self.api_keys)):
            idx = (self.current_key_idx + i) % len(self.api_keys)
            k = self.api_keys[idx]
            if self.key_cooldown_until.get(k, 0.0) <= now:
                self.current_key_idx = (idx + 1) % len(self.api_keys)
                return k
        if allow_cooldown:
            best_idx = min(range(len(self.api_keys)), key=lambda i: self.key_cooldown_until.get(self.api_keys[i], 0.0))
            self.current_key_idx = best_idx
            return self.api_keys[best_idx]
        return None

    def rotate_api_key(self, key=None, cooldown_seconds=60):
        if not self.api_keys:
            return None
        target_key = key if key and key in self.api_keys else self.api_keys[(self.current_key_idx - 1) % len(self.api_keys)]
        if cooldown_seconds > 0:
            self.key_cooldown_until[target_key] = time.time() + cooldown_seconds
        available = any(self.key_cooldown_until.get(k, 0.0) <= time.time() for k in self.api_keys)
        print(f"🔄 [SOLDIER 0: KEY ROTATE] Cooldown on key {target_key[:8]}... ({cooldown_seconds}s). Active keys available: {available}")
        return target_key

    def _get_key_lock(self, key):
        if key not in self.key_locks:
            self.key_locks[key] = asyncio.Lock()
        return self.key_locks[key]

    def load_cached_stacks(self):
        deprecated_models = {'gemini-2.5-flash'}
        if os.path.exists(self.cache_file):
            try:
                with open(self.cache_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self.pro_stack = [m for m in data.get('pro_stack', []) if m not in deprecated_models]
                    self.flash_stack = [m for m in data.get('flash_stack', []) if m not in deprecated_models]
                    self.lite_stack = [m for m in data.get('lite_stack', []) if m not in deprecated_models]
                    self.last_discovery_time = data.get('last_discovery_time', 0.0)
                    if self.lite_stack:
                        self.latest_flash_lite_model = self.lite_stack[0]
                        self.current_gemini_model = self.lite_stack[0]
            except Exception as e:
                print(f"⚠️ [SOLDIER 0] Could not load model cache: {e}")

        # در صورت نبود کش دیسک، بارگذاری پیش‌فرض‌های امن
        if not self.lite_stack:
            self.lite_stack = [m for m in GEMINI_MODELS_PRIORITY if 'lite' in m] or ["gemini-3.5-flash-lite", "gemini-flash-lite-latest", "gemini-3.1-flash-lite"]
        if not self.flash_stack:
            self.flash_stack = [m for m in GEMINI_MODELS_PRIORITY if 'lite' not in m and 'pro' not in m and m not in deprecated_models] or ["gemini-3.5-flash", "gemini-flash-latest", "gemini-3.8-flash"]
        if not self.pro_stack:
            self.pro_stack = ["gemini-2.5-pro", "gemini-pro-latest"]

        # اولویت‌بندی مدل‌های پایدار تولیدی
        if self.lite_stack:
            self.lite_stack.sort(key=self.parse_model_version_score, reverse=True)
            self.latest_flash_lite_model = self.lite_stack[0]
            self.current_gemini_model = self.lite_stack[0]
        if self.flash_stack:
            self.flash_stack.sort(key=self.parse_model_version_score, reverse=True)
        if self.pro_stack:
            self.pro_stack.sort(key=self.parse_model_version_score, reverse=True)

    def save_cached_stacks(self):
        try:
            data = {
                'pro_stack': self.pro_stack,
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
        # مدل‌های پیش‌نمایش یا دارای سقف روزانه محدود نباید اولویت بالاتری از مدل‌های پایدار GA داشته باشند
        is_experimental = 1 if any(exp in clean for exp in ['3.8', '3.7', '3.6', '3.1', 'preview', 'exp', 'omni']) else 0
        is_production = 1 if not is_experimental else 0
        has_date = 1 if re.search(r'\d{2}-\d{2}|\d{4}', clean) else 0
        return (is_production, score, has_date, clean)

    async def _live_ping_model(self, model_name, api_key):
        """
        تست زنده سلامت (Active Health Check) با ۱ توکن
        بررسی پاسخ ۲۰۰ و شناسایی مدل‌های دپریکیت شده یا ۴۰۴
        """
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
        payload = json.dumps({
            "contents": [{"parts": [{"text": "ping"}]}],
            "generationConfig": {"maxOutputTokens": 1}
        }).encode('utf-8')
        req = urllib.request.Request(
            url,
            data=payload,
            headers={'Content-Type': 'application/json'},
            method='POST'
        )
        loop = asyncio.get_running_loop()
        def do_ping():
            try:
                with urllib.request.urlopen(req, timeout=5) as response:
                    return response.status == 200
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    return False
                return True
            except Exception:
                return True
        return await loop.run_in_executor(None, do_ping)

    async def fetch_latest_google_models(self, force=False):
        """
        استعلام دوره‌ای هر ۱۲ ساعت (۴۳۲۰۰ ثانیه) به همراه تست زنده سلامت (Active Health Check).
        """
        now = time.time()
        twelve_hours = 12 * 3600
        if not force and (now - self.last_discovery_time) < twelve_hours and self.flash_stack and self.lite_stack and self.pro_stack:
            return self.lite_stack + self.flash_stack + self.pro_stack

        active_key = self.get_api_key()
        if not active_key:
            return self.lite_stack + self.flash_stack + self.pro_stack

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
                and "gemini" in m['name'].lower()
                and not any(bad in m['name'].lower() for bad in ['tts', 'image', 'audio', 'embed', 'customtools', 'deep-research', 'realtime', 'live', 'vision', 'lyria', 'banana'])
            ]

            cand_lite = [m for m in valid if 'gemini' in m and ('flash-lite' in m or 'flash_lite' in m or 'lite' in m)]
            cand_pro = [m for m in valid if 'gemini' in m and 'pro' in m and ('lite' not in m and 'flash' not in m)]
            cand_flash = [m for m in valid if 'gemini' in m and 'flash' in m and ('lite' not in m and 'flash-lite' not in m and 'flash_lite' not in m and 'pro' not in m)]

            cand_lite.sort(key=self.parse_model_version_score, reverse=True)
            cand_flash.sort(key=self.parse_model_version_score, reverse=True)
            cand_pro.sort(key=self.parse_model_version_score, reverse=True)

            # تست زنده سلامت با پینگ ۱ توکنی برای کاندیداها
            async def filter_healthy_candidates(model_list):
                to_test = model_list[:10]
                tasks = [self._live_ping_model(m, active_key) for m in to_test]
                results = await asyncio.gather(*tasks, return_exceptions=True)
                healthy = []
                for m, ok in zip(to_test, results):
                    if ok is True:
                        healthy.append(m)
                    else:
                        print(f"🗑️ [SOLDIER 0: ACTIVE HEALTH CHECK] Discarded deprecated/404 model: {m}")
                return healthy

            healthy_lite = await filter_healthy_candidates(cand_lite)
            healthy_flash = await filter_healthy_candidates(cand_flash)
            healthy_pro = await filter_healthy_candidates(cand_pro)

            if healthy_lite:
                self.lite_stack = healthy_lite
                self.latest_flash_lite_model = healthy_lite[0]
                self.current_gemini_model = healthy_lite[0]
            if healthy_flash:
                self.flash_stack = healthy_flash
            if healthy_pro:
                self.pro_stack = healthy_pro

            self.last_discovery_time = now
            self.save_cached_stacks()

            print(f"🎖️ [SOLDIER 0: MODEL REGISTRY UPDATE] Discovery & Live Ping Completed (Next update in 12h):")
            print(f"   ⚡️ LITE_STACK ({len(self.lite_stack)} models): {self.lite_stack[:4]}")
            print(f"   ⚡️ FLASH_STACK ({len(self.flash_stack)} models): {self.flash_stack[:4]}")
            print(f"   ⚡️ PRO_STACK ({len(self.pro_stack)} models): {self.pro_stack[:4]}")
            return self.lite_stack + self.flash_stack + self.pro_stack
        except Exception as e:
            print(f"⚠️ [SOLDIER 0] Discovery failed ({e}). Gracefully continuing with cached stacks.")
            return self.lite_stack + self.flash_stack + self.pro_stack

    async def rotate_gemini_model_dynamic(self, failed_model=None):
        if failed_model:
            self.model_cooldown_until[failed_model] = time.time() + 3600
        stack = self.lite_stack or [DEFAULT_GEMINI_MODEL]
        for m in stack:
            if self.model_cooldown_until.get(m, 0.0) <= time.time():
                self.current_gemini_model = m
                return m
        return self.current_gemini_model

    async def call_soldier_content(self, payload, tier="lite", timeout=15, soldier_name="Soldier"):
        """
        موتور اجرایی استراتژیک سرباز شماره صفر:
        - ماتریس تاب‌آوری دو بعدی (مدل × کلید)
        - تفکیک قطعی کوتا بر مبنای (model, key) جهت جلوگیری از فریز شدن کل مدل با سهمیه یک کلید
        - چرخش ترتیبی کلید ۱ -> ۲ -> ۳ روی همان مدل قبل از جهش به مدل بعدی
        - پله‌های نزولی درون هر پشته و سقوط نرم بین پشته‌ها (Lite -> Flash -> Pro)
        """
        await self.fetch_latest_google_models(force=False)

        payload.setdefault("safetySettings", [
            {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"}
        ])

        # تعیین زنجیره پشته‌ها بر اساس ردیف درخواستی (Tier Chain)
        if tier == "lite":
            tier_chain = [("lite", self.lite_stack), ("flash", self.flash_stack), ("pro", self.pro_stack)]
        elif tier == "flash":
            tier_chain = [("flash", self.flash_stack), ("lite", self.lite_stack), ("pro", self.pro_stack)]
        elif tier == "pro":
            tier_chain = [("pro", self.pro_stack), ("flash", self.flash_stack), ("lite", self.lite_stack)]
        else:
            tier_chain = [("lite", self.lite_stack), ("flash", self.flash_stack), ("pro", self.pro_stack)]

        loop = asyncio.get_running_loop()

        for current_tier_name, stack in tier_chain:
            if not stack:
                continue

            for model_idx, model in enumerate(stack):
                now = time.time()
                model_cooldown_left = self.model_cooldown_until.get(model, 0.0) - now
                if model_cooldown_left > 0:
                    continue

                # پیمایش تمام کلیدهای معتبر برای این مدل
                for key_idx, cur_key in enumerate(self.api_keys):
                    now = time.time()
                    # بررسی کول‌داون کلی کلید (فقط برای کلیدهای باطل/403)
                    if self.key_cooldown_until.get(cur_key, 0.0) > now:
                        continue
                    # بررسی کول‌داون تفکیک‌شده این کلید روی این مدل خاص
                    if self.key_model_cooldown.get((model, cur_key), 0.0) > now:
                        continue

                    t0 = time.time()
                    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={cur_key}"
                    try:
                        req = urllib.request.Request(
                            url,
                            data=json.dumps(payload).encode('utf-8'),
                            headers={'Content-Type': 'application/json'},
                            method='POST'
                        )

                        def make_req():
                            with urllib.request.urlopen(req, timeout=timeout) as response:
                                return json.loads(response.read().decode('utf-8'))

                        key_lock = self._get_key_lock(cur_key)
                        async with key_lock:
                            gap = time.time() - self.key_last_call_time.get(cur_key, 0.0)
                            if gap < 0.6:
                                await asyncio.sleep(0.6 - gap)
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
                            print(f"✅ [SOLDIER 0: SUCCESS | {soldier_name}] Model {model} (Tier: {current_tier_name}, Key #{key_idx+1}) responded in {elapsed_sec:.2f}s.")
                            return raw_text, model, elapsed_sec

                        # خالی بودن محتوا یا بلاک سیفتی
                        finish_reason = candidates[0].get("finishReason", "UNKNOWN") if candidates else "NO_CANDIDATES"
                        print(f"⚠️ [SOLDIER 0 | {soldier_name}] Model {model} returned empty content ({finish_reason}). Key #{key_idx+1}")
                        self.key_model_cooldown[(model, cur_key)] = time.time() + 15
                        continue

                    except urllib.error.HTTPError as e:
                        err_body = ""
                        try:
                            err_body = e.read().decode('utf-8', errors='ignore')
                        except Exception:
                            pass

                        if e.code == 403:
                            print(f"🛑 [SOLDIER 0 | {soldier_name}] Key #{key_idx+1} ({cur_key[:10]}...) PERMISSION_DENIED / LEAKED (403). Permanently disabling key.")
                            self.key_cooldown_until[cur_key] = time.time() + (86400 * 30)
                            continue

                        elif e.code == 429:
                            # تفکیک قطعی: خطای کوتا منحصراً برای جفت (مدل، کلید) ثبت می‌شود تا این کلید بتواند روی سایر مدل‌های پشته کار کند!
                            is_daily = any(t in err_body.lower() for t in ["day", "daily", "perday", "limit: 20", "limit: 15", "limit: 50"])
                            is_exhausted = any(t in err_body.lower() for t in ["resource_exhausted", "check quota", "quota exceeded"])
                            if is_daily or is_exhausted:
                                self.key_model_cooldown[(model, cur_key)] = time.time() + 3600  # ۱ ساعت کول‌داون این کلید روی این مدل خاص
                                print(f"⚠️ [SOLDIER 0 | {soldier_name}] Key #{key_idx+1} quota exhausted (429) on {model}. Isolating (model, key) for 1h. Trying NEXT KEY on same model...")
                            else:
                                self.key_model_cooldown[(model, cur_key)] = time.time() + 25  # فقط ۲۵ ثانیه کول‌داون روی این مدل
                                print(f"⚠️ [SOLDIER 0 | {soldier_name}] Key #{key_idx+1} RPM limit (429) on {model}. Isolating (model, key) for 25s. Trying NEXT KEY on same model...")
                            await asyncio.sleep(0.2)
                            continue  # امتحان کلید بعدی روی همین مدل!

                        elif e.code == 404:
                            print(f"⚠️ [SOLDIER 0 | {soldier_name}] Model {model} returned 404 (deprecated/not found). Disabling model globally for 24h.")
                            self.model_cooldown_until[model] = time.time() + 86400
                            break  # خروج از حلقه کلیدها برای جهش بلافاصله به مدل بعدی در پشته

                        elif e.code in (500, 502, 503, 504):
                            print(f"⚠️ [SOLDIER 0 | {soldier_name}] Model {model} temporary error ({e.code}). Switching to next model...")
                            await asyncio.sleep(0.3)
                            break  # جهش به مدل بعدی

                        else:
                            print(f"⚠️ [SOLDIER 0 | {soldier_name}] HTTP {e.code} on {model} (Key #{key_idx+1}): {err_body[:100]}.")
                            self.key_model_cooldown[(model, cur_key)] = time.time() + 30
                            continue

                    except Exception as e:
                        print(f"⚠️ [SOLDIER 0 | {soldier_name}] Request error on {model} (Key #{key_idx+1}): {e}.")
                        self.key_model_cooldown[(model, cur_key)] = time.time() + 15
                        continue

                # اگر تمام کلیدها روی این مدل ناموفق بودند، این مدل را موقتاً کول‌داون کن تا پیام‌های بعدی رویش گیر نکنند!
                self.model_cooldown_until[model] = time.time() + 300  # ۵ دقیقه کول‌داون روی این مدل
                if model_idx < len(stack) - 1:
                    next_cand = stack[model_idx + 1]
                    print(f"📉 [SOLDIER 0: MODEL FALLBACK | {soldier_name}] All keys exhausted for {model}. Falling back to #{model_idx+2}: {next_cand}")

            # اگر کل این پشته تمام شد، لاگ آبشاری به پشته بعدی
            print(f"📉 [SOLDIER 0: TIER CASCADE | {soldier_name}] Tier '{current_tier_name}' fully exhausted. Cascading to next available tier...")

        print(f"❌ [SOLDIER 0: ALL FAILED | {soldier_name}] All models across all tiers and keys failed. Routing message to Smart Queue.")
        return None, None, 0.0

    async def call_gemini_content(self, payload, timeout=14, max_retries=6, return_model=False):
        """
        تابع سازگار با کدهای قدیمی که از موتور سرباز شماره صفر استفاده می‌کند.
        """
        raw_text, used_model, _ = await self.call_soldier_content(payload, tier="lite", timeout=timeout, soldier_name="LegacyBridge")
        if return_model:
            return raw_text, used_model
        return raw_text
