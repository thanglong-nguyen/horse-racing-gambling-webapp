import math
import matplotlib.pyplot as plt

# ---- import your real implementations here ----
from race_track import a_star          # your a_star
from models import RaceTrack, TrackNode   # or wherever these live

def run_single_astar_test():
    # 1. Build track & super sink
    super_sink = TrackNode(15, 0, -1, "Finish")
    track = RaceTrack(
        finish_node=super_sink,
        straight_length=30,
        base_radius=10,
        resolution=40,
        lanes=8,
        lane_width=1.0
    )

    # 2. Pick a single starting lane to test
    lane_idx = 0     # try 0 (inside), then others if you want
    start_node = track.start_nodes[lane_idx]

    # 3. Run A* with a big enough max_time so it can finish in one go
    path, distance, final_node, final_speed, final_stamina = a_star(
        start_node,
        track.finish_node,
        track,
        starting_speed=20,
        starting_stamina=100,
        stamina_loss_per_meter=0.05,
        max_time=10.0,     # allow full lap
    )

    # 4. Print debug info
    if not path:
        print("No path returned by A*.")
        return

    print(f"\n=== A* TEST RESULT ===")
    print(f"Total path points: {len(path)}")
    print(f"Total distance: {distance:.2f} m")
    print(f"Final node: {final_node}")
    print(f"Final speed: {final_speed:.2f} m/s")
    print(f"Final stamina: {final_stamina:.2f}")

    # Extract lanes from the path by mapping (x,y) back to nodes
    lane_sequence = []
    for (x, y) in path:
        # Find any node with those exact coords
        found_lane = None
        for lane_id, lane_nodes in track.nodes.items():
            if (x, y) in lane_nodes:
                found_lane = lane_nodes[(x, y)].lane
                break
        lane_sequence.append(found_lane)

    unique_lanes = sorted(set(l for l in lane_sequence if l is not None))
    print(f"Lanes visited (unique): {unique_lanes}")
    print(f"First 40 lane samples along path: {lane_sequence[:40]}")

    # 5. Plot: base lanes + A* path
    plt.figure(figsize=(12, 8))

    # draw all lanes in grey
    for lane_id, lane_nodes in track.nodes.items():
        xs = [x for (x, y) in lane_nodes.keys()]
        ys = [y for (x, y) in lane_nodes.keys()]
        plt.plot(xs, ys, alpha=0.2, linewidth=1, color="black")

    # draw the A* path in red
    path_x = [p[0] for p in path]
    path_y = [p[1] for p in path]
    plt.plot(path_x, path_y, "-o", color="red", linewidth=2, markersize=3, label="A* path")

    # mark start & finish
    plt.scatter([path_x[0]], [path_y[0]], color="green", s=80, label="Start")
    plt.scatter([super_sink.x], [super_sink.y], color="blue", s=120, label="Finish")

    plt.title(f"A* Path from lane {lane_idx}")
    plt.xlabel("X (m)")
    plt.ylabel("Y (m)")
    plt.axis("equal")
    plt.grid(True)
    plt.legend()
    plt.show()


if __name__ == "__main__":
    run_single_astar_test()
