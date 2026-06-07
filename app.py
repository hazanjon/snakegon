import app
import math
import random
import time
from events.input import BUTTON_TYPES, ButtonDownEvent, Buttons
from system.eventbus import eventbus

# =============================================================================
# Hex Grid – module-level constants and functions
# =============================================================================

HEX_SIZE = 9
SQRT3 = math.sqrt(3)

DIRECTIONS = {
    "A": (0, -1),   # Up
    "B": (1, -1),   # Up-Right
    "C": (1,  0),   # Down-Right
    "D": (0,  1),   # Down
    "E": (-1, 1),   # Down-Left
    "F": (-1, 0),   # Up-Left
}

ORDERED_DIRECTIONS = ["A", "B", "C", "D", "E", "F"]

OPPOSITES = {"A": "D", "B": "E", "C": "F", "D": "A", "E": "B", "F": "C"}

# Pre-calculated hex vertex offsets – constant, avoids per-frame trig calls
HEX_OFFSETS = [
    (HEX_SIZE * math.cos(math.radians(60 * i)),
     HEX_SIZE * math.sin(math.radians(60 * i)))
    for i in range(6)
]


def axial_to_pixel(q, r):
    return (HEX_SIZE * 1.5 * q,
            HEX_SIZE * (SQRT3 / 2.0 * q + SQRT3 * r))


def get_neighbor(q, r, direction_key):
    dq, dr = DIRECTIONS[direction_key]
    return (q + dq, r + dr)


def hex_distance(q, r):
    """Hex steps from the origin (0, 0) to (q, r)."""
    return (abs(q) + abs(r) + abs(q + r)) // 2


def cube_distance(q1, r1, q2, r2):
    """Hex steps between two axial coordinates."""
    return (abs(q1 - q2) + abs(r1 - r2) + abs((q1 + r1) - (q2 + r2))) // 2


# =============================================================================
# Game Mode / State constants
# =============================================================================

MODE_SOLO     = "SOLO"
MODE_AI       = "AI"
MODE_AI_VS_AI = "AI_VS_AI"

STATE_MENU      = "MENU"
STATE_COUNTDOWN = "COUNTDOWN"
STATE_GAME      = "GAME"
STATE_GAME_OVER = "GAME_OVER"

# =============================================================================
# AI scoring penalty constants
# =============================================================================

PENALTY_WALL_SELF   = 10000
PENALTY_ENEMY_BODY  = 5000
PENALTY_HEAD_ON     = 3000
PENALTY_TRAP        = 2000
PENALTY_CONTESTED   = 2.0
PENALTY_LOSING_RACE = 4.0
ADJ_ENEMY_WEIGHT    = 2.5

FOOD_COUNT         = 3
SPAWN_ATTEMPTS_MAX = 200
TRAP_DEPTH         = 3

# =============================================================================
# Application
# =============================================================================

