import random
import math
from race_track import a_star

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
        self.history = [start_node.get_coordinates()]  # list[(x,y)]
        self.finished = False

class Rider:
    def __init__(self, name, skill, experience):
        self.name = name
        self.skill = skill
        self.experience = experience
        self.chemistries = {}  # horse_name: float


# --- Team ---

class Team:
    def __init__(self, name):
        self.name = name
        self.riders = []
        self.horses = []
        self.selected_pair = None
        self.performance = 0.0
        self.position = None
        self.distance = 0
        self.finished = False

    def select_for_race(self, rider, horse):
        self.selected_pair = (rider, horse)

    def calculate_performance(self):
        if not self.selected_pair:
            raise ValueError("No rider-horse pair selected for race.")
        rider, horse = self.selected_pair
        chemistry_bonus = rider.chemistries.get(horse.name, 0)
        randomness = random.uniform(-1, 1)  # optional flavor
        self.performance = horse.base_speed + rider.skill + rider.experience + chemistry_bonus + randomness
        return self.performance


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
                previous_node.adjacent.append((self.finish_node, 0.1))

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

        # per tick blockage map
        self.blocked = {
            lane: [False] * len(track.lane_lists[lane])
            for lane in track.lane_lists
        }

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

        # reset blocking for this tick
        for lane in self.blocked:
            for i in range(len(self.blocked[lane])):
                self.blocked[lane][i] = False

        for s in self.states:
            if s.finished:
                continue

            horse = s.horse
            

            # Call the new time-based A*
            (
                path,
                tick_time,
                new_total_time,
                final_node,
                final_speed,
                final_stamina
            ) = a_star(
                s.node,
                self.track.finish_node,
                self.track,
                starting_speed=s.current_speed,
                starting_stamina=s.current_stamina,
                stamina_loss_per_meter=horse.stamina_loss_per_meter,
                max_time=self.tick_dt,                       # per-tick time budget
                blocked_map=self.blocked,
                max_speed_for_heuristic=horse.base_speed,    # or horse.max_speed if you add it
                initial_total_time=s.total_time
            )

            print(
                f"[DEBUG] {horse.name}: "
                f"path_len={len(path) if path else 0}, "
                f"tick_time={tick_time:.3f}, "
                f"total_time={new_total_time:.3f}, "
                f"final_node={final_node}, "
                f"final_speed={final_speed:.2f}, "
                f"final_stamina={final_stamina:.2f}"
            )

            # Nothing useful planned this tick
            if not path or tick_time <= 0.0 or final_node is None:
                continue

            # Use the state that A* actually ended at
            s.node = final_node
            s.total_time = new_total_time
            s.current_stamina = max(0.0, min(100.0, final_stamina))
            s.current_speed = final_speed
            s.history.append(s.node.get_coordinates())

            # If we reached the finish, mark finished and skip blocking logic
            if s.node is self.track.finish_node:
                s.finished = True
                continue

            # BLOCKING LOGIC
            lane = s.node.lane            
            node = s.node

            lane_map = self.track.lane_index.get(lane)
            idx = lane_map.get(node) if lane_map else None

            if idx is None:
                continue

            # how many nodes a horse occupies (e.g. 2.4 m / 0.1 spacing ≈ 24 nodes)
            horse_length_nodes = int(s.horse.length / 0.1)

            tail_idx = max(0, idx - horse_length_nodes)
            for i in range(tail_idx, idx + 1):
                self.blocked[lane][i] = True


    def run(self):
        """Run the full race simulation (no animation, just physics)."""
        for _ in range(self.num_frames):
            self.advance_one_tick()
            # optional early stop if everyone finished
            if all(s.finished for s in self.states):
                break
        self._ran = True

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




