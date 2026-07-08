import matplotlib.pyplot as plt
from Stamina import *
import math
import heapq

def a_star(
    start_node,
    end_node,
    track,
    base_speed,
    starting_speed,
    base_stamina,
    starting_stamina,
    stamina_loss_per_meter,
    max_time=1.2,
    initial_total_time=0.0,
    disabled_lanes=None,
    reservations=None,
    body_length=2.4
):
    if disabled_lanes is None:
        disabled_lanes = {}
    if reservations is None:
        reservations = {}

    best_times = {}
    f_scores = {}
    prev = {}

    best_remaining = float("inf")
    # best_heuristic = float("inf")
    best_speed = starting_speed
    best_stamina = starting_stamina

    init_key = (start_node, round(starting_speed, 1), get_stamina_state(starting_stamina))
    init_h = heuristic(start_node, track, starting_speed, reservations)
    init_f = init_h

    f_scores[init_key] = init_f
    best_times[init_key] = 0.0
    prev[init_key] = None

    heap = [(init_f, 0.0, start_node, -starting_speed, -starting_stamina)]

    while heap:
        f_score, time_so_far, node, neg_speed, neg_stamina = heapq.heappop(heap)

        current_speed = -neg_speed
        current_stamina = -neg_stamina
        key = (node, round(current_speed, 1), get_stamina_state(current_stamina))

        if f_scores.get(key, float("inf")) < f_score:
            continue

        # Track the physically furthest-along node reached within the time
        # budget. Raw remaining distance, no speed in the comparison: a
        # speed-scaled heuristic isn't comparable across nodes popped at
        # different (decayed) speeds, which is what used to pin best_key
        # to the start node and stall the horse.
        if node.lane == -1:
            remaining_here = -1.0
        else:
            remaining_here = track.total_distances[node.lane] - node.distance_from_start
        if remaining_here < best_remaining:
            best_remaining = remaining_here
            best_key = key
            best_speed = current_speed
            best_stamina = current_stamina


        if node == end_node:
            tick_time_used = time_so_far
            total_time = initial_total_time + tick_time_used
            return (
                reconstruct_path(prev, best_times, key),
                tick_time_used,
                total_time,
                node,
                current_speed,
                current_stamina,
                disabled_lanes
            )

        if time_so_far >= max_time:
            continue

        disabled_lanes[node.lane] = initial_total_time + best_times[best_key] + 1

        # Combine per-tick disabled lanes (inter-tick cooldown) with
        # within-tick visited lanes to get the full exclusion set
        excluded =  {
            lane for lane, vacate_time in disabled_lanes.items()
            if initial_total_time + best_times[best_key] < vacate_time
        }


        ray_neighbors = []
        if node.lane == 0 and not has_immediate_blocker(node, reservations, current_speed):
            pass
        else:
            ray_neighbors = compute_ray_neighbors(track, node, current_speed, excluded)

        neighbors = list(node.adjacent) + ray_neighbors

        for neighbor, distance in neighbors:

            speed_reduced = False
            real_speed = current_speed


            # NORMAL nodes run at the horse's normal speed — its
            # stamina-decayed ability — regardless of whether the
            # previous node was a slowed one. Only nodes that are
            # actually blocked get the reduced speed (loop below), so a
            # horse recovers to full pace the instant the road is open.
            ability_speed = decay_speed(starting_speed, current_stamina, base_stamina)
            effective_speed = max(ability_speed, 6.0)
            edge_time = distance / effective_speed
            new_time = time_so_far + edge_time

            if new_time > max_time:
                continue

            # ONE blocking mechanism: reservations. Horses that already
            # planned this tick have their exact trajectory here; horses
            # that haven't yet are covered by a synthetic constant-speed
            # projection seeded at tick start (see Race.advance_one_tick).
            # Either way the check is the same interpolation.
            if reservations and neighbor is not end_node:
                lane_res = reservations.get(neighbor.lane)
                # Entering a lane must clear the reserved horse's body AND
                # leave room for our own tail; moving within our lane only
                # needs to not run into a body (tail room behind us is the
                # follower's problem).

                mover_len = body_length if neighbor.lane != node.lane else 0.0
                
                if neighbor.lane == node.lane:
                    if lane_res:

                        blocked = any(
                            reservation_blocks(r, neighbor.distance_from_start, new_time, mover_len)
                            for r in lane_res
                        )

                        if blocked:
                            real_speed = effective_speed_for_heuristic(node, reservations, current_speed)
                            speed_reduced = True

                        while blocked:
                            real_speed -= 0.1        
                            
                            if real_speed <= 6:        
                                break

                            edge_time = distance / real_speed
                            new_time = time_so_far + edge_time

                            if new_time > max_time:
                                break

                            blocked = any(                          # re-check ALL blockers at the new time
                                reservation_blocks(r, neighbor.distance_from_start, new_time, mover_len)
                                for r in lane_res
                            )


                else:
                    # LANE CHANGES are strict and at full pace: the merge
                    # is legal at our nominal arrival speed or it doesn't
                    # happen. No slowing-to-merge — braking in your own
                    # lane so you can tuck in behind someone in the next
                    # one reads as bizarre riding, and it's never
                    # necessary: the same-lane branch above always offers
                    # the honest alternative (stay in lane, follow, try
                    # the switch again next tick from a better position).
                    if lane_res and any(
                        reservation_blocks(r, neighbor.distance_from_start, new_time, mover_len)
                        for r in lane_res
                    ):
                        continue

                
            new_stamina = max(0.0, current_stamina - distance * stamina_loss_per_meter)
            new_speed = real_speed if speed_reduced else ability_speed

            new_key = (neighbor, round(new_speed, 1), get_stamina_state(new_stamina))

            h_score = heuristic(neighbor, track, new_speed, reservations)
            new_f_score = new_time + h_score

            if new_key not in f_scores or new_f_score < f_scores[new_key]:
                f_scores[new_key] = new_f_score
                best_times[new_key] = new_time
                prev[new_key] = key
                heapq.heappush(
                    heap,
                    (new_f_score, new_time, neighbor, -new_speed, -new_stamina)
                )

    if best_key is not None:
        # print("Goal not reached within tick budget; returning best partial path.")
        tick_time_used = best_times[best_key]
        total_time = initial_total_time + tick_time_used
        return (
            reconstruct_path(prev, best_times, best_key),
            tick_time_used,
            total_time,
            best_key[0],
            ability_speed,
            best_stamina,
            disabled_lanes
        )

    return None, 0.0, initial_total_time, start_node, starting_speed, starting_stamina, disabled_lanes


