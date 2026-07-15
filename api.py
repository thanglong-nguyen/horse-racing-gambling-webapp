import asyncio
import json
import random
import secrets
import time
import sqlite3
from contextlib import asynccontextmanager
from typing import Dict, List, Tuple

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, RootModel

from db import get_db, init_db
from models import Horse, Race, RaceTrack, TrackNode

# =====================================================================
# Response schemas (the API contract — response_model filters output,
# so anything not listed here never leaves the server)
# =====================================================================

class TrackResponse(RootModel):
    root: Dict[str, List[Tuple[float, float]]]

class RaceHorseInfo(BaseModel):
    lane: int
    name: str
    base_speed: float
    stamina: float
    loss_rate: float
    odds: float

class CurrentRaceResponse(BaseModel):
    race_id: int
    betting_closes_at: float
    seconds_left: float
    horses: List[RaceHorseInfo]

# =====================================================================
# Game configuration
# =====================================================================

BETTING_WINDOW = 30  # seconds
NUM_LANES = 8
HORSE_NAMES = ["Thunder", "Blaze", "Storm", "Falcon",
               "Shadow", "Comet", "Titan", "Vortex"]

# Immutable server-lifetime state (track geometry, outlines)
assets = {}

# =====================================================================
# Simulation helpers
# =====================================================================

def make_track(straight=100, radius=20, lanes=NUM_LANES):
    finish = TrackNode(15, 0, -1, "Finish")
    track = RaceTrack(finish_node=finish, straight_length=straight,
                      base_radius=radius, lanes=lanes)
    return track, finish

def generate_random_horses(rng: random.Random, num_lanes=NUM_LANES):
    """Roll horse stats from the given (per-race, seeded) RNG and
    shuffle them into lanes."""
    names_pool = rng.sample(HORSE_NAMES, min(num_lanes, len(HORSE_NAMES)))

    horses = []
    for name in names_pool:
        speed = round(rng.uniform(20.0, 24.0), 2)
        stamina = rng.randint(85, 115)
        loss_rate = round(rng.uniform(0.03, 0.07), 4)
        horses.append(Horse(name, speed, stamina, loss_rate))

    available_lanes = list(range(num_lanes))
    rng.shuffle(available_lanes)

    return list(zip(horses, available_lanes))

def seeded_run(horses_with_lanes: list, track) -> dict:
    """Run the full simulation and return the replay payload:
    decimated position history plus each horse's identity (name + lane)."""
    race = Race(track=track, horses_with_lanes=horses_with_lanes,
                tick_dt=1, total_time=1000)
    
    start_lane = {horse.name: lane for horse, lane in horses_with_lanes}

    race.run()

    results = []
    for horse_state in race.states:
        # Keep every 10th sample, but never drop the finish point
        slim = horse_state.history[::10]
        if slim[-1] != horse_state.history[-1]:
            slim.append(horse_state.history[-1])

        history = [[round(x, 2), round(y, 2), round(t, 3)] for x, y, t in slim]

        results.append({
            "horse": horse_state.horse.name,
            "lane": start_lane[horse_state.horse.name],   
            "final_time": horse_state.total_time,
            "history": history,
        })

    results.sort(key=lambda r: r["final_time"])
    return {"results": results}

def settle_race(race_id):
    pass  # Mission 5: pay out winning bets here

# =====================================================================
# Race lifecycle: betting -> locked -> revealed -> settled
# =====================================================================

