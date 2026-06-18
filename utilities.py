class RaceRenderer:
    @staticmethod
    def print_standings(race):
        standings = race.get_standings()
        print("\n=== Race Standings ===")
        for s in standings:
            print(f"{s['place']}. {s['horse'].name} "
                  f"(lane {s['lane']}) - {s['distance']:.2f}m")

    @staticmethod
    def plot_paths(race, track):
        import matplotlib.pyplot as plt

        results = race.get_results()

        plt.figure(figsize=(12, 8))

        # Draw base lanes lightly
        for lane_idx, lane_nodes in track.nodes.items():
            xs = [x for (x, y) in lane_nodes.keys()]
            ys = [y for (x, y) in lane_nodes.keys()]
            plt.plot(xs, ys, alpha=0.15, linewidth=1)

        colours = ["red", "blue", "green", "orange", "purple", "cyan"]

        for idx, (horse, lane, path, distance) in enumerate(results):
            if not path:
                continue
            color = colours[idx % len(colours)]
            xs = [p[0] for p in path]
            ys = [p[1] for p in path]
            plt.plot(
                xs,
                ys,
                marker="o",
                linestyle="-",
                linewidth=2,
                label=f"{horse.name} (lane {lane}, {distance:.1f}m)",
                color=color,
            )
            plt.plot(xs[0], ys[0], marker="s", markersize=8, color=color)
            plt.plot(xs[-1], ys[-1], marker="*", markersize=12, color=color)

        plt.title("Racetrack Path Visualization - Horse Race")
        plt.xlabel("X Coordinate (m)")
        plt.ylabel("Y Coordinate (m)")
        plt.axis("equal")
        plt.grid(True)
        plt.legend()
        plt.show()
