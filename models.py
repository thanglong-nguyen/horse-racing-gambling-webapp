import random
import math
from race_track import (
    triomphe, compute_ray_neighbors,
    build_path_reservation, build_projected_reservation,
)


def interpolate_position(history, t):
    """
    Given a horse's timestamped history [(x, y, total_time), ...] —
    always sorted by total_time, since it's built tick by tick — return
    the interpolated (x, y) position at playback time `t`.

    This is the piece that makes precompute-then-replay work: the race
    can be simulated once, offline, using whatever tick size is safest
    for the AI's decisions, and then played back at any frame rate by
    just asking "where was this horse at time t" — smoothly, with no
    dependency on how coarse the original simulation ticks were.
    """
    if not history:
        return None
    if t <= history[0][2]:
        return history[0][0], history[0][1]
    if t >= history[-1][2]:
        return history[-1][0], history[-1][1]

    lo, hi = 0, len(history) - 1
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if history[mid][2] <= t:
            lo = mid
        else:
            hi = mid

    x0, y0, t0 = history[lo]
    x1, y1, t1 = history[hi]
    if t1 == t0:
        return x0, y0
    frac = (t - t0) / (t1 - t0)
    return x0 + frac * (x1 - x0), y0 + frac * (y1 - y0)


# --- Horse & Rider ---

class Horse:
    def __init__(self, name, base_speed, stamina, stamina_loss_per_meter , temperament=1.0):
        self.name = name
        self.base_speed = base_speed
        self.stamina = stamina
        self.stamina_loss_per_meter = stamina_loss_per_meter
        self.temperament = temperament
        self.length = 2.4

class HorseRuntimeState:
    """
    Runtime state of a horse during a live race.
    """
    def __init__(self, horse, lane, start_node):
        self.horse = horse
        self.lane = lane
        self.node = start_node          # current TrackNode
        self.current_speed = horse.base_speed
        self.current_stamina = min(100.0, horse.stamina)
        self.total_distance = 0.0
        self.total_time = 0.0
        self.history = [(start_node.x, start_node.y, 0.0)]  # list[(x, y, total_time)]
        self.finished = False
        # Candidate lane-change rays from the horse's CURRENT node — list
        # of (target_node, distance) tuples, same shape compute_ray_neighbors
        # returns. Computed after the horse's position is finalized for
        # the tick (see advance_one_tick), so it always matches exactly
        # where the renderer will draw it from. Purely for the debug
        # overlay; doesn't affect simulation.
        self.debug_rays = []
        self.disabled_lanes = {}  # {lane_index: vacate_time} — lanes currently off-limits for ray casting


    def __lt__(self, other):
        return self.current_speed < other.current_speed
    
    def __repr__(self):
        return f"{self.horse.name}: {self.current_speed:.2f} m/s"

# --- Track Node & Generator ---

class TrackNode:
    def __init__(self, x, y, lane, position):
        self.x = x
        self.y = y
        self.lane = lane
        self.position = position
        self.adjacent = []
        self.distance_from_start = 0.0

    def __hash__(self):
        return hash((self.x, self.y, self.lane))

    def __eq__(self, other):
        return isinstance(other, TrackNode) and self.x == other.x and self.y == other.y and self.lane == other.lane

    def __lt__(self, other):
        return False 

    def __repr__(self):
        return f"{self.x},{self.y},{self.lane},{self.position}"
    
    def get_coordinates(self):
        return (self.x, self.y)

