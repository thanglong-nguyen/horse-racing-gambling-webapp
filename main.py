from models import *
from renderer import *
from utilities import RaceRenderer

if __name__ == "__main__":

    # Finish node slightly off the track, all lanes connect to this
    finish = TrackNode(15, 0, -1, "Finish")


    track = RaceTrack(
        finish_node=finish,
        straight_length=100, #500m straight
        base_radius=20 #100m radius turns
    )


    # You'll see printed:
    # Total distance for lane: ~XXX m
    # for each lane at startup

    # --- Horses ---
    # base_speed in m/s, stamina in [0,100+], stamina_loss_per_meter controls fade
    h1 = Horse("Thunder", 22, 100, 0.05)   # solid, steady
    h2 = Horse("Blaze",   22, 100, 0.04)   # fast, good stamina
    h3 = Horse("Storm",   22, 100, 0.04)   # balanced
    h4 = Horse("Falcon",  22,  100, 0.06)   # bursty, burns stamina faster
    h5 = Horse("Shadow",  22, 110, 0.03)   # slower top speed, great stamina
    h6 = Horse("Comet",   22,  100, 1)   # strong sprinter
    h7 = Horse("Titan",   base_speed=22, stamina=90, stamina_loss_per_meter=0.04)  # tanky grinder

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
        tick_dt= 1,    # gives A* enough horizon to overtake smartly
        total_time= 1000 # enough time for everyone to finish a ~120–170m lap
    )

    # race.play()

    race.play_replay(0.5)


    # race.run()
    # RaceRenderer.plot_paths(race, track)
    