class SnakegonApp(app.App):
    def __init__(self):
        super().__init__()
        self.button_states = Buttons(self)

        # Game state
        self.state       = STATE_MENU
        self.mode        = MODE_SOLO
        self.score       = 0
        self.high_score  = 0
        self.map_radius  = 7
        self.winner      = None

        # Snake / food
        self.snake    = []
        self.ai_snake = []
        self.foods    = []

        # Movement
        self.direction      = "A"
        self.next_direction = "A"
        self.tick_timer     = 0
        self.tick_speed     = 500
        self.countdown_timer = 0

        # Input
        self.latched_buttons = set()
        self.BUTTON_MAPPING  = {
            "A": "UP",     "B": "RIGHT",   "C": "CONFIRM",
            "D": "DOWN",   "E": "LEFT",    "F": "CANCEL",
            "UP": "UP",    "RIGHT": "RIGHT", "CONFIRM": "CONFIRM",
            "DOWN": "DOWN", "LEFT": "LEFT",  "CANCEL": "CANCEL",
        }

        # Debounce / focus tracking
        self.button_hold_time  = 0
        self.last_toggle_time  = 0
        self.last_state_change = 0
        self.prev_focused      = True

        # Store a strong reference so the handler isn't GC'd
        self._on_down = self.handle_button_down
        eventbus.on(ButtonDownEvent, self._on_down, self)

        # Pre-compute static draw geometry (called once)
        self._calculate_grid_paths()

        print("SnakegonApp initialized")

    # -------------------------------------------------------------------------
    # Game lifecycle
    # -------------------------------------------------------------------------

    def reset_game(self):
        self.snake          = [(0, 0), (0, 1), (0, 2)]
        self.direction      = "A"
        self.next_direction = "A"
        self.score          = 0
        self.winner         = None
        self.tick_speed     = 250 if self.mode == MODE_AI_VS_AI else 500

        if self.mode in (MODE_AI, MODE_AI_VS_AI):
            self.ai_snake = [(3, -3), (3, -4), (3, -5)]
        else:
            self.ai_snake = []

        self.foods = []
        self._spawn_food()
        self.countdown_timer = 3000
        self.state = STATE_COUNTDOWN
        self.latched_buttons.clear()

    def _spawn_food(self):
        """Fill self.foods up to FOOD_COUNT, guarded against infinite loops."""
        attempts = 0
        while len(self.foods) < FOOD_COUNT and attempts < SPAWN_ATTEMPTS_MAX:
            attempts += 1
            q  = random.randint(-self.map_radius, self.map_radius)
            r1 = max(-self.map_radius, -q - self.map_radius)
            r2 = min(self.map_radius,  -q + self.map_radius)
            r  = random.randint(r1, r2)
            tile = (q, r)
            if tile not in self.snake and tile not in self.ai_snake and tile not in self.foods:
                self.foods.append(tile)

    def game_over(self):
        self.state = STATE_GAME_OVER
        if self.mode != MODE_AI_VS_AI and self.score > self.high_score:
            self.high_score = self.score

    # -------------------------------------------------------------------------
    # Update loop
    # -------------------------------------------------------------------------

    def update(self, delta):
        # Focus recovery: clear stuck inputs when app regains foreground
        curr_focused  = getattr(self, '_focused', True)
        was_unfocused = not getattr(self, 'prev_focused', True)
        self.prev_focused = curr_focused
        if curr_focused and was_unfocused:
            self.button_states.clear()
            self.latched_buttons.clear()
            self.last_state_change = time.ticks_ms()

        # Capture and clear latch atomically before any state logic reads it
        captured_latch = self.latched_buttons.copy()
        self.latched_buttons.clear()

        # Game direction input is processed before the tick so queued turns apply
        if self.state == STATE_GAME:
            for logical in captured_latch:
                self._handle_game_input(logical)

        # Exit combo: hold any opposing button pair for 2 s to return to menu
        if self._check_exit_combo(delta):
            self.state = STATE_MENU
            self.last_state_change = time.ticks_ms()
            return True

        if self.state == STATE_MENU:
            if self._check_btn("CONFIRM", BUTTON_TYPES["CONFIRM"], captured_latch):
                self.reset_game()
            if self._check_btn("CANCEL", BUTTON_TYPES["CANCEL"], captured_latch):
                if time.ticks_ms() - self.last_state_change > 500:
                    self.minimise()
            now = time.ticks_ms()
            if (self._check_btn("RIGHT", BUTTON_TYPES["RIGHT"], captured_latch) or
                    self._check_btn("LEFT",  BUTTON_TYPES["LEFT"],  captured_latch)):
                if now - self.last_toggle_time > 300:
                    modes = [MODE_SOLO, MODE_AI, MODE_AI_VS_AI]
                    self.mode = modes[(modes.index(self.mode) + 1) % len(modes)]
                    self.last_toggle_time = now

        elif self.state == STATE_COUNTDOWN:
            self.countdown_timer -= delta
            if self.countdown_timer <= 0:
                self.state = STATE_GAME

        elif self.state == STATE_GAME:
            self.tick_timer += delta
            if self.tick_timer >= self.tick_speed:
                self.tick_timer = 0
                self._step_game()

        elif self.state == STATE_GAME_OVER:
            if self._check_btn("CONFIRM", BUTTON_TYPES["CONFIRM"], captured_latch):
                self.reset_game()
            if self._check_btn("CANCEL", BUTTON_TYPES["CANCEL"], captured_latch):
                self.state = STATE_MENU
                self.last_state_change = time.ticks_ms()

        return True

    def _check_btn(self, logical_name, btn_type, captured_latch):
        """True if the button was tapped this frame OR is currently held."""
        return (logical_name in captured_latch) or self.button_states.get(btn_type)

    def _handle_game_input(self, name):
        mapping = {
            "UP": "A", "RIGHT": "B", "CONFIRM": "C",
            "DOWN": "D", "LEFT": "E", "CANCEL": "F",
        }
        new_dir = mapping.get(name)
        if new_dir and new_dir != OPPOSITES.get(self.direction):
            self.next_direction = new_dir

    def _check_exit_combo(self, delta):
        pair1 = (self.button_states.get(BUTTON_TYPES["UP"])
                 and self.button_states.get(BUTTON_TYPES["DOWN"]))
        pair2 = (self.button_states.get(BUTTON_TYPES["RIGHT"])
                 and self.button_states.get(BUTTON_TYPES["LEFT"]))
        pair3 = (self.button_states.get(BUTTON_TYPES["CONFIRM"])
                 and self.button_states.get(BUTTON_TYPES["CANCEL"]))
        if pair1 or pair2 or pair3:
            self.button_hold_time += delta
            if self.button_hold_time > 2000:
                self.button_hold_time = 0
                return True
        else:
            self.button_hold_time = 0
        return False

    def handle_button_down(self, event):
        logical = self.BUTTON_MAPPING.get(event.button.name)
        if logical:
            self.latched_buttons.add(logical)

    # -------------------------------------------------------------------------
    # Game step
    # -------------------------------------------------------------------------

    def _step_game(self):
        # In AI_VS_AI the player snake is also AI-driven
        if self.mode == MODE_AI_VS_AI:
            ai_dir = self._score_ai_moves(self.snake, self.ai_snake, return_direction=True)
            if ai_dir:
                self.next_direction = ai_dir

        self.direction = self.next_direction

        p_head = self.snake[0]
        p_next = get_neighbor(p_head[0], p_head[1], self.direction)

        ai_next = None
        if self.mode in (MODE_AI, MODE_AI_VS_AI) and self.ai_snake:
            ai_next = self._score_ai_moves(self.ai_snake, self.snake)
            if not ai_next:
                self.ai_snake = []  # AI fully trapped

        # Collision detection
        p_crash = (hex_distance(p_next[0], p_next[1]) > self.map_radius
                   or p_next in self.snake
                   or (self.mode in (MODE_AI, MODE_AI_VS_AI) and p_next in self.ai_snake))

        ai_crash = False
        if self.mode in (MODE_AI, MODE_AI_VS_AI) and self.ai_snake and ai_next:
            ai_crash = (hex_distance(ai_next[0], ai_next[1]) > self.map_radius
                        or ai_next in self.ai_snake
                        or ai_next in self.snake)

        head_on = (self.mode in (MODE_AI, MODE_AI_VS_AI)
                   and self.ai_snake and ai_next and p_next == ai_next)

        if p_crash or ai_crash or head_on:
            if self.mode in (MODE_AI, MODE_AI_VS_AI):
                if not self.ai_snake:
                    self.winner = "RED"
                elif head_on or (p_crash and ai_crash):
                    self.winner = "DRAW"
                elif p_crash:
                    self.winner = "BLUE"
                else:
                    self.winner = "RED"
            else:
                self.winner = None
            self.game_over()
            return

        # Advance player snake
        self.snake.insert(0, p_next)
        ate_food = False
        if p_next in self.foods:
            self.foods.remove(p_next)
            self.score += 10
            ate_food = True
        else:
            self.snake.pop()

        # Advance AI snake
        if self.mode in (MODE_AI, MODE_AI_VS_AI) and self.ai_snake and ai_next:
            self.ai_snake.insert(0, ai_next)
            if ai_next in self.foods:
                self.foods.remove(ai_next)
                ate_food = True
            else:
                self.ai_snake.pop()

        if ate_food:
            self._spawn_food()
            self.tick_speed = max(100, self.tick_speed * 0.98)

    # -------------------------------------------------------------------------
    # AI
    # -------------------------------------------------------------------------

    def _is_trap(self, start_pos, my_snake, enemy_snake, depth=TRAP_DEPTH):
        """BFS: return True if no escape path of `depth` steps exists from start_pos."""
        temp_snake = [start_pos] + my_snake[:-1]
        queue   = [(start_pos, 0)]
        visited = {start_pos}

        while queue:
            curr, d = queue.pop(0)
            if d >= depth:
                return False  # Found an escape path of sufficient length

            cq, cr = curr
            for direction in ORDERED_DIRECTIONS:
                nxt = get_neighbor(cq, cr, direction)
                if nxt in visited:
                    continue
                nq, nr = nxt
                if hex_distance(nq, nr) > self.map_radius:
                    continue
                if nxt in temp_snake:
                    continue
                if enemy_snake and nxt in enemy_snake:
                    continue
                visited.add(nxt)
                queue.append((nxt, d + 1))

        return True  # No escape path found — this is a trap

    def _score_ai_moves(self, my_snake, enemy_snake, predicting=False, return_direction=False):
        """
        Shared scoring engine for both AI snakes.

        Evaluates all 6 neighbours and returns either:
          - the best neighbour coordinate  (return_direction=False, default)
          - the direction key to move in   (return_direction=True)

        Used for both the AI_VS_AI 'player' snake and the AI opponent.
        """
        if not my_snake:
            return None

        hq, hr   = my_snake[0]
        best      = None
        min_score = float('inf')

        # Predict the enemy's next move once so we can penalise head-on collisions
        predicted_enemy_pos = None
        if self.mode == MODE_AI_VS_AI and enemy_snake and not predicting:
            if enemy_snake is self.snake:
                # Enemy is the player snake (also AI-controlled in AI_VS_AI)
                d = self._score_ai_moves(self.snake, self.ai_snake,
                                         predicting=True, return_direction=True)
                if d:
                    eh, er = self.snake[0]
                    predicted_enemy_pos = get_neighbor(eh, er, d)
            else:
                # Enemy is the AI snake
                predicted_enemy_pos = self._score_ai_moves(
                    self.ai_snake, self.snake, predicting=True)

        for d in ORDERED_DIRECTIONS:
            n   = get_neighbor(hq, hr, d)
            nq, nr = n

            is_wall  = hex_distance(nq, nr) > self.map_radius
            is_self  = n in my_snake
            is_enemy = bool(enemy_snake) and n in enemy_snake

            # --- Collision penalties ---
            collision_penalty = 0
            if is_wall or is_self:
                collision_penalty = PENALTY_WALL_SELF
            elif is_enemy:
                collision_penalty = PENALTY_ENEMY_BODY

            if predicted_enemy_pos and n == predicted_enemy_pos:
                collision_penalty += PENALTY_HEAD_ON

            # --- Food distance with contest awareness ---
            d_to_food = 0 if not self.foods else 999
            if self.foods:
                best_food_score = float('inf')
                for food in self.foods:
                    fq, fr = food
                    fd = cube_distance(nq, nr, fq, fr)

                    draw_penalty = 0
                    if self.mode == MODE_AI_VS_AI and enemy_snake:
                        eq, er = enemy_snake[0]
                        my_dist = cube_distance(hq, hr, fq, fr)
                        en_dist = cube_distance(eq, er, fq, fr)
                        if my_dist == en_dist:
                            draw_penalty = PENALTY_CONTESTED
                        elif en_dist < my_dist:
                            draw_penalty = PENALTY_LOSING_RACE

                    food_score = fd + draw_penalty
                    if food_score < best_food_score:
                        best_food_score = food_score
                        d_to_food = fd

            # --- Enemy adjacency repulsion ---
            adj_enemy = 0
            if self.mode == MODE_AI_VS_AI and enemy_snake:
                for seg in enemy_snake:
                    if cube_distance(nq, nr, seg[0], seg[1]) == 1:
                        adj_enemy += 1

            # --- Trap look-ahead (skip if the move is already fatal) ---
            trap_penalty = 0
            if not is_wall and not is_self and not is_enemy:
                if self._is_trap(n, my_snake, enemy_snake):
                    trap_penalty = PENALTY_TRAP

            if self.mode == MODE_AI_VS_AI:
                score = d_to_food + collision_penalty + adj_enemy * ADJ_ENEMY_WEIGHT + trap_penalty
            else:
                score = d_to_food + collision_penalty + trap_penalty

            if score < min_score:
                min_score = score
                best = d if return_direction else n

        return best

    # -------------------------------------------------------------------------
    # Drawing
    # -------------------------------------------------------------------------

    def draw(self, ctx):
        ctx.save()
        ctx.rgb(0.1, 0.1, 0.1).rectangle(-120, -120, 240, 240).fill()

        if self.state == STATE_MENU:
            ctx.text_align = ctx.CENTER
            ctx.rgb(0, 1, 0).move_to(0, -40).text("Snakegon")
            ctx.rgb(1, 1, 1).move_to(0, 0).text(f"High Score: {self.high_score}")
            ctx.rgb(0.5, 0.5, 1).move_to(0, 25).text(f"Mode: {self.mode}")
            ctx.rgb(1, 1, 1)
            ctx.font_size = 15
            ctx.move_to(0, 40).text("(E/B to Toggle)")
            ctx.font_size = 20
            ctx.move_to(0, 60).text("Press C to Start")

        elif self.state == STATE_COUNTDOWN:
            self._draw_game_elements(ctx)
            num = math.ceil(self.countdown_timer / 1000)
            ctx.rgb(1, 1, 1).move_to(0, 0)
            ctx.text_align = ctx.CENTER
            ctx.font_size = 40
            ctx.text(str(num))
            ctx.font_size = 20

        elif self.state == STATE_GAME:
            self._draw_game_elements(ctx)
            ctx.rgb(1, 1, 1).move_to(0, -100)
            ctx.text_align = ctx.CENTER
            ctx.text(f"{self.score}")

        elif self.state == STATE_GAME_OVER:
            ctx.text_align = ctx.CENTER
            ctx.rgb(1, 0, 0).move_to(0, -30).text("GAME OVER")

            winner = self.winner
            if self.mode in (MODE_AI, MODE_AI_VS_AI) and winner is not None:
                ctx.move_to(0, -5)
                if winner == "RED":
                    ctx.rgb(1, 0.3, 0.3).text("Red Team Wins!")
                elif winner == "BLUE":
                    ctx.rgb(0.3, 0.3, 1).text("Blue Team Wins!")
                elif winner == "DRAW":
                    ctx.rgb(1, 1, 1).text("It's a Draw!")

            ctx.rgb(1, 1, 1)
            ctx.move_to(0, 20).text(f"Score: {self.score}")
            ctx.move_to(0, 45).text("C to Restart")
            ctx.move_to(0, 65).text("F to Menu")

        ctx.restore()

    def _draw_game_elements(self, ctx):
        self._draw_grid(ctx)

        # Player snake – red
        if self.snake:
            hx, hy = axial_to_pixel(*self.snake[0])
            self._draw_hex(ctx, hx, hy, 1, 0, 0, fill=True)
            if len(self.snake) > 1:
                self._draw_hex_batch(ctx, self.snake[1:], 1, 0, 0)

        # AI snake – blue
        if self.ai_snake:
            hx, hy = axial_to_pixel(*self.ai_snake[0])
            self._draw_hex(ctx, hx, hy, 0, 0, 1, fill=True)
            if len(self.ai_snake) > 1:
                self._draw_hex_batch(ctx, self.ai_snake[1:], 0, 0, 1)

        # Food – green
        for f in self.foods:
            fx, fy = axial_to_pixel(*f)
            self._draw_hex(ctx, fx, fy, 0, 1, 0, fill=True)

    def _draw_hex(self, ctx, x, y, r, g, b, fill=False):
        """Draw a single filled or outlined hex using pre-cached offsets."""
        o = HEX_OFFSETS
        ctx.rgb(r, g, b).begin_path()
        ctx.move_to(x + o[0][0], y + o[0][1])
        for i in range(1, 6):
            ctx.line_to(x + o[i][0], y + o[i][1])
        ctx.close_path()
        if fill:
            ctx.fill()
        else:
            ctx.stroke()

    def _draw_hex_batch(self, ctx, hex_list, r, g, b):
        """Draw a batch of outlined hexes in a single colour in one path call."""
        o = HEX_OFFSETS
        ctx.rgb(r, g, b).begin_path()
        for (q, rc) in hex_list:
            cx, cy = axial_to_pixel(q, rc)
            ctx.move_to(cx + o[0][0], cy + o[0][1])
            for i in range(1, 6):
                ctx.line_to(cx + o[i][0], cy + o[i][1])
            ctx.close_path()
        ctx.stroke()

    def _draw_grid(self, ctx):
        """Draw the background hex grid and boundary outline."""
        o = HEX_OFFSETS
        ctx.rgb(0.2, 0.2, 0.2)
        batch_size = 10
        count = 0
        ctx.begin_path()
        for x, y in self.grid_points:
            ctx.move_to(x + o[0][0], y + o[0][1])
            for i in range(1, 6):
                ctx.line_to(x + o[i][0], y + o[i][1])
            ctx.close_path()
            count += 1
            if count >= batch_size:
                ctx.stroke()
                ctx.begin_path()
                count = 0
        if count > 0:
            ctx.stroke()

        # Map boundary – bright blue outline
        ctx.rgb(0, 0, 1).begin_path()
        sx, sy = self.boundary_path[0]
        ctx.move_to(sx, sy)
        for px, py in self.boundary_path[1:]:
            ctx.line_to(px, py)
        ctx.close_path().stroke()

    def _calculate_grid_paths(self):
        """Pre-compute hex grid centres and boundary vertices (called once in __init__)."""
        self.grid_points = []
        for q in range(-self.map_radius, self.map_radius + 1):
            for r in range(-self.map_radius, self.map_radius + 1):
                if hex_distance(q, r) <= self.map_radius:
                    self.grid_points.append(axial_to_pixel(q, r))

        corners = [
            ((0,               -self.map_radius), [240, 300]),
            ((self.map_radius,  -self.map_radius), [300, 0]),
            ((self.map_radius,   0),               [0,   60]),
            ((0,                self.map_radius),  [60,  120]),
            ((-self.map_radius, self.map_radius),  [120, 180]),
            ((-self.map_radius, 0),                [180, 240]),
        ]
        self.boundary_path = []
        for (q, r), angles in corners:
            cx, cy = axial_to_pixel(q, r)
            for angle in angles:
                rad = math.radians(angle)
                self.boundary_path.append(
                    (cx + HEX_SIZE * math.cos(rad),
                     cy + HEX_SIZE * math.sin(rad))
                )


__app_export__ = SnakegonApp
