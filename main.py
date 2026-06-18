from models import *
from renderer import *

if __name__ == "__main__":

    # --- Track setup ---
    # Finish node slightly off the track, all lanes connect to this
    finish = TrackNode(15, 0, -1, "Finish")


    track = RaceTrack(
        finish_node=finish,
        lanes=8,
        straight_length=300,
        base_radius=100
    )


    # You'll see printed:
    # Total distance for lane: ~XXX m
    # for each lane at startup

    # --- Horses ---
    # base_speed in m/s, stamina in [0,100+], stamina_loss_per_meter controls fade
    h1 = Horse("Thunder", 20, 100, 0.05)   # solid, steady
    h2 = Horse("Blaze",   28, 100, 0.04)   # fast, good stamina
    h3 = Horse("Storm",   19, 100, 0.04)   # balanced
    h4 = Horse("Falcon",  21,  95, 0.06)   # bursty, burns stamina faster
    h5 = Horse("Shadow",  18, 110, 0.03)   # slower top speed, great stamina
    h6 = Horse("Comet",   23,  90, 0.05)   # strong sprinter
    h7 = Horse("Titan",   17, 120, 0.035)  # tanky grinder

    horses_with_lanes = [
        (h1, 0),
        (h2, 1),
        (h3, 2),
        (h4, 3),
        (h5, 4),
        (h6, 5),
        (h7, 6),
        # lane 7 free, or add an 8th horse later
    ]

    # --- Race config ---
    race = Race(
        horses_with_lanes=horses_with_lanes,
        track=track,
        tick_dt=0.5,    # gives A* enough horizon to overtake smartly
        total_time=12.0 # enough time for everyone to finish a ~120–170m lap
    )

    race.play()
