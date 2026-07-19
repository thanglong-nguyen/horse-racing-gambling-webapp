import asyncio
import json
import random
import secrets
import time
import sqlite3
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from typing import Dict, List, Tuple

from fastapi import FastAPI, HTTPException, Request, Header
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse, JSONResponse
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
    pool: float          # money (real bets + house seed) on this lane

class CurrentRaceResponse(BaseModel):
    race_id: int
    betting_closes_at: float
    seconds_left: float
    total_pool: float    # whole pool (real + seed) — lets the client
    house_edge: float    # project "your payout" for any stake honestly
    horses: List[RaceHorseInfo]

class PlayerBalanceResponse(BaseModel):
    name: str
    balance: float

# =====================================================================
# Game configuration
# =====================================================================

BETTING_WINDOW = 30  # seconds
NUM_LANES = 8
HORSE_NAMES = ["Thunder", "Blaze", "Storm", "Falcon",
               "Shadow", "Comet", "Titan", "Vortex"]

STARTING_BALANCE = 1000.0

# --- odds / pool configuration ---
HOUSE_EDGE = 0.15        # the house's cut of the pool
TOTAL_SEED = 400.0       # phantom house money, spread across the field
                         # by estimated strength — PRICING ONLY, never paid out
MIN_ODDS = 1.01
MIN_BET = 1.0
LANE_WEIGHT = 0.7        # score penalty per lane out from the rail.
                         # PROVISIONAL — calibrate with quant.py (measure
                         # real win rates per lane over N simulated races)

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

# =====================================================================
# Odds: pari-mutuel pricing
#
# Displayed odds are APPROXIMATE — a live projection of what the pool
# would pay right now. Actual payouts are computed at settlement from
# the final pool (true pari-mutuel), which makes pool-manipulation
# attacks unprofitable by construction: inflating the pool to juice a
# lane's odds just dilutes your own share of it.
# =====================================================================

def estimate_strengths(horses):
    """horses: rows with lane, base_speed, stamina, loss_rate.
    Returns {lane: probability}, summing to 1.0. The house's opening
    handicap — used only to spread the phantom seed liquidity."""
    scores = {}
    for row in horses:
        lane = row["lane"]
        score = (
            row["base_speed"]
            + 0.05 * row["stamina"]
            - 100 * row["loss_rate"]
            - LANE_WEIGHT * lane      # inner lanes run a shorter loop
        )
        scores[lane] = score

    # Raw scores sit in a narrow band; raising to a power stretches
    # the gaps so favorites/longshots separate.
    k = 12
    powered = {lane: (score ** k) for lane, score in scores.items()}
    total = sum(powered.values())

    return {lane: powered[lane] / total for lane in powered}

def get_pool(cursor, race_id):
    """The single source of pool truth: (grand_total, {lane: lane_money}),
    where lane_money = real bets on the lane + the house's phantom seed
    share. Used by pricing, the /race/current endpoint, and settlement."""
    # 1. Load stats -> house handicap
    cursor.execute(
        """
        SELECT lane, base_speed, stamina, loss_rate
        FROM race_horses
        WHERE race_id = ?
        """,
        (race_id,)
    )
    horses = cursor.fetchall()
    strengths = estimate_strengths(horses)

    # 2. Real money per lane
    cursor.execute(
        """
        SELECT lane, SUM(amount)
        FROM bets
        WHERE race_id = ?
        GROUP BY lane
        """,
        (race_id,)
    )
    real_money = {row[0]: row[1] for row in cursor.fetchall()}
    for row in horses:
        real_money.setdefault(row["lane"], 0.0)

    # 3. Totals (real + phantom seed)
    grand_total = sum(real_money.values()) + TOTAL_SEED
    lane_money = {
        row["lane"]: real_money[row["lane"]] + TOTAL_SEED * strengths[row["lane"]]
        for row in horses
    }
    return grand_total, lane_money

def recompute_odds(cursor, race_id):
    """Reprice all lanes from stats + betting pool.
    Runs inside the caller's transaction — NO commit in here."""
    grand_total, lane_money = get_pool(cursor, race_id)

    for lane, money in lane_money.items():
        odds = grand_total * (1 - HOUSE_EDGE) / money
        odds = max(MIN_ODDS, round(odds, 2))

        cursor.execute(
            """
            UPDATE race_horses
            SET odds = ?
            WHERE race_id = ? AND lane = ?
            """,
            (odds, race_id, lane)
        )

