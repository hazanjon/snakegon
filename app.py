import app
import random
import math
import time
from events.input import BUTTON_TYPES, ButtonDownEvent, ButtonUpEvent
from events.input import BUTTON_TYPES, ButtonDownEvent, ButtonUpEvent, Buttons
from system.eventbus import eventbus
class hex_utils:
    HEX_SIZE = 9
    SQRT3 = math.sqrt(3)
    
    DIRECTIONS = {
        "A": (0, -1),
        "B": (1, -1),
        "C": (1, 0),
        "D": (0, 1),
        "E": (-1, 1),
        "F": (-1, 0),
    }
    
    ORDERED_DIRECTIONS = ["A", "B", "C", "D", "E", "F"]

    @staticmethod
    def axial_to_pixel(q, r, size=HEX_SIZE):
        x = size * (3.0 / 2.0 * q)
        y = size * (hex_utils.SQRT3 / 2.0 * q + hex_utils.SQRT3 * r)
        return x, y

    @staticmethod
    def pixel_to_axial_rounded(x, y, size=HEX_SIZE):
        q_est = (2./3 * x) / size
        r_est = (-1./3 * x + hex_utils.SQRT3/3 * y) / size
        return hex_utils.cube_to_axial(hex_utils.cube_round(hex_utils.axial_to_cube(q_est, r_est)))

    @staticmethod
    def axial_to_cube(q, r):
        return q, r, -q-r

    @staticmethod
    def cube_to_axial(cube):
        return cube[0], cube[1]

    @staticmethod
    def cube_round(cube):
        q, r, s = cube
        rq, rr, rs = round(q), round(r), round(s)
        q_diff, r_diff, s_diff = abs(rq - q), abs(rr - r), abs(rs - s)

        if q_diff > r_diff and q_diff > s_diff:
            rq = -rr - rs
        elif r_diff > s_diff:
            rr = -rq - rs
        else:
            rs = -rq - rr
        return rq, rr, rs

    @staticmethod
    def axial_add(a, b):
        return (a[0] + b[0], a[1] + b[1])

    @staticmethod
    def get_neighbor(q, r, direction_key):
        dq, dr = hex_utils.DIRECTIONS[direction_key]
        return (q + dq, r + dr)


