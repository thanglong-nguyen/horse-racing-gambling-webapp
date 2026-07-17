import sqlite3

DB_PATH = "race.db"

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn
    

SCHEMA = """
CREATE TABLE IF NOT EXISTS races (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    status            TEXT NOT NULL,
    seed              INTEGER NOT NULL,
    betting_closes_at REAL NOT NULL,
    result_json       TEXT
);

--race_horses table  (race_id, lane, name, base_speed, stamina,
--                           loss_rate, odds)         

CREATE TABLE IF NOT EXISTS race_horses (
    race_id     INTEGER NOT NULL REFERENCES races(id),
    lane        INTEGER NOT NULL,
    name        TEXT NOT NULL,
    base_speed  REAL,
    stamina     REAL,
    loss_rate   REAL,                  -- Matched to your comment name
    odds        REAL NOT NULL          -- decimal odds, frozen at race creation
);                                     -- Added the missing semicolon here

-- players table      (id, name UNIQUE, token UNIQUE, balance)

CREATE TABLE IF NOT EXISTS players (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,
    token TEXT UNIQUE NOT NULL,
    balance REAL NOT NULL
);

-- bets table         (id, race_id, player_id, lane, amount,
--                           odds, settled DEFAULT 0)

CREATE TABLE IF NOT EXISTS bets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    race_id   INTEGER NOT NULL REFERENCES races(id),
    player_id INTEGER NOT NULL REFERENCES players(id),
    lane      INTEGER NOT NULL,       -- which horse (by lane)
    amount    REAL NOT NULL,
    odds      REAL NOT NULL,          -- odds AT BET TIME, copied — never recomputed
    settled   INTEGER DEFAULT 0
);
"""

def init_db():
    conn = get_db()
    conn.executescript(SCHEMA)
    conn.commit()
    conn.close()