def build_path_reservation(track, path, body_length, final_speed, tick_dt=None):
    """
    Turn a horse's planned tick path [(x, y, rel_t), ...] into per-lane
    space-time reservations.

    Returns {lane: (samples, body_length, tail_speed)} where samples is
    [(rel_t, head_distance_from_start), ...] in tick-relative time. Later
    planners this tick check candidate nodes against these with
    reservation_blocks — the whole travelled corridor is protected, but
    only for the moments the horse's body is actually on each piece of it.
    """
    per_lane = {}
    for x, y, t in path:
        node = None
        for lane_nodes in track.nodes.values():
            n = lane_nodes.get((x, y))
            if n is not None:
                node = n
                break
        if node is None:  # finish super-sink has no lane entry
            continue
        per_lane.setdefault(node.lane, []).append((t, node.distance_from_start))
    if tick_dt is None and path:
        tick_dt = path[-1][2]
    return {
        lane: (samples, body_length, final_speed, tick_dt)
        for lane, samples in per_lane.items()
    }


def reservation_blocks(reservation, node_dist, t, mover_length=0.0):
    """
    Is `node_dist` (distance_from_start in the reservation's lane) covered
    by the reserved horse's body at tick-relative time `t`?

    Before the horse enters the lane its first sampled position stands in.
    Between its last sample and the end of the tick the head is HELD at
    its final position — this mirrors exactly what commit does (the horse
    stands at final_node for the rest of the tick). Projecting it forward
    in that window would let followers arriving late in the tick creep
    inside the leader's real body by comparing against a phantom head.
    Only past the tick boundary does the head project forward again.
    """
    samples, body_length, tail_speed, tick_end = reservation
    
    t0, d0 = samples[0]
    tn, dn = samples[-1]
    
    if tick_end is None or tick_end < tn:
        tick_end = tn

    if t <= t0:
        head = d0

    elif t >= tn:
        head = dn + max(0.0, t - tick_end) * max(tail_speed, 6.0)

    else:
        head = dn
        for i in range(len(samples) - 1):
            ta, da = samples[i]
            tb, db = samples[i + 1]
            if ta <= t <= tb:
                head = da if tb == ta else da + (db - da) * (t - ta) / (tb - ta)
                break

    # On the body: theirs is (head - body_length, head] at arrival time.
    # STRICT at the tail edge: nose exactly touching tail at body_length
    # separation is legal station-keeping. With the follow-pace edge model
    # a follower's arrival clock advances at exactly the leader's vacate
    # rate, so at equal speeds every next node lands precisely ON this
    # boundary — an inclusive bound blocks all of them and the follower
    # slams to a crawl for a tick (sprint-stutter), which is exactly the
    # artifact this avoids. Overlap requires strictly inside the body.

    # chasing from behind or merging from side


    if head - body_length < node_dist <= head :
        return True

    
    # Merging in AHEAD of them: clear at arrival isn't enough — they keep
    # moving after we land, so our tail must clear their furthest head
    # THIS tick (dn), not just where they are at our arrival moment.

    # merging ahead
    if mover_length > 0.0 and node_dist > head and node_dist - mover_length <= dn:
        return True
    
    return False




