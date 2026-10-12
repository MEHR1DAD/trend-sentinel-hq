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
            cur.execute("""
                CREATE TABLE IF NOT EXISTS channel_watermarks (
                    canon_node TEXT PRIMARY KEY,
                    last_seen_msg_id INTEGER,
                    updated_at REAL
                )
            """)
            con.commit()
            
            # 1. Load watermarks from dedicated durable table
            cur.execute("SELECT canon_node, last_seen_msg_id FROM channel_watermarks")
            for node, wm in cur.fetchall():
                self.channel_watermarks[node] = int(wm)

            # 2. Load posted records into fast lookup map
            cur.execute("SELECT target_channel, source_node, source_msg_id, posted_msg_id FROM posted_sources")
            rows = cur.fetchall()
            for ch, node, src_id, dst_id in rows:
                canon = node or 'unknown'
                self.recent_posted_map[(ch, canon, src_id)] = dst_id
                # Backfill watermark if higher than existing
                self.channel_watermarks[canon] = max(self.channel_watermarks.get(canon, 0), src_id)
            con.close()
            print(f"🗄️ Initialized SQLite registry: loaded {len(rows)} posted records & {len(self.channel_watermarks)} watermarks.")
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
            
            # 1. Update fast in-memory map (bounded to 1000 items)
            self.recent_posted_map[(target_channel, canon, src_int)] = dst_int
            if len(self.recent_posted_map) > 1000:
                oldest_keys = list(self.recent_posted_map.keys())[:200]
                for ok in oldest_keys:
                    self.recent_posted_map.pop(ok, None)
                    
            # 2. Advance watermark in memory & SQLite
            self.advance_watermark(canon, src_int)
            
            # 3. Persist to SQLite posted_sources
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
        if not canon_node or not msg_id:
            return
        try:
            src_int = int(msg_id)
        except (ValueError, TypeError):
            return

        old_val = self.channel_watermarks.get(canon_node, 0)
        new_val = max(old_val, src_int)
        self.channel_watermarks[canon_node] = new_val

        # Durable immediate write to SQLite channel_watermarks table
        if new_val > old_val or canon_node not in self.channel_watermarks:
            try:
                con = sqlite3.connect(self.db_file)
                cur = con.cursor()
                cur.execute("""
                    INSERT INTO channel_watermarks (canon_node, last_seen_msg_id, updated_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(canon_node) DO UPDATE SET
                        last_seen_msg_id = MAX(last_seen_msg_id, excluded.last_seen_msg_id),
                        updated_at = excluded.updated_at
                """, (canon_node, new_val, time.time()))
                con.commit()
                con.close()
            except Exception as e:
                print(f"⚠️ Error persisting watermark for {canon_node} to DB: {e}")