# TODO startup sweep: void orphaned races, refund their bets.
async def race_scheduler():
    while True:
        # --- 1. create the next race, status='betting' ---
        seed = secrets.randbits(32)      # unpredictable token from OS entropy
        rng = random.Random(seed)        # isolated per-race generator

        track = assets["track"]
        horses = generate_random_horses(rng)
        closes_at = time.time() + BETTING_WINDOW

        conn = get_db()
        try:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO races (status, seed, betting_closes_at)
                VALUES ('betting', ?, ?)
            """, (seed, closes_at))
            race_id = cursor.lastrowid

            for horse, lane in horses:
                cursor.execute("""
                    INSERT INTO race_horses
                        (race_id, lane, name, base_speed, stamina, loss_rate, odds)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (race_id, lane, horse.name, horse.base_speed,
                      horse.stamina, horse.stamina_loss_per_meter, 5.0))
            conn.commit()
        finally:
            conn.close()

        print(f"[scheduler] race {race_id}: betting open for {BETTING_WINDOW}s")

        # --- 2. wait for the betting window to close ---
        await asyncio.sleep(max(0.0, closes_at - time.time()))

        # --- 3. lock (bets die here), simulate off-thread, reveal ---
        conn = get_db()
        try:
            conn.execute("UPDATE races SET status = 'locked' WHERE id = ?",
                         (race_id,))
            conn.commit()
        finally:
            conn.close()

        print(f"[scheduler] race {race_id}: locked, simulating...")

        result = await asyncio.to_thread(seeded_run, horses, track)

        conn = get_db()
        try:
            conn.execute("""
                UPDATE races
                SET result_json = ?, status = 'revealed'
                WHERE id = ?
            """, (json.dumps(result), race_id))
            conn.commit()
        finally:
            conn.close()

        print(f"[scheduler] race {race_id}: revealed")

        # --- 4. settle ---
        settle_race(race_id)

        conn = get_db()
        try:
            conn.execute("UPDATE races SET status = 'settled' WHERE id = ?",
                         (race_id,))
            conn.commit()
        finally:
            conn.close()

        print(f"[scheduler] race {race_id}: settled\n")

# =====================================================================
# App setup
# =====================================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    print("Initializing Database Schema...")
    init_db()

    print("Building RaceTrack...")
    track, finish = make_track()

    outlines = {
        str(lane): [[round(n.x, 1), round(n.y, 1)] for n in nodes[::20]]
        for lane, nodes in track.lane_lists.items()
    }

    assets["track"] = track
    assets["finish"] = finish
    assets["outlines"] = outlines

    scheduler_task = asyncio.create_task(race_scheduler())
    scheduler_task.add_done_callback(
        lambda t: print("[scheduler] DIED:", t.exception())
        if not t.cancelled() else None
    )

    yield

    scheduler_task.cancel()
    try:
        await scheduler_task
    except asyncio.CancelledError:
        print("[scheduler] shutdown complete")
    assets.clear()

app = FastAPI(lifespan=lifespan)
app.mount("/static", StaticFiles(directory="static", html=True), name="static")
app.add_middleware(GZipMiddleware, minimum_size=1000)

@app.middleware("http")
async def add_process_time_header(request: Request, call_next):
    start_time = time.time()
    response = await call_next(request)
    process_time = time.time() - start_time
    print(f"Request to {request.url.path} took {process_time:.4f} seconds.")
    return response

# =====================================================================
# Endpoints
# =====================================================================

@app.get("/track", response_model=TrackResponse)
async def get_track_endpoint():
    return assets["outlines"]