# =====================================================================
# Race lifecycle: betting -> locked -> revealed -> settled
# =====================================================================

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

            recompute_odds(cursor, race_id)   # opening line overwrites the 5.0

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
        await asyncio.to_thread(settle_race, race_id, result["results"][0]["lane"])

        REPLAY_BUFFER = 8.0   # results-viewing pause, a touch more than the client's 5s

        replay_duration = result["results"][-1]["final_time"]
        print(f"[scheduler] race {race_id}: replay window {replay_duration:.0f}s")
        await asyncio.sleep(replay_duration + REPLAY_BUFFER)

        conn = get_db()
        try:
            cursor = conn.cursor()
            prune_old_races(cursor, race_id)
            cursor.execute("UPDATE races SET status = 'settled' WHERE id = ?",
                           (race_id,))
            conn.commit()   # prune + status flip land together
        finally:
            conn.close()

        print(f"[scheduler] race {race_id}: settled\n")

def settle_race(race_id, winning_lane):
    """Pari-mutuel settlement with the house in the pool.

    The house's phantom seed bets are treated as REAL at settlement:
    the payout pool is (real bets + TOTAL_SEED) minus the edge, and the
    winning side includes the house's seed share on that lane. This
    makes the board formula and the settlement formula identical, so
    the displayed odds ARE the current projected payout — honest by
    construction.

    Per-bet frozen odds are still ignored at payout (receipt only), so
    pool manipulation stays unprofitable: pumping money into losing
    lanes grows a pool your own late bet must win back from its own
    diluted share. The house pays winners from its pocket when real
    money is thin (bounded by ~TOTAL_SEED per race); the 15% edge
    covers that long-run, provided estimate_strengths stays calibrated."""
    conn = get_db()
    try:
        cursor = conn.cursor()

        # Same pool the board priced from — display and payout must agree
        grand_pool, lane_money = get_pool(cursor, race_id)
        payout_pool = grand_pool * (1 - HOUSE_EDGE)
        # Winning side = real money on the lane + the house's seed bet
        # on it. The house's own share of the winnings simply stays with
        # the house — no row to update.
        winner_money = lane_money[winning_lane]

        cursor.execute(
            """
            SELECT id, player_id, lane, amount
            FROM bets
            WHERE race_id = ? AND settled = 0
            """,
            (race_id,)
        )
        bets = cursor.fetchall()

        for bet in bets:
            if bet["lane"] != winning_lane:
                continue
            share = bet["amount"] / winner_money * payout_pool
            # Minimum-payoff rule (real tracks have one too): a winning
            # bet never pays less than the stake back. Kicks in only if
            # >85% of the grand pool sits on the winner.
            payout = round(max(bet["amount"], share), 2)
            cursor.execute(
                """
                UPDATE players
                SET balance = balance + ?
                WHERE id = ?
                """,
                (payout, bet["player_id"])
            )

        cursor.execute(
            """
            UPDATE bets
            SET settled = 1
            WHERE race_id = ? AND settled = 0
            """,
            (race_id,)
        )

        conn.commit()      # ONE commit for the entire settlement
        print(f"[settle] race {race_id}: lane {winning_lane} won, "
              f"grand pool {grand_pool:.2f}, "
              f"winner side {winner_money:.2f}")
    finally:
        conn.close()

def sweep_orphan_races():
    """Refund unsettled bets on races that never reached 'settled'.
    Runs once at startup, before the scheduler creates new races."""
    conn = get_db()
    try:
        cursor = conn.cursor()

        # 1. Find orphaned races (not settled, not voided)
        cursor.execute(
            """
            SELECT id
            FROM races
            WHERE status NOT IN ('settled', 'void')
            """
        )
        orphans = [row[0] for row in cursor.fetchall()]

        for race_id in orphans:
            # 2. Fetch all unsettled bets for this race
            cursor.execute(
                """
                SELECT id, player_id, amount
                FROM bets
                WHERE race_id = ? AND settled = 0
                """,
                (race_id,)
            )
            bets = cursor.fetchall()

            # 3. Refund each bet (stake returned, no winnings — the race
            #    never happened)
            for bet_id, player_id, amount in bets:
                cursor.execute(
                    """
                    UPDATE players
                    SET balance = balance + ?
                    WHERE id = ?
                    """,
                    (amount, player_id)
                )

            # 4. Mark all bets as settled
            cursor.execute(
                """
                UPDATE bets
                SET settled = 1
                WHERE race_id = ? AND settled = 0
                """,
                (race_id,)
            )

            # 5. Mark race as void
            cursor.execute(
                """
                UPDATE races
                SET status = 'void'
                WHERE id = ?
                """,
                (race_id,)
            )

        conn.commit()   # one transaction: a crash mid-sweep rolls back clean
        print(f"[sweep] voided {len(orphans)} orphan race(s)")
    finally:
        conn.close()

