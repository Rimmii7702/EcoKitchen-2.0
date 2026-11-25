"""SQLite account, revocable session, and login throttle storage."""

import sqlite3
from contextlib import closing, contextmanager
from hashlib import sha256
from pathlib import Path
from secrets import token_urlsafe


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY, username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL, role TEXT NOT NULL CHECK (role IN ('admin','viewer'))
);
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    csrf TEXT NOT NULL, expires INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS sessions_expires ON sessions(expires);
CREATE TABLE IF NOT EXISTS login_limits (
    peer TEXT PRIMARY KEY, started INTEGER NOT NULL, attempts INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS login_limits_started ON login_limits(started);
"""


def digest(token):
    return sha256(token.encode("utf-8")).hexdigest()


class AuthStore:
    def __init__(self, path: Path):
        self.path = path

    @contextmanager
    def connection(self, *, create=False):
        uri = self.path.as_uri() + ("?mode=rwc" if create else "?mode=rw")
        with closing(sqlite3.connect(uri, uri=True, timeout=5)) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            with connection:
                yield connection

    def create_user(self, username, password_hash, role):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection(create=True) as db:
            db.executescript(SCHEMA)
            db.execute("INSERT INTO users(username,password_hash,role) VALUES (?,?,?)", (username, password_hash, role))

    def attempt(self, peer, now, window, limit):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM login_limits WHERE started <= ?", (now - window,))
            row = db.execute("SELECT started, attempts FROM login_limits WHERE peer = ?", (peer,)).fetchone()
            if row and row["attempts"] >= limit:
                return row["started"] + window - now
            db.execute("""
                INSERT INTO login_limits VALUES (?, ?, 1)
                ON CONFLICT(peer) DO UPDATE SET attempts = attempts + 1
                """, (peer, now))
            return 0

    def user(self, username):
        with self.connection() as db:
            return db.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()

    def issue(self, user_id, now, lifetime, previous):
        token, csrf = token_urlsafe(32), token_urlsafe(32)
        with self.connection() as db:
            db.execute("DELETE FROM sessions WHERE expires <= ? OR id = ?", (now, digest(previous) if previous else ""))
            db.execute("INSERT INTO sessions VALUES (?,?,?,?)", (digest(token), user_id, csrf, now + lifetime))
        return token, csrf

    def session(self, token, now):
        with self.connection() as db:
            return db.execute("""
                SELECT u.username, u.role, s.csrf
                FROM sessions s JOIN users u ON u.id = s.user_id
                WHERE s.id = ? AND s.expires > ?
                """, (digest(token), now)).fetchone()

    def revoke(self, token):
        with self.connection() as db:
            db.execute("DELETE FROM sessions WHERE id = ?", (digest(token),))