class SnakegonApp(app.App):
    def __init__(self):
        super().__init__()
        self.button_states = Buttons(self) # Restore standard helper
        
        # Track previous state for Edge Detection
        self.prev_button_states = {
            "UP": False, "DOWN": False, "LEFT": False, "RIGHT": False, 
            "CONFIRM": False, "CANCEL": False
        }
        
        self.state = "MENU" # MENU, GAME, GAME_OVER
        self.score = 0
        self.high_score = 0
        self.map_radius = 7
        self.snake = []
        self.food = None
        self.direction = "A"
        self.next_direction = "A"
        self.tick_timer = 0
        self.tick_speed = 300
        self.button_hold_time = 0
        self.mode = "SOLO" # SOLO, AI
        self.ai_snake = []
        
        # Register Event Handlers
        # Store strong refs to prevent GC
        self._on_down = self.handle_button_down
        self._on_up = self.handle_button_up
        eventbus.on(ButtonDownEvent, self._on_down, self)
        eventbus.on(ButtonUpEvent, self._on_up, self)
        
        # Latch for short presses
        self.latched_buttons = set()
        
        self.BUTTON_MAPPING = {
            "A": "UP",
            "B": "RIGHT",
            "C": "CONFIRM",
            "D": "DOWN",
            "E": "LEFT",
            "F": "CANCEL",
            "UP": "UP",
            "RIGHT": "RIGHT",
            "CONFIRM": "CONFIRM",
            "DOWN": "DOWN",
            "LEFT": "LEFT",
            "CANCEL": "CANCEL"
        }
        
        # Debounce for Menu
        self.last_toggle_time = 0
        self.last_state_change = 0
        self.prev_focused = True
        
        self.calculate_grid_paths()
        
        print("SnakegonApp initialized")

    def is_button_pressed(self, button_type):
        # Wrapper for Buttons.get() - Checks State (Held)
        if self.button_states.get(button_type):
            return True
        # Also check latch for "Just Pressed" in Menu
        # This makes Menu responsive to quick taps too
        # Find logical name for this button_type
        # mapping reverse lookup or just iterate? 
        # BUTTON_TYPES values have names.
        name = button_type.name
        logical = self.BUTTON_MAPPING.get(name)
        if logical and logical in self.latched_buttons:
            # We don't clear here, update() clears it. 
            # But Menu handles logic in update() anyway.
            return True
            
        return False

    def reset_game(self):
        self.snake = [(0, 0), (0, 1), (0, 2)] 
        self.direction = "A"
        self.next_direction = "A"
        self.score = 0
        if self.mode == "AI_VS_AI":
            self.tick_speed = 250
        else:
            self.tick_speed = 500
        
        self.calculate_grid_paths()
        
        if self.mode in ["AI", "AI_VS_AI"]:
            # AI Start opposite side (Top-Rightish)
            self.ai_snake = [(3, -3), (3, -4), (3, -5)]
        else:
            self.ai_snake = []
            
        self.foods = []
        self.spawn_food()
        self.countdown_timer = 3000
        self.state = "COUNTDOWN"
        self.winner = None
        self.latched_buttons.clear()

    def spawn_food(self):
        if not hasattr(self, 'foods'):
            self.foods = []
        while len(self.foods) < 3:
            q = random.randint(-self.map_radius, self.map_radius)
            r1 = max(-self.map_radius, -q - self.map_radius)
            r2 = min(self.map_radius, -q + self.map_radius)
            r = random.randint(r1, r2)
            
            tile = (q, r)
            if (tile not in self.snake and 
                tile not in self.ai_snake and 
                tile not in self.foods):
                self.foods.append(tile)

    def update(self, delta):
        # Focus recovery check (clears stuck input states when app is reopened)
        curr_focused = getattr(self, '_focused', True)
        was_unfocused = not getattr(self, 'prev_focused', True)
        self.prev_focused = curr_focused
        
        if curr_focused and was_unfocused:
            self.button_states.clear()
            self.latched_buttons.clear()
            self.last_state_change = time.ticks_ms()

        # Input Processing
        self.process_inputs()
        
        # Clear Latch AFTER processing is done for this frame
        # We clear it at the END of update to ensure all logic saw it?
        # Or clear immediately after processing?
        # process_inputs handles Game Move.
        # Menu logic is below. Only one State is active.
        
        captured_latch = self.latched_buttons.copy()
        self.latched_buttons.clear()
        
        # Helper to check if button was pressed this frame (Latch) OR is held (State)
        def check_btn(logical_name, btn_type):
            return (logical_name in captured_latch) or self.button_states.get(btn_type)

        if self.check_exit_combo(delta):
            self.state = "MENU"
            self.last_state_change = time.ticks_ms()
            return True

        if self.state == "MENU":
            if check_btn("CONFIRM", BUTTON_TYPES["CONFIRM"]):
                self.reset_game()
            if check_btn("CANCEL", BUTTON_TYPES["CANCEL"]):
                # Debounce: prevent accidental minimise from state transitions
                if time.ticks_ms() - self.last_state_change > 500:
                    self.minimise()
            
            # Toggle Mode
            now = time.ticks_ms()
            if (check_btn("RIGHT", BUTTON_TYPES["RIGHT"]) or 
                check_btn("LEFT", BUTTON_TYPES["LEFT"])):
                if now - self.last_toggle_time > 300:
                    if self.mode == "SOLO":
                        self.mode = "AI"
                    elif self.mode == "AI":
                        self.mode = "AI_VS_AI"
                    else:
                        self.mode = "SOLO"
                    self.last_toggle_time = now
        
        elif self.state == "COUNTDOWN":
            self.countdown_timer -= delta
            if self.countdown_timer <= 0:
                self.state = "GAME"
        
        elif self.state == "GAME":
            # handle_input() is deprecated, using event handler
            self.tick_timer += delta
            if self.tick_timer >= self.tick_speed:
                self.tick_timer = 0
                self.step_game()
        
        elif self.state == "GAME_OVER":
            if check_btn("CONFIRM", BUTTON_TYPES["CONFIRM"]):
                self.reset_game()
            if check_btn("CANCEL", BUTTON_TYPES["CANCEL"]):
                self.state = "MENU"
                self.last_state_change = time.ticks_ms()

        return True

    def process_inputs(self):
        # Process Game Direction from Latch
        # Iterate through latched buttons
        # We read directly from self.latched_buttons before clearing (it is cleared in update())
        # Actually update() calls process_inputs() first.
        # So we can just read self.latched_buttons.
        
        for logical in self.latched_buttons:
             self.handle_game_input(logical)
             
        # Also check Rising Edge from polling?
        # Redundant if Events work. But keep polling as backup? 
        # If I keep polling, I might get double moves if both fire?
        # Handlers are reliable now. Let's trust Events for "Just Pressed".
        # If user holds button, we don't want repeated moves anyway (Snake is discrete).
        # We only want move on Initial Press.
        pass 

    def handle_game_input(self, name):
        if self.state == "GAME":
            opposites = {"A": "D", "B": "E", "C": "F", "D": "A", "E": "B", "F": "C"}
            new_dir = None
            
            if name == "UP": new_dir = "A"
            elif name == "RIGHT": new_dir = "B"
            elif name == "CONFIRM": new_dir = "C"
            elif name == "DOWN": new_dir = "D"
            elif name == "LEFT": new_dir = "E"
            elif name == "CANCEL": new_dir = "F"
            
            if new_dir:
                if new_dir != opposites.get(self.direction):
                    self.next_direction = new_dir

    def check_exit_combo(self, delta):
        # Exit Combo needs HOLD checking, so we rely on button_states (State/Polling)
        pair1 = self.button_states.get(BUTTON_TYPES["UP"]) and self.button_states.get(BUTTON_TYPES["DOWN"])
        pair2 = self.button_states.get(BUTTON_TYPES["RIGHT"]) and self.button_states.get(BUTTON_TYPES["LEFT"])
        pair3 = self.button_states.get(BUTTON_TYPES["CONFIRM"]) and self.button_states.get(BUTTON_TYPES["CANCEL"])
        
        if pair1 or pair2 or pair3:
            self.button_hold_time += delta
            if self.button_hold_time > 2000:
                self.button_hold_time = 0
                return True
        else:
            self.button_hold_time = 0
        return False
        
    # Event Handlers (Restored)
    def handle_button_down(self, event):
        raw_name = event.button.name
        
        logical = self.BUTTON_MAPPING.get(raw_name)
        if logical:
            self.latched_buttons.add(logical)

    def handle_button_up(self, event):
        pass # No action needed for latching on rising edge usually
        
        # Menu/Game Over still use polling via update() for simplicity or can move here too
        # But polling is fine for non-reflexive UI.
        # However, button_states wrapper handles the polling dict update? 
        # Usually Base App handles event injection into button_states.
        # To be safe, we just use this for Game Direction.

    def handle_input(self):
        pass # Deprecated by handle_button_down

    def step_game(self):
        if self.mode == "AI_VS_AI":
            ai_dir = self.calculate_ai_direction(self.snake, self.ai_snake)
            if ai_dir:
                self.next_direction = ai_dir

        self.direction = self.next_direction
        
        # Player Move
        p_head = self.snake[0]
        p_next = hex_utils.get_neighbor(p_head[0], p_head[1], self.direction)
        
        # AI Move
        ai_next = None
        if self.mode in ["AI", "AI_VS_AI"] and self.ai_snake:
            ai_next = self.calculate_ai_move(self.ai_snake, self.snake)
            if not ai_next:
                # AI Trapped
                self.ai_snake = [] # Kill AI
                pass
        
        # Check Collision (Player)
        p_dist = (abs(p_next[0]) + abs(p_next[1]) + abs(-p_next[0]-p_next[1])) / 2
        p_crash = (p_dist > self.map_radius or 
                   p_next in self.snake or 
                   (self.mode in ["AI", "AI_VS_AI"] and p_next in self.ai_snake))
        
        # Check Collision (AI)
        ai_crash = False
        if self.mode in ["AI", "AI_VS_AI"] and self.ai_snake and ai_next:
            ai_dist = (abs(ai_next[0]) + abs(ai_next[1]) + abs(-ai_next[0]-ai_next[1])) / 2
            ai_crash = (ai_dist > self.map_radius or 
                        ai_next in self.ai_snake or 
                        ai_next in self.snake)
            
        # Head-on collision
        head_on_collision = (self.mode in ["AI", "AI_VS_AI"] and 
                             self.ai_snake and 
                             ai_next and 
                             p_next == ai_next)
        
        if p_crash or ai_crash or head_on_collision:
            if self.mode in ["AI", "AI_VS_AI"]:
                if not self.ai_snake:
                    self.winner = "RED"
                elif head_on_collision or (p_crash and ai_crash):
                    self.winner = "DRAW"
                elif p_crash:
                    self.winner = "BLUE"
                else:
                    self.winner = "RED"
            else:
                self.winner = None
            self.game_over()
            return

        # Move Player
        self.snake.insert(0, p_next)
        ate_food = False
        if p_next in self.foods:
            self.foods.remove(p_next)
            self.score += 10
            ate_food = True
        else:
            self.snake.pop()
        
        # Move AI
        if self.mode in ["AI", "AI_VS_AI"] and self.ai_snake and ai_next:
            self.ai_snake.insert(0, ai_next)
            if ai_next in self.foods:
                self.foods.remove(ai_next)
                ate_food = True # Respawn food
            else:
                self.ai_snake.pop()
        
        if ate_food:
            self.spawn_food()
            self.tick_speed = max(100, self.tick_speed * 0.98)

    def is_trap(self, start_pos, my_snake, enemy_snake, depth=3):
        # BFS to find if a safe path of length `depth` exists
        # To simulate the snake moving, we approximate the body after moving into start_pos
        temp_my_snake = [start_pos] + my_snake[:-1]
        
        queue = [(start_pos, 0)]
        visited = {start_pos}
        
        while queue:
            curr, d = queue.pop(0)
            if d >= depth:
                return False # Found a safe path of length `depth`!
                
            cq, cr = curr
            cs = -cq - cr
            for direction in hex_utils.ORDERED_DIRECTIONS:
                nxt = hex_utils.get_neighbor(cq, cr, direction)
                if nxt in visited: continue
                
                nq, nr = nxt
                ns = -nq - nr
                dist = (abs(nq) + abs(nr) + abs(ns)) / 2
                
                # Check collision
                if dist > self.map_radius: continue
                if nxt in temp_my_snake: continue
                if enemy_snake and nxt in enemy_snake: continue
                
                visited.add(nxt)
                queue.append((nxt, d + 1))
                
        return True # Checked all reachable moves and couldn't survive `depth` steps!

    def calculate_ai_move(self, my_snake, enemy_snake, predicting=False):
        if not my_snake:
            return None
        head = my_snake[0]
        best_move = None
        min_score = 99999
        
        # Predict enemy's next move to avoid head-on crashes (draws)
        predicted_enemy_move = None
        if self.mode == "AI_VS_AI" and enemy_snake and not predicting:
            # If enemy is player snake, it uses calculate_ai_direction
            if enemy_snake is self.snake:
                enemy_dir = self.calculate_ai_direction(self.snake, self.ai_snake, predicting=True)
                if enemy_dir:
                    predicted_enemy_move = hex_utils.get_neighbor(self.snake[0][0], self.snake[0][1], enemy_dir)
            else:
                # If enemy is AI snake, it uses calculate_ai_move
                predicted_enemy_move = self.calculate_ai_move(self.ai_snake, self.snake, predicting=True)
        
        for d in hex_utils.ORDERED_DIRECTIONS:
            n = hex_utils.get_neighbor(head[0], head[1], d)
            nq, nr = n
            ns = -nq - nr
            
            # Wall check
            dist = (abs(nq) + abs(nr) + abs(ns)) / 2
            is_wall = dist > self.map_radius
            
            # Self check
            is_self = n in my_snake
            
            # Enemy check
            is_enemy = enemy_snake and n in enemy_snake
            
            # Penalties
            collision_penalty = 0
            if is_wall or is_self:
                collision_penalty = 10000
            elif is_enemy:
                collision_penalty = 5000
                
            # Head-on collision (draw) penalty
            if predicted_enemy_move and n == predicted_enemy_move:
                collision_penalty += 3000
                
            # Distance to the best food item (evaluating contested foods)
            d_to_food = 999
            best_food_score = 99999
            if hasattr(self, 'foods') and self.foods:
                for food in self.foods:
                    fq, fr = food
                    fs = -fq - fr
                    fd = (abs(nq - fq) + abs(nr - fr) + abs(ns - fs)) / 2
                    
                    # Check if this food is contested or if we are beaten to it
                    draw_risk_penalty = 0
                    if self.mode == "AI_VS_AI" and enemy_snake:
                        eq, er = enemy_snake[0]
                        es = -eq - er
                        enemy_dist = (abs(eq - fq) + abs(er - fr) + abs(es - fs)) / 2
                        my_dist = (abs(head[0] - fq) + abs(head[1] - fr) + abs(-head[0]-head[1] - fs)) / 2
                        if my_dist == enemy_dist:
                            draw_risk_penalty = 2.0  # contested food
                        elif enemy_dist < my_dist:
                            draw_risk_penalty = 4.0  # beaten to food penalty
                            
                    food_score = fd + draw_risk_penalty
                    if food_score < best_food_score:
                        best_food_score = food_score
                        d_to_food = fd
            else:
                d_to_food = 0
            
            # Distance to enemy and adjacency check
            adj_enemy = 0
            if enemy_snake:
                # Check adjacency to enemy body (repulsion)
                for seg in enemy_snake:
                    sq, sr = seg
                    ss = -sq - sr
                    seg_dist = (abs(nq - sq) + abs(nr - sr) + abs(ns - ss)) / 2
                    if seg_dist == 1:
                        adj_enemy += 1
            
            # Trap look-ahead (forward planning)
            trap_penalty = 0
            if not is_wall and not is_self and not is_enemy:
                if self.is_trap(n, my_snake, enemy_snake, depth=3):
                    trap_penalty = 2000
                        
            if self.mode == "AI_VS_AI":
                score = d_to_food + collision_penalty + adj_enemy * 2.5 + trap_penalty
            else:
                score = d_to_food + collision_penalty + trap_penalty
                
            if score < min_score:
                min_score = score
                best_move = n
        
        return best_move

    def calculate_ai_direction(self, my_snake, enemy_snake, predicting=False):
        if not my_snake:
            return None
        head = my_snake[0]
        best_dir = None
        min_score = 99999
        
        # Predict enemy's next move to avoid head-on crashes (draws)
        predicted_enemy_move = None
        if self.mode == "AI_VS_AI" and enemy_snake and not predicting:
            # If enemy is player snake, it uses calculate_ai_direction
            if enemy_snake is self.snake:
                enemy_dir = self.calculate_ai_direction(self.snake, self.ai_snake, predicting=True)
                if enemy_dir:
                    predicted_enemy_move = hex_utils.get_neighbor(self.snake[0][0], self.snake[0][1], enemy_dir)
            else:
                # If enemy is AI snake, it uses calculate_ai_move
                predicted_enemy_move = self.calculate_ai_move(self.ai_snake, self.snake, predicting=True)
        
        for d in hex_utils.ORDERED_DIRECTIONS:
            n = hex_utils.get_neighbor(head[0], head[1], d)
            nq, nr = n
            ns = -nq - nr
            
            # Wall check
            dist = (abs(nq) + abs(nr) + abs(ns)) / 2
            is_wall = dist > self.map_radius
            
            # Self check
            is_self = n in my_snake
            
            # Enemy check
            is_enemy = enemy_snake and n in enemy_snake
            
            # Penalties
            collision_penalty = 0
            if is_wall or is_self:
                collision_penalty = 10000
            elif is_enemy:
                collision_penalty = 5000
                
            # Head-on collision (draw) penalty
            if predicted_enemy_move and n == predicted_enemy_move:
                collision_penalty += 3000
                
            # Distance to the best food item (evaluating contested foods)
            d_to_food = 999
            best_food_score = 99999
            if hasattr(self, 'foods') and self.foods:
                for food in self.foods:
                    fq, fr = food
                    fs = -fq - fr
                    fd = (abs(nq - fq) + abs(nr - fr) + abs(ns - fs)) / 2
                    
                    # Check if this food is contested or if we are beaten to it
                    draw_risk_penalty = 0
                    if self.mode == "AI_VS_AI" and enemy_snake:
                        eq, er = enemy_snake[0]
                        es = -eq - er
                        enemy_dist = (abs(eq - fq) + abs(er - fr) + abs(es - fs)) / 2
                        my_dist = (abs(head[0] - fq) + abs(head[1] - fr) + abs(-head[0]-head[1] - fs)) / 2
                        if my_dist == enemy_dist:
                            draw_risk_penalty = 2.0  # contested food
                        elif enemy_dist < my_dist:
                            draw_risk_penalty = 4.0  # beaten to food penalty
                            
                    food_score = fd + draw_risk_penalty
                    if food_score < best_food_score:
                        best_food_score = food_score
                        d_to_food = fd
            else:
                d_to_food = 0
            
            # Distance to enemy and adjacency check
            adj_enemy = 0
            if enemy_snake:
                # Check adjacency to enemy body (repulsion)
                for seg in enemy_snake:
                    sq, sr = seg
                    ss = -sq - sr
                    seg_dist = (abs(nq - sq) + abs(nr - sr) + abs(ns - ss)) / 2
                    if seg_dist == 1:
                        adj_enemy += 1
                        
            # Trap look-ahead (forward planning)
            trap_penalty = 0
            if not is_wall and not is_self and not is_enemy:
                if self.is_trap(n, my_snake, enemy_snake, depth=3):
                    trap_penalty = 2000
                        
            if self.mode == "AI_VS_AI":
                score = d_to_food + collision_penalty + adj_enemy * 2.5 + trap_penalty
            else:
                score = d_to_food + collision_penalty + trap_penalty
                
            if score < min_score:
                min_score = score
                best_dir = d
        
        return best_dir

    def game_over(self):
        self.state = "GAME_OVER"
        if self.mode != "AI_VS_AI":
            if self.score > self.high_score:
                self.high_score = self.score

    def draw(self, ctx):
        ctx.save()
        ctx.rgb(0.1, 0.1, 0.1).rectangle(-120, -120, 240, 240).fill()
        
        if self.state == "MENU":
            ctx.rgb(0, 1, 0).move_to(0, -40)
            ctx.text_align = ctx.CENTER
            ctx.text("Snakegon")
            ctx.rgb(1, 1, 1).move_to(0, 0).text(f"High Score: {self.high_score}")
            ctx.rgb(0.5, 0.5, 1).move_to(0, 25).text(f"Mode: {self.mode}")
            ctx.rgb(1, 1, 1).font_size = 15
            ctx.move_to(0, 40).text("(E/B to Toggle)")
            ctx.font_size = 20
            ctx.move_to(0, 60).text("Press C to Start")

        elif self.state == "COUNTDOWN":
            self.draw_game_elements(ctx)
            # Big Number
            # Big Number
            num = math.ceil(self.countdown_timer / 1000)
            ctx.rgb(1, 1, 1).move_to(0, 0)
            ctx.text_align = ctx.CENTER
            ctx.font_size = 40
            ctx.text(str(num))
            ctx.font_size = 20

        elif self.state == "GAME":
            self.draw_game_elements(ctx)
            ctx.rgb(1, 1, 1).move_to(0, -100)
            ctx.text_align = ctx.CENTER
            ctx.text(f"{self.score}")

        elif self.state == "GAME_OVER":
            ctx.rgb(1, 0, 0).move_to(0, -30)
            ctx.text_align = ctx.CENTER
            ctx.text("GAME OVER")
            
            # Show winner if applicable
            if self.mode in ["AI", "AI_VS_AI"] and getattr(self, 'winner', None) is not None:
                ctx.move_to(0, -5)
                if self.winner == "RED":
                    ctx.rgb(1, 0.3, 0.3).text("Red Team Wins!")
                elif self.winner == "BLUE":
                    ctx.rgb(0.3, 0.3, 1).text("Blue Team Wins!")
                elif self.winner == "DRAW":
                    ctx.rgb(1, 1, 1).text("It's a Draw!")
            
            ctx.rgb(1, 1, 1).move_to(0, 20).text(f"Score: {self.score}")
            ctx.move_to(0, 45).text("C to Restart")
            ctx.move_to(0, 65).text("F to Menu")
        
        ctx.restore()

    def draw_game_elements(self, ctx):
        self.draw_grid(ctx)
        
        # Player Snake
        if self.snake:
            # Head: Red Filled
            head = self.snake[0]
            hx, hy = hex_utils.axial_to_pixel(head[0], head[1])
            self.draw_hex(ctx, hx, hy, 1, 0, 0, fill=True)
            
            # Body: Red Outline (Batched)
            if len(self.snake) > 1:
                self.draw_hex_batch(ctx, self.snake[1:], 1, 0, 0)
        
        # AI Snake
        if self.ai_snake:
            # Head: Blue Filled
            head = self.ai_snake[0]
            hx, hy = hex_utils.axial_to_pixel(head[0], head[1])
            self.draw_hex(ctx, hx, hy, 0, 0, 1, fill=True)
            
            # Body: Blue Outline (Batched)
            if len(self.ai_snake) > 1:
                self.draw_hex_batch(ctx, self.ai_snake[1:], 0, 0, 1)
            
        # Draw Food
        if hasattr(self, 'foods'):
            for f in self.foods:
                fx, fy = hex_utils.axial_to_pixel(f[0], f[1])
                self.draw_hex(ctx, fx, fy, 0, 1, 0, fill=True)

    def draw_hex_batch(self, ctx, hex_list, r, g, b):
        ctx.rgb(r, g, b).begin_path()
        size = hex_utils.HEX_SIZE
        
        # Pre-calc offsets (optimization)
        offsets = []
        for i in range(6):
            rad = math.radians(60 * i)
            offsets.append((size * math.cos(rad), size * math.sin(rad)))

        for (q, r_coord) in hex_list:
            cx, cy = hex_utils.axial_to_pixel(q, r_coord)
            ctx.move_to(cx + offsets[0][0], cy + offsets[0][1])
            for i in range(1, 6):
                ctx.line_to(cx + offsets[i][0], cy + offsets[i][1])
            ctx.close_path()
        
        ctx.stroke()



    def calculate_grid_paths(self):
        # Pre-calculate grid centers for mass-drawing
        self.grid_points = []
        for q in range(-self.map_radius, self.map_radius + 1):
            for r in range(-self.map_radius, self.map_radius + 1):
                if (abs(q) + abs(r) + abs(q+r)) / 2 <= self.map_radius:
                     self.grid_points.append(hex_utils.axial_to_pixel(q, r))
        
        # Calculate Boundary Path (Single continuous loop)
        # Vertices of the large Map Hexagon
        # Map Radius R.
        # Corners hexes: (0, -R), (R, -R), (R, 0), (0, R), (-R, R), (-R, 0)
        # Corresponding outer vertices (angles):
        # (0, -R) -> Top Hex. Outer edge is Top edge (240->300 deg)
        # Vertices indices: (240+60*i). -120 is 240. 
        # Flat Top angles: 0, 60, 120, 180, 240, 300.
        # Order of corners (Clockwise starting from Top Right? No, directional order):
        # A(Up, 0,-R), B(UpRight, R,-R), C(DnRight, R,0), D(Dn, 0,R), E(DnLeft, -R,R), F(UpLeft, -R,0)
        
        corners = [
            ((0, -self.map_radius), [240, 300]),   # A: Top Edge
            ((self.map_radius, -self.map_radius), [300, 0]), # B: Top Right Edge
            ((self.map_radius, 0), [0, 60]),       # C: Bottom Right Edge
            ((0, self.map_radius), [60, 120]),     # D: Bottom Edge
            ((-self.map_radius, self.map_radius), [120, 180]), # E: Bottom Left Edge
            ((-self.map_radius, 0), [180, 240])    # F: Top Left Edge
        ]
        
        self.boundary_path = []
        size = hex_utils.HEX_SIZE
        for (q, r), angles in corners:
            cx, cy = hex_utils.axial_to_pixel(q, r)
            for angle in angles:
                rad = math.radians(angle)
                px = cx + size * math.cos(rad)
                py = cy + size * math.sin(rad)
                self.boundary_path.append((px, py))

    def draw_grid(self, ctx):
        # Batched Grid Draw (Grey lines)
        # Split into smaller batches to avoid vertex buffer overflow
        ctx.rgb(0.2, 0.2, 0.2)
        
        size = hex_utils.HEX_SIZE
        # Pre-calc cos/sin for hex shape
        hex_offsets = []
        for i in range(6):
            rad = math.radians(60 * i)
            hex_offsets.append((size * math.cos(rad), size * math.sin(rad)))
        
        batch_size = 10
        count = 0
        
        ctx.begin_path()
        for x, y in self.grid_points:
            # Draw hex at x, y
            ctx.move_to(x + hex_offsets[0][0], y + hex_offsets[0][1])
            for i in range(1, 6):
                 ctx.line_to(x + hex_offsets[i][0], y + hex_offsets[i][1])
            ctx.close_path()
            
            count += 1
            if count >= batch_size:
                ctx.stroke()
                ctx.begin_path()
                count = 0
        
        if count > 0:
            ctx.stroke()
        
        # Draw Single Blue Boundary Loop
        ctx.rgb(0, 0, 1).begin_path() # Blue
        start_x, start_y = self.boundary_path[0]
        ctx.move_to(start_x, start_y)
        for px, py in self.boundary_path[1:]:
             ctx.line_to(px, py)
        ctx.close_path().stroke()

    def draw_hex(self, ctx, x, y, r, g, b, fill=False):
        ctx.rgb(r, g, b).begin_path()
        size = hex_utils.HEX_SIZE
        for i in range(6):
            angle_deg = 60 * i
            angle_rad = math.pi / 180 * angle_deg
            hx = x + size * math.cos(angle_rad)
            hy = y + size * math.sin(angle_rad)
            if i == 0:
                ctx.move_to(hx, hy)
            else:
                ctx.line_to(hx, hy)
        ctx.close_path()
        if fill:
            ctx.fill()
        else:
            ctx.stroke()
        
__app_export__ = SnakegonApp
