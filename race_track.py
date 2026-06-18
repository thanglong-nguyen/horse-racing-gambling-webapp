import matplotlib.pyplot as plt
from Stamina import *
import math
import heapq

def a_star(
    start_node,
    end_node,
    track,
    starting_speed=20,
    starting_stamina=100,
    stamina_loss_per_meter=0.05,
    max_time=1.2,                 # per-tick time budget
    blocked_map=None,
    max_speed_for_heuristic=25.0, # per-horse max speed for heuristic
    initial_total_time=0.0        # total race time BEFORE this tick
):
    """
    Returns:
        path:              list of (x, y)
        tick_time_used:    time spent moving in THIS tick      (<= max_time)
        total_time:        initial_total_time + tick_time_used
        final_node:        node at end of this tick's movement
        final_speed:       speed at that node
        final_stamina:     stamina at that node
    """

    # g_score = time travelled (seconds) within THIS TICK
    best_times = {}     # (node, speed, stamina_state) -> best time_so_far (this tick)
    f_scores = {}       # same key -> f = g + h
    prev = {}           # for path reconstruction

    # Track the best partial progress towards goal (smallest h)
    best_heuristic = float("inf")
    best_key = None
    best_speed = starting_speed
    best_stamina = starting_stamina

    # ---- Initial state ----
    init_key = (start_node, round(starting_speed, 1), get_stamina_state(starting_stamina))
    init_h = heuristic(start_node, track, max_speed_for_heuristic)
    init_f = init_h

    f_scores[init_key] = init_f
    best_times[init_key] = 0.0     # time_so_far THIS TICK
    prev[init_key] = None

    # Heap: (f_score, time_so_far_this_tick, node, -speed, -stamina)
    heap = [(init_f, 0.0, start_node, -starting_speed, -starting_stamina)]

    while heap:
        f_score, time_so_far, node, neg_speed, neg_stamina = heapq.heappop(heap)

        current_speed = -neg_speed
        current_stamina = -neg_stamina
        key = (node, round(current_speed, 1), get_stamina_state(current_stamina))

        # Skip stale queue entries
        if f_scores.get(key, float("inf")) < f_score:
            continue

        # Update best partial progress (smallest heuristic so far)
        h_here = heuristic(node, track, max_speed_for_heuristic)
        if h_here < best_heuristic:
            best_heuristic = h_here
            best_key = key
            best_speed = current_speed
            best_stamina = current_stamina

        # ---- Goal reached within this tick budget ----
        if node == end_node:
            tick_time_used = time_so_far
            total_time = initial_total_time + tick_time_used
            return (
                reconstruct_path(prev, key),
                tick_time_used,
                total_time,
                node,
                current_speed,
                current_stamina
            )

        # ---- Budget check: if we've hit/exceeded it, don't expand further ----
        if time_so_far >= max_time:
            continue

        # ---- Expand neighbors ----
        ray_neighbors = compute_ray_neighbors(track, node, current_speed)
        neighbors = list(node.adjacent) + ray_neighbors

        for neighbor, distance in neighbors:

            #  BLOCKING CHECK
            if blocked_map is not None and neighbor is not end_node:
                lane = neighbor.lane
                if lane in track.lane_index:
                    idx = track.lane_index[lane].get(neighbor)
                    if idx is not None and blocked_map[lane][idx]:
                        continue

            # ---- TIME cost for this edge ----
            effective_speed = max(current_speed, 6.0)  # guard against too-low speeds
            edge_time = distance / effective_speed
            new_time = time_so_far + edge_time

            # Respect per-tick budget
            if new_time > max_time:
                continue

            # ---- Update stamina / speed using DISTANCE ----
            new_stamina = round(
                max(0.0, current_stamina - distance * stamina_loss_per_meter), 1
            )
            new_speed = round(decay_speed(current_speed, new_stamina), 1)

            new_key = (neighbor, new_speed, get_stamina_state(new_stamina))

            # Time-based heuristic
            h_score = heuristic(neighbor, track, max_speed_for_heuristic)
            new_f_score = new_time + h_score

            # Standard A* relaxation
            if new_key not in f_scores or new_f_score < f_scores[new_key]:
                f_scores[new_key] = new_f_score
                best_times[new_key] = new_time
                prev[new_key] = key
                heapq.heappush(
                    heap,
                    (new_f_score, new_time, neighbor, -new_speed, -new_stamina)
                )

    # ---- Return best partial path if goal unreachable within this tick ----
    if best_key is not None:
        print("Goal not reached within tick budget; returning best partial path.")
        tick_time_used = best_times[best_key]
        total_time = initial_total_time + tick_time_used
        return (
            reconstruct_path(prev, best_key),
            tick_time_used,
            total_time,
            best_key[0],
            best_speed,
            best_stamina
        )

    # No progress at all (edge case)
    return None, 0.0, initial_total_time, start_node, starting_speed, starting_stamina