def build_projected_reservation(node, speed, body_length, tick_dt):
    """
    Synthetic reservation for a horse that HASN'T planned yet this tick:
    its start-of-tick position projected forward at constant speed. Same
    shape as a real path reservation — {lane: (samples, body_length,
    tail_speed)} — so every consumer (edge blocking, heuristic throttling,
    ray gating) treats estimated and exact trajectories identically. As
    each horse plans, Race.advance_one_tick swaps this estimate for the
    real thing.
    """
    d = node.distance_from_start
    v = max(speed, 6.0)
    samples = [(0.0, d), (tick_dt, d + v * tick_dt)]
    return {node.lane: (samples, body_length, speed, tick_dt)}


def is_blocking_now(node, reservation, speed_for_arrival):
    """
    Would we reach this reserved horse's start-of-tick nose before its
    body has cleared it? Works on any reservation — synthetic or real:
    samples[0] is where the horse's head starts, reservation[2] is the
    speed its body vacates at.
    """
    samples, body_length, blocker_speed, _tick_end = reservation
    gap = samples[0][1] - node.distance_from_start
    if gap <= 0:
        return False
    safe_speed = max(speed_for_arrival, 6.0)
    my_arrival_time = gap / safe_speed
    safe_blocker_speed = max(blocker_speed, 6.0)
    t_clear = body_length / safe_blocker_speed
    return my_arrival_time < t_clear


def effective_speed_for_heuristic(node, reservations, max_speed):
    effective_speed = max_speed
    if not reservations:
        return effective_speed
    lane_res = reservations.get(node.lane)
    if not lane_res:
        return effective_speed
    for reservation in lane_res:
        if is_blocking_now(node, reservation, max_speed):
            effective_speed = min(effective_speed, max(reservation[2], 6.0))
    return effective_speed


