import random
import time
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles 
from fastapi.middleware.gzip import GZipMiddleware
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

# State container for the immutable track
assets = {}

def make_track(straight=100, radius=20, lanes=8):
    finish = TrackNode(15, 0, -1, "Finish")
    track = RaceTrack(finish_node=finish, straight_length=straight,
                      base_radius=radius, lanes=lanes)
    return track, finish

# Define horse names to pick from
HORSE_NAMES = ["Thunder", "Blaze", "Storm", "Falcon", "Shadow", "Comet", "Titan", "Vortex"]

def generate_random_horses(num_lanes=8):
    """
    Generates dynamic horses with stats inside random ranges 
    and randomly assigns them to available track lanes.
    """
    horses = []
    names_pool = random.sample(HORSE_NAMES, min(num_lanes, len(HORSE_NAMES)))
    
    for name in names_pool:
        speed = round(random.uniform(20.0, 24.0), 2)          
        stamina = random.randint(85, 115)                     
        loss_rate = round(random.uniform(0.03, 0.07), 4)      
        
        horses.append(Horse(name, speed, stamina, loss_rate))
    
    available_lanes = list(range(num_lanes))
    random.shuffle(available_lanes)
    
    horses_with_lanes = []
    for horse, lane in zip(horses, available_lanes):
        horses_with_lanes.append((horse, lane))
        
    return horses_with_lanes

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Built once when the server starts
    print("Building RaceTrack...")
    track, finish = make_track(lanes=8)

    outlines = {
        str(lane): [[round(n.x, 1), round(n.y, 1)] for n in nodes[::20]]
        for lane, nodes in track.lane_lists.items()
    }

    assets["track"] = track
    assets["finish"] = finish
    assets["outlines"] = outlines

    yield
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
    
    horses_with_lanes = generate_random_horses(num_lanes=8)

    race = Race(track=track, horses_with_lanes=horses_with_lanes, tick_dt=1, total_time=1000)
    race.run()

    results = []
    for horse_state in race.states:
        # deal with DNF horses later if needed, for now just include their history.

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
