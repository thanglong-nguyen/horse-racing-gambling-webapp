import pygame

class RaceRendererPygame:

    def __init__(self, race, show_debug_rays=True):
        self.race = race
        self.show_debug_rays = show_debug_rays
        pygame.init()

        self.window_w = 1200
        self.window_h = 800
        self.screen = pygame.display.set_mode((self.window_w, self.window_h))
        pygame.display.set_caption("Horse Racing Simulation")
        self.clock = pygame.time.Clock()

        self.font_small = pygame.font.SysFont(None, 20)
        self.font_medium = pygame.font.SysFont(None, 24)

        # ---------------------------
        # AUTO–COMPUTE TRACK BOUNDS
        # ---------------------------
        xs = []
        ys = []
        for lane_nodes in race.track.nodes.values():
            for (x, y) in lane_nodes.keys():
                xs.append(x)
                ys.append(y)

        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)

        track_width_m  = max_x - min_x
        track_height_m = max_y - min_y

        # Fit track with 10% padding
        scale_x = (self.window_w * 0.9) / track_width_m
        scale_y = (self.window_h * 0.9) / track_height_m

        # Final scale is the lower one so everything fits
        self.SCALE = min(scale_x, scale_y)

        # Centering offsets
        self.OFFSET_X = self.window_w / 2 - (min_x + track_width_m / 2) * self.SCALE
        self.OFFSET_Y = self.window_h / 2 + (min_y + track_height_m / 2) * self.SCALE

        print(f"[Renderer] Using SCALE={self.SCALE:.3f} px/m")

        self.colours = [
            (255,0,0),(0,100,255),(0,200,0),
            (255,140,0),(200,0,200),(0,255,255),(255,255,0)
        ]


    def world_to_screen(self, x, y):
        sx = x * self.SCALE + self.OFFSET_X
        sy = -y * self.SCALE + self.OFFSET_Y
        return int(sx), int(sy)

    def draw_track(self):
        # draw each lane as a polyline
        for lane_idx, lane_nodes in self.race.track.nodes.items():
            if not lane_nodes:
                continue
            points = [self.world_to_screen(x, y) for (x, y) in lane_nodes.keys()]
            pygame.draw.lines(self.screen, (80, 80, 80), False, points, 1)

        # optional: draw finish node as a small white circle
        fn = self.race.track.finish_node
        fx, fy = self.world_to_screen(fn.x, fn.y)
        pygame.draw.circle(self.screen, (255, 255, 255), (fx, fy), 5)

    def draw_horses(self):
        for i, s in enumerate(self.race.states):
            color = self.colours[i % len(self.colours)]
            x, y = s.node.get_coordinates()
            px, py = self.world_to_screen(x, y)

            # horse body
            pygame.draw.circle(self.screen, color, (px, py), 8)

            # horse name
            name_surf = self.font_small.render(s.horse.name, True, color)
            self.screen.blit(name_surf, (px + 10, py - 10))

            # show total time above horse (for debugging the time-based pathing)
            time_text = f"{s.total_time:.2f}s"
            time_surf = self.font_small.render(time_text, True, (200, 200, 200))
            self.screen.blit(time_surf, (px + 10, py + 5))

    def draw_debug_rays(self):
        # Draws every candidate lane-change ray a horse is currently
        # considering, in that horse's own colour, with a small marker
        # where the ray actually crosses into the other lane. Horses with
        # no rays this tick (lane 0, nothing in the way) simply draw
        # nothing here — that's the "not worth considering" case made
        # visible.
        for i, s in enumerate(self.race.states):
            if not s.debug_rays:
                continue
            color = self.colours[i % len(self.colours)]
            sx, sy = s.node.get_coordinates()
            spx, spy = self.world_to_screen(sx, sy)
            for target_node, distance in s.debug_rays:
                tx, ty = target_node.get_coordinates()
                tpx, tpy = self.world_to_screen(tx, ty)
                pygame.draw.line(self.screen, color, (spx, spy), (tpx, tpy), 1)
                pygame.draw.circle(self.screen, color, (tpx, tpy), 4, 1)

    def draw_hud(self):
        # simple HUD: show tick dt and whether all finished
        all_finished = all(st.finished for st in self.race.states)
        status = "FINISHED" if all_finished else "RUNNING"
        ray_status = "ON" if self.show_debug_rays else "OFF"
        hud_text = f"dt={self.race.tick_dt:.2f}s   status={status}   rays={ray_status} (R to toggle)"
        hud_surf = self.font_medium.render(hud_text, True, (220, 220, 220))
        self.screen.blit(hud_surf, (20, 20))

    def run(self):
        running = True
        sim_accum = 0.0  # seconds of real time accumulated

        while running:
            # real time passed since last frame (seconds)
            real_dt = self.clock.tick(60) / 1000.0   # cap render at 60 FPS
            sim_accum += real_dt

            # handle quit
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    running = False
                elif event.type == pygame.KEYDOWN and event.key == pygame.K_r:
                    self.show_debug_rays = not self.show_debug_rays

            # advance simulation in fixed-size steps (tick_dt) as real time allows
            while sim_accum >= self.race.tick_dt and not all(s.finished for s in self.race.states):
                self.race.advance_one_tick()
                sim_accum -= self.race.tick_dt

            # draw
            self.screen.fill((10, 10, 10))
            self.draw_track()
            if self.show_debug_rays:
                self.draw_debug_rays()
            self.draw_horses()
            self.draw_hud()
            pygame.display.flip()

        pygame.quit()