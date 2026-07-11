import random
import time
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from models import (
    Horse, TrackNode, RaceTrack, Race
)

# State container for the immutable track
ml_models = {}

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
    # 1. Generate horses with randomized stats
    horses = []
    names_pool = random.sample(HORSE_NAMES, min(num_lanes, len(HORSE_NAMES)))
    
    for name in names_pool:
        # Define your specific attribute ranges here
        speed = round(random.uniform(20.0, 24.0), 2)          # e.g., 20m/s to 24m/s
        stamina = random.randint(85, 115)                     # e.g., 85 to 115 stamina points
        loss_rate = round(random.uniform(0.03, 0.07), 4)      # e.g., 0.03 to 0.07 loss/meter
        
        horses.append(Horse(name, speed, stamina, loss_rate))
    
    # 2. Assign horses to lanes completely randomly
    # Creates a list of available lanes [0, 1, 2, ..., num_lanes-1] and shuffles it
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

    ml_models["track"] = track
    ml_models["finish"] = finish
    
    yield
    # FIXED: Clean up ONLY happens on shutdown now. 
    # Moving this below 'yield' keeps ml_models populated during runtime.
    ml_models.clear()

app = FastAPI(lifespan=lifespan)

# Middleware to measure "before and after" automatically
@app.middleware("http")
async def add_process_time_header(request: Request, call_next):
    start_time = time.time()
    response = await call_next(request)
    process_time = time.time() - start_time
    print(f"Request to {request.url.path} took {process_time:.4f} seconds.")
    return response


@app.post("/race")
async def run_race_endpoint():
    # Retrieve the globally cached track
    track = ml_models["track"]
    
    # Dynamic: Every single request now gets unique horse stats and lane setups
    horses_with_lanes = generate_random_horses(num_lanes=8)

    race = Race(track=track, horses_with_lanes=horses_with_lanes, tick_dt=1, total_time=1000)
    race.run()

    results = []
    for horse_state in race.states:
        results.append({
            "horse": horse_state.horse.name,
            "final_time": horse_state.total_time
        })

    results.sort(key=lambda x: x["final_time"])  # Sort by final time for ranking

    return {"placements": results}