KEEP_RACES = 50

def prune_old_races(cursor, current_race_id):
    """Drop ancient races and their child rows. Settled bets have already
    paid out — player balances are the surviving record."""
    cutoff = current_race_id - KEEP_RACES
    cursor.execute("DELETE FROM bets        WHERE race_id < ?", (cutoff,))
    cursor.execute("DELETE FROM race_horses WHERE race_id < ?", (cutoff,))
    cursor.execute("DELETE FROM races       WHERE id      < ?", (cutoff,))

# =====================================================================
# App setup
# =====================================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    print("Initializing Database Schema...")
    init_db()
    sweep_orphan_races()

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
# Rate limiting — sliding window per IP, in memory (fine for one worker;
# resets on redeploy, same as the rest of the free-tier state)
# =====================================================================

RATE_LIMITS = {              # path -> (max requests, window seconds)
    "/player": (5, 3600),    # signups are the scriptable endpoint
    "/bet":    (20, 60),     # no human bets 20 times a minute
}
_hits = defaultdict(deque)   # (ip, path) -> timestamps of recent requests

@app.middleware("http")
async def rate_limit(request: Request, call_next):
    limit = RATE_LIMITS.get(request.url.path)
    if limit and request.method == "POST":
        max_req, window = limit
        # Behind Render's proxy, client.host is the proxy itself —
        # the real visitor is the first entry of X-Forwarded-For.
        ip = (request.headers.get("x-forwarded-for")
              or request.client.host).split(",")[0].strip()

        now = time.time()
        q = _hits[(ip, request.url.path)]
        while q and q[0] < now - window:   # forget requests outside the window
            q.popleft()

        if len(q) >= max_req:
            # Return (not raise): exceptions in middleware bypass the
            # normal HTTPException handling.
            return JSONResponse(status_code=429,
                                content={"detail": "Too many requests, slow down"})
        q.append(now)

    return await call_next(request)

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

        # Pool sizes let the client project "your payout" for any stake
        # with the same formula settlement uses. Real tote boards show
        # pool totals too — this is disclosure, not a leak.
        grand_total, lane_money = get_pool(cursor, race_id)
        for horse in horses:
            horse["pool"] = round(lane_money[horse["lane"]], 2)

        return {
            "race_id": race_id,
            "betting_closes_at": closes_at,
            "seconds_left": round(max(0.0, closes_at - now), 2),
            "total_pool": round(grand_total, 2),
            "house_edge": HOUSE_EDGE,
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

class PlayerCreate(BaseModel):
    name: str

class PlayerResponse(BaseModel):
    player_id: int
    name: str
    token: str        # returned ONCE, at creation. Never again.
    balance: float

@app.post("/player", response_model=PlayerResponse)
def create_player(body: PlayerCreate):
    token = secrets.token_hex(16)
    conn = get_db()
    try:
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
            "balance": STARTING_BALANCE,
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

        # 1. amount sane (the minimum itself must pass)
        if body.amount < MIN_BET:
            raise HTTPException(status_code=400, detail="Minimum bet is $1")

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

        # Recorded for the player's receipt only — the board price at
        # bet time. Settlement is pari-mutuel and ignores this.
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

            cursor.execute(
                """
                INSERT INTO bets (race_id, player_id, lane, amount, odds, settled)
                VALUES (?, ?, ?, ?, ?, 0)
                """,
                (body.race_id, player_id, body.lane, body.amount, odds)
            )

            bet_id = cursor.lastrowid

            recompute_odds(cursor, body.race_id)   # reprice the board

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

@app.get("/player/balance", response_model=PlayerBalanceResponse)
def get_me(authorization: str = Header(None)):
    # --- check 1: header exists and uses the Bearer scheme ---
    if authorization is None or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or malformed token")

    token = authorization.removeprefix("Bearer ")

    # --- check 2: token maps to a real player ---
    conn = get_db()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT name, balance FROM players WHERE token = ?",
            (token,)
        )
        row = cursor.fetchone()
        if row is None:
            raise HTTPException(status_code=401, detail="Unknown token")

        return {"name": row["name"], "balance": row["balance"]}
    finally:
        conn.close()


@app.get("/")
def root():
    return RedirectResponse(url="/static/")