def would_be_throttled(node, reservations, current_speed):
    return effective_speed_for_heuristic(node, reservations, current_speed) < current_speed


def has_immediate_blocker(node, reservations, current_speed):
    if not reservations:
        return False
    lane_res = reservations.get(node.lane)
    if not lane_res:
        return False
    return any(is_blocking_now(node, reservation, current_speed) for reservation in lane_res)


def heuristic(node, track, max_speed, reservations=None):
    if node.lane == -1:
        return 0.0
    total_dist = track.total_distances[node.lane]
    remaining_dist = max(0.0, total_dist - node.distance_from_start)
    effective_speed = effective_speed_for_heuristic(node, reservations, max_speed)
    return remaining_dist / effective_speed


def reconstruct_path(prev, best_times, end_key):
    path = []
    current_key = end_key
    while current_key is not None:
        node = current_key[0]
        t = best_times.get(current_key, 0.0)
        path.append((node.x, node.y, t))
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


def get_local_heading(track, node):
    lane = node.lane
    lane_list = track.lane_lists.get(lane)
    lane_idx_map = track.lane_index.get(lane)
    idx = lane_idx_map.get(node) if lane_idx_map else None

    dx, dy = None, None

    if lane_list is not None and idx is not None:
        if idx + 1 < len(lane_list):
            nxt = lane_list[idx + 1]
            dx, dy = nxt.x - node.x, nxt.y - node.y
        elif idx > 0:
            prev = lane_list[idx - 1]
            dx, dy = node.x - prev.x, node.y - prev.y

    if dx is None and node.adjacent:
        nxt = node.adjacent[0][0]
        if nxt.lane != -1:
            dx, dy = nxt.x - node.x, nxt.y - node.y

    if not dx and not dy:
        return 1.0, 0.0

    mag = math.hypot(dx, dy)
    return dx / mag, dy / mag


def compute_ray_neighbors(track, current_node, current_speed, excluded_lanes=None,
                          max_distance=5.0, step_size=0.5, lane_change_penalty=False):
    ray_neighbors = []
    speed_for_angle = current_speed * (0.7 if current_node.position == "Curve" else 1.0)
    current_lane = current_node.lane

    heading_x, heading_y = get_local_heading(track, current_node)
    heading_angle = math.atan2(heading_y, heading_x)

    # excluded_lanes = None

    for direction in [-1, 1]:
        target_lane = current_lane + direction
        if target_lane not in track.nodes:
            continue
        if excluded_lanes and target_lane in excluded_lanes:
            continue

        for (low, high), angle in track.SPEED_ANGLE_BUCKETS.items():
            if not (low <= speed_for_angle < high):
                continue

            x, y = current_node.x, current_node.y
            ray_angle = heading_angle - angle * direction
            ray_travel = 0.0

            while ray_travel <= max_distance:
                x += math.cos(ray_angle) * step_size
                y += math.sin(ray_angle) * step_size
                ray_travel += step_size

                target_node = find_nearest_node(track, target_lane, x, y)
                if target_node is None:
                    continue

                dx = target_node.x - current_node.x
                dy = target_node.y - current_node.y

                lateral = dx * (-heading_y) + dy * heading_x
                if abs(lateral) > track.lane_width * 2:
                    continue

                true_dist = math.hypot(dx, dy)

                if lane_change_penalty:
                    lane_delta = abs(target_lane - current_lane)
                    true_dist *= (1.0 + lane_delta * 0.2)

                ray_neighbors.append((target_node, round(true_dist, 2)))
                break

    return ray_neighbors


def decay_speed(speed, stamina, base_stamina):
    stamina = max(0.0, stamina)
    stamina_lost = base_stamina - stamina
    decay_multiplier = 1.0 - (stamina_lost / base_stamina) * 0.01
    min_speed = 5.0 + (stamina / base_stamina) * 7.0
    return max(speed * decay_multiplier, min_speed)