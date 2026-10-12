import sqlite3
import time
from backend.core.config import POSTED_DB_FILE

class DatabaseManager:
    def __init__(self, db_file=POSTED_DB_FILE):
        self.db_file = db_file
        self.channel_watermarks = {}
        self.recent_posted_map = {}
        self.init_posted_db()

    def init_posted_db(self):
        try:
            con = sqlite3.connect(self.db_file)
            cur = con.cursor()
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
            con.commit()
            
            cur.execute("SELECT target_channel, source_node, source_msg_id, posted_msg_id FROM posted_sources")
            rows = cur.fetchall()
            for ch, node, src_id, dst_id in rows:
                canon = node or 'unknown'
                self.recent_posted_map[(ch, canon, src_id)] = dst_id
                self.channel_watermarks[canon] = max(self.channel_watermarks.get(canon, 0), src_id)
            con.close()
            print(f"🗄️ Initialized SQLite registry: loaded {len(rows)} posted records into in-memory fast map.")
        except Exception as e:
            print(f"⚠️ Error initializing SQLite DB: {e}")

    def is_source_posted(self, target_channel, source_node, source_msg_id):
        try:
            src_int = int(source_msg_id) if str(source_msg_id).isdigit() else source_msg_id
            canon = source_node or 'unknown'
            
            # 1. Ultra-fast in-memory lookup (O(1))
            if (target_channel, canon, src_int) in self.recent_posted_map:
                return True
                
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
                return True
        except Exception:
            pass
        return False

    def record_posted_message(self, target_channel, source_node, source_msg_id, posted_msg_id):
        try:
            src_int = int(source_msg_id) if str(source_msg_id).isdigit() else source_msg_id
            dst_int = int(posted_msg_id) if str(posted_msg_id).isdigit() else posted_msg_id
            canon = source_node or 'unknown'
            
            # 1. Update fast in-memory map & watermark (bounded to 1000 items)
            self.recent_posted_map[(target_channel, canon, src_int)] = dst_int
            if len(self.recent_posted_map) > 1000:
                oldest_keys = list(self.recent_posted_map.keys())[:200]
                for ok in oldest_keys:
                    self.recent_posted_map.pop(ok, None)
                    
            self.channel_watermarks[canon] = max(self.channel_watermarks.get(canon, 0), src_int)
            
            # 2. Persist to SQLite
            con = sqlite3.connect(self.db_file)
            cur = con.cursor()
            cur.execute("""
                INSERT OR REPLACE INTO posted_sources (target_channel, source_node, source_msg_id, posted_msg_id, created_at)
                VALUES (?, ?, ?, ?, ?)
            """, (target_channel, source_node, src_int, dst_int, time.time()))
            con.commit()
            con.close()
            return True
        except Exception as e:
            print(f"⚠️ Error recording posted message to DB: {e}")
            return False

    def get_watermark(self, canon_node):
        return self.channel_watermarks.get(canon_node, 0)

    def advance_watermark(self, canon_node, msg_id):
        src_int = int(msg_id) if str(msg_id).isdigit() else msg_id
        self.channel_watermarks[canon_node] = max(self.channel_watermarks.get(canon_node, 0), src_int)
