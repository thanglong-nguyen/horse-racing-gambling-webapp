import random, time, asyncio, json, secrets
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, HTTPException
from fastapi.staticfiles import StaticFiles 
from fastapi.middleware.gzip import GZipMiddleware
from db import get_db, init_db
from models import (
    Horse, TrackNode, RaceTrack, Race
)

# 1. Import RootModel alongside standard Pydantic tools
from pydantic import BaseModel, RootModel
from typing import List, Tuple, Dict

# --- Pydantic Validation Schemas ---

class TrackResponse(RootModel):
    root: Dict[str, List[Tuple[float, float]]]

class HorseResult(BaseModel):
    horse: str
    final_time: float
    history: List[Tuple[float, float, float]] 

class RaceResponse(BaseModel):
    results: List[HorseResult]

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

# State container for the immutable track
assets = {}

def make_track(straight=100, radius=20, lanes=8):
    finish = TrackNode(15, 0, -1, "Finish")
    track = RaceTrack(finish_node=finish, straight_length=straight,
                      base_radius=radius, lanes=lanes)
    return track, finish

# Define horse names to pick from
HORSE_NAMES = ["Thunder", "Blaze", "Storm", "Falcon", "Shadow", "Comet", "Titan", "Vortex"]

def generate_random_horses(rng: random.Random, num_lanes=8):
    """
    Generates dynamic horses with stats inside random ranges 
    and randomly assigns them to available track lanes.
    """
    horses = []

    names_pool = rng.sample(HORSE_NAMES, min(num_lanes, len(HORSE_NAMES)))
    
    for name in names_pool:
        speed = round(rng.uniform(20.0, 24.0), 2)          
        stamina = rng.randint(85, 115)                     
        loss_rate = round(rng.uniform(0.03, 0.07), 4)      
        
        horses.append(Horse(name, speed, stamina, loss_rate))
    
    available_lanes = list(range(num_lanes))
    rng.shuffle(available_lanes)
    
    horses_with_lanes = []
    for horse, lane in zip(horses, available_lanes):
        horses_with_lanes.append((horse, lane))
        
    return horses_with_lanes

def seeded_run(horses_with_lanes: list, track) -> dict:
    
    race = Race(track=track, horses_with_lanes=horses_with_lanes, tick_dt=1, total_time=1000)
    race.run()

    results = []
    for horse_state in race.states:
        slim = horse_state.history[::10]

        if slim[-1] != horse_state.history[-1]:
            slim.append(horse_state.history[-1])
            
        history = [[round(x, 2), round(y, 2), round(t, 3)] for x, y, t in slim]

        results.append({
            "horse": horse_state.horse.name,    
            "final_time": horse_state.total_time,
            "history": history
        })

    results.sort(key=lambda x: x["final_time"])  
    return {"results": results}

def settle_race(race_id):
    pass


BETTING_WINDOW = 60  # seconds

# TODO startup sweep: void orphaned races, refund their bets.
async def race_scheduler():
    while True:
        # --- 1. create the next race, status='betting' ---
        seed = secrets.randbits(32)              # Unpredictable token from OS
        rng = random.Random(seed)                # Isolated private generator

        track = assets["track"]
        horses = generate_random_horses(rng, num_lanes=8)

        closes_at = time.time() + BETTING_WINDOW

        conn = get_db()

        try:
            cursor = conn.cursor()
            
            # Insert the race row
            cursor.execute("""
                INSERT INTO races (status, seed, betting_closes_at)
                VALUES ('betting', ?, ?)
            """, (seed, closes_at))
            
            race_id = cursor.lastrowid

            # Insert one race_horses row per horse
            for horse, lane in horses:
                cursor.execute("""
                    INSERT INTO race_horses (race_id, lane, name, base_speed, stamina, loss_rate, odds)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (
                    race_id, 
                    lane, 
                    horse.name, 
                    horse.base_speed, 
                    horse.stamina, 
                    horse.stamina_loss_per_meter, 
                    5.0
                ))
            
            conn.commit()
        finally:
            conn.close()

        print(f"[scheduler] race {race_id}: betting open for {BETTING_WINDOW}s")

        # --- 2. wait for the window to close ---
        await asyncio.sleep(max(0.0, closes_at - time.time()))

        # --- 3. lock, simulate, reveal ---
        conn = get_db()
        try:
            # UPDATE status -> 'locked' (bets die HERE)
            conn.execute("UPDATE races SET status = 'locked' WHERE id = ?", (race_id,))
            conn.commit()
        finally:
            conn.close()

        print(f"[scheduler] race {race_id}: locked, simulating...")

        # Run CPU-bound race simulation inside a thread pool to avoid blocking the main async thread
        result = await asyncio.to_thread(seeded_run, horses, track)

        conn = get_db()
        try:
            # UPDATE the race with the json payload and reveal status
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
        settle_race(race_id)     # write it as a stub: def settle_race(rid): pass

        conn = get_db()
        try:
            # Final status update -> 'settled'
            conn.execute("UPDATE races SET status = 'settled' WHERE id = ?", (race_id,))
            conn.commit()
        finally:
            conn.close()

        print(f"[scheduler] race {race_id}: settled\n")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Built once when the server starts
    print("Initializing Database Schema...")
    init_db()

    print("Building RaceTrack...")
    track, finish = make_track(lanes=8)

    outlines = {
        str(lane): [[round(n.x, 1), round(n.y, 1)] for n in nodes[::20]]
        for lane, nodes in track.lane_lists.items()
    }

    assets["track"] = track
    assets["finish"] = finish
    assets["outlines"] = outlines

    scheduler_task = asyncio.create_task(race_scheduler())
    scheduler_task.add_done_callback(
    lambda t: print("[scheduler] DIED:", t.exception()) if not t.cancelled() else None
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

# Middleware to measure "before and after" automatically
@app.middleware("http")
async def add_process_time_header(request: Request, call_next):
    start_time = time.time()
    response = await call_next(request)
    process_time = time.time() - start_time
    print(f"Request to {request.url.path} took {process_time:.4f} seconds.")
    return response


@app.get("/track", response_model=TrackResponse)
async def get_track_endpoint():
    return assets["outlines"]

@app.post("/race", response_model=RaceResponse)
def run_race_endpoint():
    track = assets["track"]
    
    # Generate an isolated seed and generator instance here too
    seed = secrets.randbits(32)
    rng = random.Random(seed)
    
    horses_with_lanes = generate_random_horses(rng, num_lanes=8)
    return seeded_run(horses_with_lanes, track)