def heuristic(node, track, max_speed):
    """
    Admissible time heuristic:
      h(n) = remaining_distance / max_speed

    max_speed should be >= the horse's actual possible speed.
    """
    # Finish node
    if node.lane == -1:
        return 0.0

    total_dist = track.total_distances[node.lane]
    remaining_dist = max(0.0, total_dist - node.distance_from_start)

    # Time = distance / speed, use a global upper bound on speed
    return remaining_dist / max_speed

def reconstruct_path(prev, end_key):
    path = []
    current_key = end_key
    while current_key is not None:
        path.append(current_key[0].get_coordinates())
        current_key = prev.get(current_key)
    path.reverse()
    return path

def find_nearest_node(track, lane, x, y, tolerance=0.5):
    min_dist = float('inf')
    nearest = None
    cell_x, cell_y = int(x), int(y)
    for dx in [-1, 0, 1]:
        for dy in [-1, 0, 1]:
            cell = (cell_x + dx, cell_y + dy)
            if cell in track.node_grid:
                for node in track.node_grid[cell]:
                    if node.lane == lane:
                        dist = math.hypot(node.x - x, node.y - y)
                        if dist < min_dist and dist <= tolerance:
                            min_dist = dist
                            nearest = node
    return nearest if min_dist <= tolerance else None

def compute_ray_neighbors(track, current_node, current_speed,
                          max_distance=5.0, step_size=0.5):
    """
    Compute possible lane-change neighbours for this node & speed.

    Returns:
        list[(neighbor_node, distance_cost)]
    """
    ray_neighbors = []

    # Adjust speed on curves (tighter turning at same nominal speed)
    speed_for_angle = current_speed * (0.7 if current_node.position == "Curve" else 1.0)
    current_lane = current_node.lane

    for direction in [-1, 1]:  # -1 = left, +1 = right (lane index change)
        target_lane = current_lane + direction
        if target_lane not in track.nodes:
            continue

        for (low, high), angle in track.SPEED_ANGLE_BUCKETS.items():
            if not (low <= speed_for_angle < high):
                continue

            # Start ray from current node
            x, y = current_node.x, current_node.y
            ray_angle = -angle * direction  # left/right tilt
            ray_travel = 0.0

            # March along the ray
            while ray_travel <= max_distance:
                x += math.cos(ray_angle) * step_size
                y += math.sin(ray_angle) * step_size
                ray_travel += step_size

                target_node = find_nearest_node(track, target_lane, x, y)
                if target_node and abs(target_node.y - current_node.y) <= track.lane_width * 2:
                    # True geometric distance from current_node to the crossing node
                    dx = target_node.x - current_node.x
                    dy = target_node.y - current_node.y
                    true_dist = math.hypot(dx, dy)

                    # (Optional) add lane-change penalty so big jumps are more expensive
                    lane_delta = abs(target_lane - current_lane)
                    true_dist *= (1.0 + lane_delta * 0.2)  # tweak 0.2 if you like

                    ray_neighbors.append((target_node, round(true_dist, 2)))
                    break  # stop this ray once we found a valid crossing

    return ray_neighbors



def decay_speed(speed, stamina):
    stamina = max(0.0, min(100.0, stamina))
    stamina_lost = 100 - stamina
    decay_multiplier = 1.0 - (stamina_lost / 100) * 0.001
    min_speed = 5.0 + (stamina / 100) * 7.0
    return max(speed * decay_multiplier, min_speed)