class RaceTrack:
    SPEED_ANGLE_BUCKETS = {
        (0, 10): math.radians(60),
        (10, 14): math.radians(45),
        (14, 17): math.radians(30),
        (17, 20): math.radians(15),
        (20, float('inf')): math.radians(10)
    }

    def __init__(self, finish_node, straight_length=30, base_radius=10, resolution=40, lanes=8, lane_width=1.0):
        self.nodes = {}
        self.node_grid = {}
        self.start_nodes = {}

        self.finish_node = finish_node
        self.straight_length = straight_length
        self.base_radius = base_radius
        self.resolution = resolution
        self.lane_width = lane_width

        self.total_distances = {}  # Store total lane distances

        self.build_all_lanes(lanes)
        self.build_grid()
        self.build_graph()

        # After building graph / nodes
        self.lane_lists = {
            lane: list(self.nodes[lane].values())
            for lane in self.nodes
        }

        # Reverse index map for fast lookup: node → index
        self.lane_index = {
            lane: {node: i for i, node in enumerate(self.lane_lists[lane])}
            for lane in self.lane_lists
        }

    def build_grid(self):
        for lane in self.nodes.values():
            for (x, y), node in lane.items():
                cell_x, cell_y = int(x), int(y)
                cell_key = (cell_x, cell_y)
                if cell_key not in self.node_grid:
                    self.node_grid[cell_key] = []
                self.node_grid[cell_key].append(node)

    def build_all_lanes(self, lane_count):
        for lane in range(lane_count):
            radius = self.base_radius + lane * self.lane_width
            half_height = radius
            self.nodes[lane] = {}

            bottom_y = -half_height
            top_y = half_height
            cx_right = self.straight_length
            cx_left = 0
            cy = 0

            num_straight_nodes = int(self.straight_length / 0.1)
            arc_length = math.pi * radius
            num_curve_nodes = int(arc_length / 0.1)

            for i in range(num_straight_nodes//2, num_straight_nodes + 1):
                x = round(i * 0.1, 2)
                key = (x, bottom_y)
                self.nodes[lane][key] = TrackNode(x, bottom_y, lane, "Straight")

            start_key = (round(self.straight_length//2, 2), bottom_y)
            self.start_nodes[lane] = self.nodes[lane][start_key]

            for i in range(num_curve_nodes + 1):
                angle = -math.pi / 2 + (i / num_curve_nodes) * math.pi
                x = round(cx_right + radius * math.cos(angle), 2)
                y = round(cy + radius * math.sin(angle), 2)
                key = (x, y)
                self.nodes[lane][key] = TrackNode(x, y, lane, "Curve")

            for i in range(num_straight_nodes + 1):
                x = round(self.straight_length - i * 0.1, 2)
                key = (x, top_y)
                self.nodes[lane][key] = TrackNode(x, top_y, lane, "Straight")

            for i in range(num_curve_nodes + 1):
                angle = math.pi / 2 + (i / num_curve_nodes) * math.pi
                x = round(cx_left + radius * math.cos(angle), 2)
                y = round(cy + radius * math.sin(angle), 2)
                key = (x, y)
                self.nodes[lane][key] = TrackNode(x, y, lane, "Curve")

            for i in range(num_straight_nodes//2):
                x = round(i * 0.1, 2)
                key = (x, bottom_y)
                self.nodes[lane][key] = TrackNode(x, bottom_y, lane, "Straight")

    def distance_between(self, node1, node2):
        dx = node1.x - node2.x
        dy = node1.y - node2.y
        return round(math.sqrt(dx * dx + dy * dy), 2)

    def build_graph(self):
        for lane in self.nodes.values():
            total_dist = 0
            previous_node = None
            lane_nodes = list(lane.values())
            for i, node in enumerate(lane_nodes):
                if i == 0:
                    previous_node = node
                    continue

                dist = self.distance_between(previous_node, node)
                total_dist += dist
                node.distance_from_start = round(total_dist, 2)
                previous_node.adjacent.append((node, dist))
                previous_node = node

            print(f"Total distance for lane: {total_dist:.2f}m")
            self.total_distances[lane_nodes[0].lane] = total_dist  # Store the total distance for the lane

            # Connect the last node to the super sink
            if previous_node:
                previous_node.adjacent.append((self.finish_node, 0.0))

    def get_lane_coordinates(self, lane_index):
        return list(self.nodes[lane_index].keys())



from matplotlib.animation import FuncAnimation
import matplotlib.pyplot as plt

class Race:
    def __init__(self, horses_with_lanes, track, tick_dt=0.2, total_time=6.0):
        """
        horses_with_lanes: list of (Horse, lane_index) tuples
        track: RaceTrack instance
        tick_dt: seconds per simulation tick (and per A* planning horizon)
        total_time: total simulated time in seconds
        """
        self.horses_with_lanes = horses_with_lanes
        self.track = track


        self.tick_dt = tick_dt
        self.total_time = total_time

        # Runtime state for each horse
        self.states = [
            HorseRuntimeState(horse, lane, track.start_nodes[lane])
            for horse, lane in horses_with_lanes
        ]

        self.num_frames = int(total_time / tick_dt)
        self._ran = False



    # ---------- core simulation ----------

    def advance_one_tick(self):
        """Advance all horses by one tick using A* as short-horizon planner."""

        planned = []  # (state, final_node, new_total_time, final_stamina, final_speed)

        # Leaders plan first: the horse physically in front can't be blocked
        # by anyone behind it
        active = [s for s in self.states if not s.finished]
        active.sort(
            key=lambda s: self.track.total_distances[s.node.lane] - s.node.distance_from_start
        )

        # EVERY horse has a reservation at all times this tick. A horse
        # that hasn't planned yet is covered by a synthetic one — its
        # start-of-tick position projected at constant speed. As each
        # horse plans, its estimate is swapped for the exact trajectory.
        # One blocking mechanism; estimates simply get upgraded to truth
        # as the loop advances.
        res_by_horse = {
            id(s): build_projected_reservation(
                s.node, s.current_speed, s.horse.length, self.tick_dt
            )
            for s in active
        }

        for s in active:
            horse = s.horse

            # Everyone's reservation except our own
            reservations = {}
            for sid, lanes in res_by_horse.items():
                if sid == id(s):
                    continue
                for lane, entry in lanes.items():
                    reservations.setdefault(lane, []).append(entry)

            # Call the new time-based A*
            (
                path,
                tick_time,
                new_total_time,
                final_node,
                final_speed,
                final_stamina,
                new_disabled_lanes
            ) = triomphe (
                s.node,
                self.track.finish_node,
                self.track,
                base_speed=horse.base_speed,
                starting_speed=s.current_speed,
                base_stamina=horse.stamina,
                starting_stamina=s.current_stamina,
                stamina_loss_per_meter=horse.stamina_loss_per_meter,
                max_time=self.tick_dt,
                initial_total_time=s.total_time,
                disabled_lanes=dict(s.disabled_lanes),  # pass a copy so a_star can mutate freely
                reservations=reservations,
                body_length=horse.length
            )


            # print(
            #     f"[DEBUG] {horse.name}: "
            #     f"path_len={len(path) if path else 0}, "
            #     f"tick_time={tick_time:.3f}, "
            #     f"total_time={new_total_time:.3f}, "
            #     f"final_node={final_node}, "
            #     f"final_speed={final_speed:.2f}, "
            #     f"final_stamina={final_stamina:.2f}"
            # )

            # Nothing useful planned this tick: the horse stays where it
            # is, so its synthetic reservation stays in place for later
            # planners.
            if not path or tick_time <= 0.0 or final_node is None:
                continue

            # Upgrade this horse's estimate to its exact tick trajectory —
            # later planners see where it actually is at each moment of
            # the tick, not the constant-speed guess.
            # Held-head (tick_dt) applies only to horses still racing —
            # commit really does hold them at final_node until tick end.
            # A FINISHER doesn't park on the line: it gallops through.
            # Holding its head there leaves a phantom stationary body on
            # the finish line for the rest of the tick, braking every
            # horse arriving just behind it. Let a finisher's reservation
            # keep projecting forward past its crossing time instead.
            finished_now = final_node is self.track.finish_node
            
            res_by_horse[id(s)] = build_path_reservation(
                self.track, path, horse.length, final_speed,
                tick_dt=None if finished_now else self.tick_dt
            )

            planned.append((s, path, s.total_time, final_node, new_total_time, final_stamina, final_speed, new_disabled_lanes))

        # ---- COMMIT phase: apply everyone's move ----
        planned_ids = set()
        for s, path, tick_start_time, final_node, new_total_time, final_stamina, final_speed, new_disabled_lanes in planned:
            planned_ids.add(id(s))
            fx, fy = self.track.finish_node.x, self.track.finish_node.y
            for x, y, rel_t in path[1:]:
                # The finish super-sink sits in the infield — writing its
                # coordinates into history makes every finisher's replay
                # dot teleport to the middle of the field and freeze.
                if x == fx and y == fy:
                    continue
                # Clamp to the tick: the same-lane wait loop can overshoot
                # max_time by a hair before its budget break fires, and an
                # overshot timestamp lands past the tick-end point appended
                # below — a microscopic backwards step in the history that
                # jitters the replay interpolator. Position is kept; the
                # timestamp just can't leave the tick.
                s.history.append((x, y, tick_start_time + min(rel_t, self.tick_dt)))

            s.node = final_node
            s.current_stamina = max(0.0, min(100.0, final_stamina))
            s.current_speed = final_speed
            s.lane = final_node.lane if final_node.lane != -1 else s.lane
            s.disabled_lanes = new_disabled_lanes


            if final_node is not self.track.finish_node:
                s.total_distance = final_node.distance_from_start

            if s.node is self.track.finish_node:
                s.finished = True
                # Finish time is the exact moment the line was crossed
                s.total_time = new_total_time
            else:
                # Every horse consumes exactly tick_dt of wall time per
                # tick, whether A* used its whole budget or not. Without
                # this, per-tick shortfalls accumulate differently per
                # horse and their clocks drift apart — the sim's positions
                # stay honest but replay (which interpolates by wall time)
                # shows horses several metres from where they really are
                # relative to each other.
                s.total_time = tick_start_time + self.tick_dt
                s.history.append((final_node.x, final_node.y, s.total_time))

        # Horses that planned nothing this tick are standing still — but
        # standing still consumes wall time like anything else.
        for s in active:  # probably remove this, all horses should plan something every tick
            if id(s) not in planned_ids:
                s.total_time += self.tick_dt
                s.history.append((s.node.x, s.node.y, s.total_time))


        # ---- DEBUG OVERLAY: candidate rays from where everyone now stands ----
        # Computed last, after every horse's position AND the rebuilt
        # blocking map are both final for this tick. Doing this earlier
        # (e.g. during planning, before positions update) would compute
        # rays from last tick's position but draw them from this tick's
        # dot — two different points stitched together, which looks like
        # a wrong direction even though the underlying ray math is
        # correct. Uses the exact same gating rule a_star itself uses
        # internally, so the overlay never shows a ray the planner
        # wouldn't actually have bothered considering.
        # for s in self.states:
        #     if s.finished:
        #         s.debug_rays = []
        #         continue
        #     if s.node.lane == 0 and not has_immediate_blocker(s.node, self.blocked, s.current_speed):
        #         s.debug_rays = []
        #     else:
        #         excluded = {lane for lane, t in s.disabled_lanes.items() if s.total_time < t}
        #         s.debug_rays = compute_ray_neighbors(self.track, s.node, s.current_speed, excluded_lanes=excluded)


    def run(self):
        """Run the full race simulation (no animation, just physics)."""
        for _ in range(self.num_frames):
            self.advance_one_tick()
            # optional early stop if everyone finished
            if all(s.finished for s in self.states):
                break
        self._ran = True

    def play_replay(self, playback_speed=1.0):
        """
        Precompute the entire race right now (fast — there's no real-time
        constraint, so even a very small tick_dt finishes in well under a
        second), then hand off to a renderer that just interpolates the
        recorded timestamped history smoothly at whatever frame rate the
        display wants. The simulation's tick size no longer has any
        bearing on how smooth this looks — that tradeoff is gone, because
        "compute the race" and "watch the race" are no longer the same
        clock. This is the betting-phase-then-racing-phase split: run
        this once while bets are open, then replay the result live.
        """
        if not self._ran:
            self.run()
        from renderer import RaceReplayRenderer
        renderer = RaceReplayRenderer(self, playback_speed=playback_speed)
        renderer.run()

    # ---------- results / standings ----------

    def get_results(self):
        """
        Return list of (horse, lane, history, total_distance).
        """
        if not self._ran:
            raise RuntimeError("Race has not been run yet. Call race.run() first.")
        return [
            (s.horse, s.lane, s.history, s.total_distance)
            for s in self.states
        ]

    def get_standings(self):
        """
        Return standings as list of dicts sorted by distance.
        """
        if not self._ran:
            raise RuntimeError("Race has not been run yet. Call race.run() first.")
        results = self.get_results()
        results.sort(key=lambda r: r[3], reverse=True)  # by distance desc

        standings = []
        for place, (horse, lane, history, dist) in enumerate(results, start=1):
            standings.append({
                "place": place,
                "horse": horse,
                "lane": lane,
                "distance": dist,
                "history": history,
            })
        return standings

    # ---------- live animation ----------

    def play(self):
        from renderer import RaceRendererPygame
        renderer = RaceRendererPygame(self)
        renderer.run()
        self._ran = True