@app.get("/race/current", response_model=CurrentRaceResponse)
def get_current_race():
    conn = get_db()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, betting_closes_at
            FROM races
            WHERE status = 'betting'
            ORDER BY id DESC
            LIMIT 1
        """)
        newest_race = cursor.fetchone()

        if newest_race is None:
            raise HTTPException(status_code=404, detail="No race open for betting")

        now = time.time()
        closes_at = newest_race["betting_closes_at"]

        # Belt-and-braces: the scheduler may not have flipped status yet
        if closes_at <= now:
            raise HTTPException(status_code=404, detail="No race open for betting")

        race_id = newest_race["id"]

        cursor.execute("""
            SELECT lane, name, base_speed, stamina, loss_rate, odds
            FROM race_horses
            WHERE race_id = ?
            ORDER BY lane ASC
        """, (race_id,))

        horses = [dict(row) for row in cursor.fetchall()]

        return {
            "race_id": race_id,
            "betting_closes_at": closes_at,
            "seconds_left": round(max(0.0, closes_at - now), 2),
            "horses": horses,
        }
    finally:
        conn.close()

@app.get("/race/{race_id}/replay")
def get_replay(race_id: int):
    conn = get_db()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT status, result_json FROM races WHERE id = ?", (race_id,))
        race = cursor.fetchone()

        if race is None:
            raise HTTPException(status_code=404, detail="Race not found")

        if race["status"] not in ("revealed", "settled"):
            raise HTTPException(
                status_code=425,
                detail="Race results have not been revealed yet")

        if not race["result_json"]:
            raise HTTPException(
                status_code=500, detail="Race data is missing result JSON")

        return json.loads(race["result_json"])
    finally:
        conn.close()


STARTING_BALANCE = 1000.0

class PlayerCreate(BaseModel):
    name: str

class PlayerResponse(BaseModel):
    player_id: int
    name: str
    token: str        # returned ONCE, at creation. Never again.
    balance: float

@app.post("/player", response_model=PlayerResponse)
def create_player(body: PlayerCreate):
    token = secrets.token_hex(16)     # same module as race seeds — why not random?
    conn = get_db()
    try:
        # INSERT the player. Two things to handle:
        # 1. name is UNIQUE — a duplicate raises sqlite3.IntegrityError.
        #    Catch it and raise HTTPException(409, "Name taken").
        # 2. use cursor.lastrowid for player_id, like in the scheduler.
        
        # 1. Attempt to insert the new player using parameterized queries

        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO players (name, token, balance) VALUES (?, ?, ?)",
            (body.name, token, STARTING_BALANCE)
        )
        conn.commit()
        
        player_id = cursor.lastrowid
        
        return {
            "player_id": player_id,
            "name": body.name,
            "token": token,
            "balance" : STARTING_BALANCE
        }
        
    except sqlite3.IntegrityError:
        conn.rollback()
        raise HTTPException(status_code=409, detail="Name taken")
        
    finally:
        conn.close()

class BetRequest(BaseModel):
    token: str       
    race_id: int
    lane: int
    amount: float

@app.post("/bet")
def place_bet(body: BetRequest):
    conn = get_db()
    try:
        cursor = conn.cursor()

        # 1. amount > 0
        if body.amount <= 0:
            raise HTTPException(status_code=400, detail="Bet amount must be positive")

        # 2. token -> player row
        cursor.execute(
            "SELECT id FROM players WHERE token = ?",
            (body.token,)
        )
        row = cursor.fetchone()
        if row is None:
            raise HTTPException(status_code=401, detail="Unknown token")

        player_id = row[0]

        # 3. race exists AND status='betting' AND betting_closes_at > now
        cursor.execute(
            "SELECT status, betting_closes_at FROM races WHERE id = ?",
            (body.race_id,)
        )
        race = cursor.fetchone()
        if race is None:
            raise HTTPException(status_code=404, detail="Race does not exist")

        status, closes_at = race
        now = time.time()

        if status != "betting" or closes_at <= now:
            raise HTTPException(status_code=409, detail="Betting closed")

        # 4. lane exists in race_horses for this race
        cursor.execute(
            """
            SELECT odds
            FROM race_horses
            WHERE race_id = ? AND lane = ?
            """,
            (body.race_id, body.lane)
        )
        horse_row = cursor.fetchone()
        if horse_row is None:
            raise HTTPException(status_code=404, detail="Lane not found")

        odds = horse_row[0]

        # 5+6. Atomic balance check + subtraction
        try:
            cursor.execute(
                """
                UPDATE players
                SET balance = balance - ?
                WHERE id = ? AND balance >= ?
                """,
                (body.amount, player_id, body.amount)
            )

            if cursor.rowcount == 0:
                conn.rollback()
                raise HTTPException(status_code=402, detail="Insufficient funds")

            # Insert bet
            cursor.execute(
                """
                INSERT INTO bets (race_id, player_id, lane, amount, odds, settled)
                VALUES (?, ?, ?, ?, ?, 0)
                """,
                (body.race_id, player_id, body.lane, body.amount, odds)
            )

            bet_id = cursor.lastrowid

            conn.commit()

        except Exception:
            conn.rollback()
            raise

        # Re-select fresh balance (never trust stale Python-side value)
        cursor.execute("SELECT balance FROM players WHERE id = ?", (player_id,))
        new_balance = cursor.fetchone()[0]

        return {
            "bet_id": bet_id,
            "new_balance": new_balance
        }

    finally:
        conn.close()


