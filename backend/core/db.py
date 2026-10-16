import sqlite3
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from backend.core.config import POSTED_DB_FILE

class DatabaseManager:
    def __init__(self, db_file=POSTED_DB_FILE):
        self.db_file = db_file
        self.channel_watermarks = {}
        self.recent_posted_map = OrderedDict()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="sentinel_db")
        self.init_posted_db()

    def init_posted_db(self):
        try:
            con = sqlite3.connect(self.db_file)
            cur = con.cursor()
            cur.execute("PRAGMA journal_mode = WAL;")
            cur.execute("PRAGMA synchronous = NORMAL;")
            cur.execute("""
                CREATE TABLE IF NOT EXISTS posted_sources (
                    target_channel TEXT,
                    source_node TEXT,
                    source_msg_id INTEGER,
                    posted_msg_id INTEGER,
                    created_at REAL,
                    PRIMARY KEY (target_channel, source_node, source_msg_id)
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS channel_watermarks (
                    canon_node TEXT PRIMARY KEY,
                    last_seen_msg_id INTEGER,
                    updated_at REAL
                )
            """)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_posted_lookup ON posted_sources (target_channel, source_node, source_msg_id);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_posted_created ON posted_sources (created_at);")
            VIP_CANONICAL_NODES = {'withyashar', 'alibk3', 'vahidonline', 'iliaen', 'netblocks'}

            # 1. Clean up any non-VIP watermarks from table
            cur.execute("""
                DELETE FROM channel_watermarks 
                WHERE LOWER(canon_node) NOT IN ('withyashar', 'alibk3', 'vahidonline', 'iliaen', 'netblocks')
            """)
            con.commit()

            # 2. Load watermarks from dedicated durable table for VIP channels only
            cur.execute("SELECT canon_node, last_seen_msg_id FROM channel_watermarks")
            for node, wm in cur.fetchall():
                if node.lower() in VIP_CANONICAL_NODES:
                    self.channel_watermarks[node] = int(wm)

            # 3. Load posted records into fast lookup map (bounded to 1000 items)
            cur.execute("SELECT target_channel, source_node, source_msg_id, posted_msg_id FROM posted_sources ORDER BY created_at ASC")
            rows = cur.fetchall()
            for ch, node, src_id, dst_id in rows:
                canon = node or 'unknown'
                self.recent_posted_map[(ch, canon, src_id)] = dst_id
                if len(self.recent_posted_map) > 1000:
                    self.recent_posted_map.popitem(last=False)
                # Backfill watermark if higher than existing (VIP only)
                if canon.lower() in VIP_CANONICAL_NODES:
                    self.channel_watermarks[canon] = max(self.channel_watermarks.get(canon, 0), src_id)
            con.close()
            print(f"🗄️ Initialized SQLite registry: loaded {len(rows)} posted records & {len(self.channel_watermarks)} VIP watermarks.")
        except Exception as e:
            print(f"⚠️ Error initializing SQLite DB: {e}")

    def get_posted_message_id(self, target_channel, source_node, source_msg_id):
        """
        دریافت شناسه پست تلگرامی متناظر با منبع (یا None در صورت عدم انتشار).
        """
        try:
            src_int = int(source_msg_id) if str(source_msg_id).isdigit() else source_msg_id
            canon = source_node or 'unknown'

            # 1. Ultra-fast in-memory lookup (O(1))
            if (target_channel, canon, src_int) in self.recent_posted_map:
                return self.recent_posted_map[(target_channel, canon, src_int)]

            # 2. Durable SQLite fallback
            con = sqlite3.connect(self.db_file)
            cur = con.cursor()
            cur.execute(
                "SELECT posted_msg_id FROM posted_sources WHERE target_channel = ? AND source_node = ? AND source_msg_id = ?",
                (target_channel, source_node, src_int)
            )
            row = cur.fetchone()
            con.close()
            if row:
                self.recent_posted_map[(target_channel, canon, src_int)] = row[0]
                if len(self.recent_posted_map) > 1000:
                    self.recent_posted_map.popitem(last=False)
                return row[0]
        except Exception:
            pass
        return None

    def is_source_posted(self, target_channel, source_node, source_msg_id):
        return self.get_posted_message_id(target_channel, source_node, source_msg_id) is not None

    def _sync_delete_posted(self, target_channel, source_node, src_int):
        try:
            con = sqlite3.connect(self.db_file)
            cur = con.cursor()
            cur.execute(
                "DELETE FROM posted_sources WHERE target_channel = ? AND source_node = ? AND source_msg_id = ?",
                (target_channel, source_node, src_int)
            )
            con.commit()
            con.close()
        except Exception as e:
            print(f"⚠️ Error deleting posted record from DB: {e}")

    def delete_posted_record(self, target_channel, source_node, source_msg_id):
        """
        حذف رکورد ثبت‌شده در صورت نیاز به چتر نجات یا رفع دابل‌پست.
        """
        try:
            src_int = int(source_msg_id) if str(source_msg_id).isdigit() else source_msg_id
            canon = source_node or 'unknown'
            self.recent_posted_map.pop((target_channel, canon, src_int), None)
            self._executor.submit(self._sync_delete_posted, target_channel, source_node, src_int)
            return True
        except Exception as e:
            print(f"⚠️ Error initiating delete posted record from DB: {e}")
            return False

    def _sync_insert_posted(self, target_channel, source_node, src_int, dst_int, ts):
        try:
            con = sqlite3.connect(self.db_file)
            cur = con.cursor()
            cur.execute("""
                INSERT OR REPLACE INTO posted_sources (target_channel, source_node, source_msg_id, posted_msg_id, created_at)
                VALUES (?, ?, ?, ?, ?)
            """, (target_channel, source_node, src_int, dst_int, ts))
            con.commit()
            con.close()
        except Exception as e:
            print(f"⚠️ Error in background DB write: {e}")

    def record_posted_message(self, target_channel, source_node, source_msg_id, posted_msg_id):
        try:
            src_int = int(source_msg_id) if str(source_msg_id).isdigit() else source_msg_id
            dst_int = int(posted_msg_id) if str(posted_msg_id).isdigit() else posted_msg_id
            canon = source_node or 'unknown'
            
            # 1. Update fast in-memory map (bounded to 1000 items with true FIFO eviction)
            self.recent_posted_map[(target_channel, canon, src_int)] = dst_int
            if len(self.recent_posted_map) > 1000:
                self.recent_posted_map.popitem(last=False)
                    
            # 2. Advance watermark in memory & SQLite
            self.advance_watermark(canon, src_int)
            
            # 3. Non-blocking asynchronous persistence to SQLite
            self._executor.submit(self._sync_insert_posted, target_channel, source_node, src_int, dst_int, time.time())
            return True
        except Exception as e:
            print(f"⚠️ Error recording posted message to DB: {e}")
            return False

    def get_watermark(self, canon_node):
        return self.channel_watermarks.get(canon_node, 0)

    def _sync_update_watermark(self, canon_node, new_val, ts):
        try:
            con = sqlite3.connect(self.db_file)
            cur = con.cursor()
            cur.execute("""
                INSERT INTO channel_watermarks (canon_node, last_seen_msg_id, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(canon_node) DO UPDATE SET
                    last_seen_msg_id = MAX(last_seen_msg_id, excluded.last_seen_msg_id),
                    updated_at = excluded.updated_at
            """, (canon_node, new_val, ts))
            con.commit()
            con.close()
        except Exception as e:
            print(f"⚠️ Error persisting watermark for {canon_node} to DB: {e}")

    def advance_watermark(self, canon_node, msg_id):
        VIP_CANONICAL_NODES = {'withyashar', 'alibk3', 'vahidonline', 'iliaen', 'netblocks'}
        if not canon_node or (canon_node or '').lower() not in VIP_CANONICAL_NODES or not msg_id:
            return
        try:
            src_int = int(msg_id)
        except (ValueError, TypeError):
            return

        old_val = self.channel_watermarks.get(canon_node, 0)
        new_val = max(old_val, src_int)
        self.channel_watermarks[canon_node] = new_val

        # Non-blocking immediate write to SQLite channel_watermarks table
        if new_val > old_val or canon_node not in self.channel_watermarks:
            self._executor.submit(self._sync_update_watermark, canon_node, new_val, time.time())

    def optimize(self):
        try:
            con = sqlite3.connect(self.db_file)
            cur = con.cursor()
            cur.execute("PRAGMA optimize;")
            con.commit()
            con.close()
        except Exception as e:
            print(f"⚠️ Error optimizing SQLite DB: {e}")

    def close(self):
        try:
            self._executor.shutdown(wait=True)
        except Exception:
            pass
