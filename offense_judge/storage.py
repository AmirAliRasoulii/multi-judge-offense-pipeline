import json
import os
import socket
import sqlite3
import threading
from pathlib import Path
from .common import atomic_text, digest, dumps, now


class RunLock:
    def __init__(self, directory):
        self.path = Path(directory) / ".run.lock"

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            try:
                previous = json.loads(self.path.read_text())
                if previous.get("host") == socket.gethostname():
                    try:
                        os.kill(previous["pid"], 0)
                    except ProcessLookupError:
                        self.path.unlink(missing_ok=True)
                        return self.__enter__()
            except (json.JSONDecodeError, FileNotFoundError, OSError):
                self.path.unlink(missing_ok=True)
                return self.__enter__()
            raise ValueError("Run directory is locked by another process")
        with os.fdopen(fd, "w") as f:
            f.write(dumps({"pid": os.getpid(), "host": socket.gethostname(), "at": now()}))
        return self

    def __exit__(self, *args):
        self.path.unlink(missing_ok=True)


class Store:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(self.directory / "state.sqlite", check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript("""
          CREATE TABLE IF NOT EXISTS votes (cache_key TEXT PRIMARY KEY, value TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS attempts (id INTEGER PRIMARY KEY, cache_key TEXT, slot INTEGER, value TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS reviews (record_id TEXT PRIMARY KEY, value TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS review_log (id INTEGER PRIMARY KEY, record_id TEXT, value TEXT NOT NULL);
        """)
        self.db.commit()

    def close(self):
        self.db.close()

    def init_run(self, records, config):
        from .schema import SCHEMA
        spec = {"configuration": config.public(), "input_hash": digest(records), "schema": SCHEMA}
        fingerprint = digest(spec)
        path = self.directory / "manifest.json"
        if path.exists():
            manifest = json.loads(path.read_text())
            if manifest["fingerprint"] != fingerprint:
                raise ValueError("Input/prompt/schema/model settings changed. Use a new output directory.")
        else:
            manifest = {"run_id": fingerprint[:24], "fingerprint": fingerprint, "created_at": now(), **spec}
            atomic_text(self.directory / "input_snapshot.jsonl", "".join(dumps(r) + "\n" for r in records))
            atomic_text(self.directory / "prompt_snapshot.md", config.prompt)
            atomic_text(path, dumps(manifest, indent=2))
        return manifest

    def get_vote(self, key):
        with self.lock:
            row = self.db.execute("SELECT value FROM votes WHERE cache_key=?", (key,)).fetchone()
            return json.loads(row[0]) if row else None

    def set_vote(self, key, value):
        with self.lock:
            self.db.execute("INSERT OR REPLACE INTO votes VALUES (?,?)", (key, dumps(value)))
            self.db.commit()

    def attempt(self, key, slot, value):
        with self.lock:
            cursor = self.db.execute("INSERT INTO attempts (cache_key,slot,value) VALUES (?,?,?)", (key, slot, dumps(value)))
            self.db.commit()
            return cursor.lastrowid

    def attempts(self):
        return list(self.iter_attempts())

    def iter_attempts(self):
        # Export after worker pool drains; stream raw responses instead of loading gigabytes.
        for row in self.db.execute("SELECT id,cache_key,slot,value FROM attempts ORDER BY id"):
            yield {"attempt_id": row[0], "cache_key": row[1], "slot": row[2], **json.loads(row[3])}

    def reviews(self):
        return {row[0]: json.loads(row[1]) for row in self.db.execute("SELECT record_id,value FROM reviews")}

    def set_reviews(self, updates):
        # All validation happens before entering this transaction.
        with self.lock, self.db:
            previous = self.reviews()
            for ident, value in updates.items():
                if previous.get(ident) == value:
                    continue
                self.db.execute("INSERT OR REPLACE INTO reviews VALUES (?,?)", (ident, dumps(value)))
                self.db.execute("INSERT INTO review_log(record_id,value) VALUES (?,?)", (ident, dumps({
                    "at": now(), "before": previous.get(ident), "after": value})))

    def review_log(self):
        return [{"id": row[0], **json.loads(row[1])} for row in self.db.execute("SELECT record_id,value FROM review_log ORDER BY id")]
