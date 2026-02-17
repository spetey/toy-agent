#!/usr/bin/env python3
"""
ifbc-02.py — ifb to fb2d compiler, v0.2.1

Authored or modified by Claude Opus 4.6, 2026-02-16
Based on ifbc.py v0.1 (2026-02-13)

Compiles a minimal reversible imperative language (ifb) to fb2d grid files.
NEW in v0.2: nested while loops.
NEW in v0.2.1: zero keyword (Z opcode), no canonical resets (minimal head moves).

Grid layout (extends v0.1 with 2 rows per nesting level):
  Row 0: variables (one per column)
  Row 1: level-0 body row (opcodes going West)
  Row 2: level-0 code row (IP goes East; ( P % here)
  Row 3: GP trail (zeros, GP starts at left end)
  Row 4: level-1 body row (going West)
  Row 5: level-1 code row (going East; inner ( P % here)
  Row 6: level-2 body row ...
  Row 7: level-2 code row ...
  ...

Nested loop strategy:
  - Every loop uses ( P ... % with GP breadcrumbs.
  - GP advances monotonically (] before each inner (, ] on each exit).
  - Fresh GP cells (value 0) are consumed by P — "burning zeroes."
  - Outer ( relies on inner P leaving grid[GP]!=0 for re-entry detection.
  - Each nesting level uses its own pair of rows for body/code.
  - Transitions between levels use / \\ mirror pairs.
"""

import sys
import re

# ── fb2d opcode values (must match fb2d.py) ────────────────────────
OP = {
    '/':  1,   '\\': 2,
    '%':  3,   '?':  4,   '&':  5,   '!':  6,
    'N':  7,   'S':  8,   'E':  9,   'W': 10,
    'n': 11,   's': 12,   'e': 13,   'w': 14,
    '+': 15,   '-': 16,   '.': 17,   ',': 18,
    'x': 19,   'F': 20,   'G': 21,   'T': 22,
    '>': 23,   '<': 24,   '^': 25,   'v': 26,
    'P': 27,   'Q': 28,   ']': 29,   '[': 30,
    '}': 31,   '{': 32,   'K': 33,   '(': 34,   ')': 35,
    '#': 36,   '$': 37,
    'Z': 38,
}

OPCODE_TO_CHAR = {v: k for k, v in OP.items()}

# Layout constants
DATA_ROW = 0
GP_ROW   = 3

def body_row(level):
    """Row for loop body at given nesting level."""
    return 1 + level * 2

def code_row(level):
    """Row for loop control/code at given nesting level."""
    return 2 + level * 2


# ── AST node types ────────────────────────────────────────────────

class VarDecl:
    """var x = N"""
    def __init__(self, name, value=0):
        self.name = name
        self.value = value
    def __repr__(self):
        return f"VarDecl({self.name}, {self.value})"

class AddConst:
    """x += N"""
    def __init__(self, var, n):
        self.var = var
        self.n = n
    def __repr__(self):
        return f"AddConst({self.var}, {self.n})"

class SubConst:
    """x -= N"""
    def __init__(self, var, n):
        self.var = var
        self.n = n
    def __repr__(self):
        return f"SubConst({self.var}, {self.n})"

class AddVar:
    """x += y"""
    def __init__(self, target, source):
        self.target = target
        self.source = source
    def __repr__(self):
        return f"AddVar({self.target}, {self.source})"

class SubVar:
    """x -= y"""
    def __init__(self, target, source):
        self.target = target
        self.source = source
    def __repr__(self):
        return f"SubVar({self.target}, {self.source})"

class Swap:
    """swap x y"""
    def __init__(self, x, y):
        self.x = x
        self.y = y
    def __repr__(self):
        return f"Swap({self.x}, {self.y})"

class ZeroVar:
    """zero x"""
    def __init__(self, var):
        self.var = var
    def __repr__(self):
        return f"ZeroVar({self.var})"

class While:
    """while x do ... end"""
    def __init__(self, var, body, negated=False):
        self.var = var
        self.body = body      # list of statements
        self.negated = negated # while !x do
    def __repr__(self):
        return f"While({self.var}, neg={self.negated}, body={self.body})"

class Program:
    def __init__(self, stmts):
        self.stmts = stmts

# ── Parser ────────────────────────────────────────────────────────

def tokenize(source):
    """Split source into tokens, stripping comments."""
    tokens = []
    for line in source.split('\n'):
        line = line.split('//')[0].strip()
        if not line:
            continue
        parts = re.findall(r'\+=|-=|[a-zA-Z_]\w*|\d+|[!=]', line)
        tokens.extend(parts)
    return tokens

def parse(source):
    """Parse ifb source into a Program AST."""
    tokens = tokenize(source)
    pos = [0]

    def peek():
        return tokens[pos[0]] if pos[0] < len(tokens) else None

    def advance():
        tok = tokens[pos[0]]
        pos[0] += 1
        return tok

    def expect(tok):
        got = advance()
        if got != tok:
            raise SyntaxError(f"Expected '{tok}', got '{got}' at token {pos[0]-1}")
        return got

    def parse_stmts(stop_tokens=None):
        stmts = []
        while pos[0] < len(tokens):
            tok = peek()
            if stop_tokens and tok in stop_tokens:
                break
            stmts.append(parse_stmt())
        return stmts

    def parse_stmt():
        tok = peek()
        if tok == 'var':
            advance()
            name = advance()
            value = 0
            if peek() == '=':
                advance()
                value = int(advance())
            return VarDecl(name, value)
        elif tok == 'swap':
            advance()
            x = advance()
            y = advance()
            return Swap(x, y)
        elif tok == 'zero':
            advance()
            var = advance()
            return ZeroVar(var)
        elif tok == 'while':
            advance()
            negated = False
            if peek() == '!':
                advance()
                negated = True
            var = advance()
            expect('do')
            body = parse_stmts(stop_tokens={'end'})
            expect('end')
            return While(var, body, negated)
        else:
            var = advance()
            op = advance()
            rhs = advance()
            if op == '+=':
                try:
                    return AddConst(var, int(rhs))
                except ValueError:
                    return AddVar(var, rhs)
            elif op == '-=':
                try:
                    return SubConst(var, int(rhs))
                except ValueError:
                    return SubVar(var, rhs)
            else:
                raise SyntaxError(f"Unknown operator '{op}'")

    stmts = parse_stmts()
    return Program(stmts)


# ── Compiler ──────────────────────────────────────────────────────

class Compiler:
    """Compiles a Program AST to a fb2d grid."""

    def __init__(self):
        self.variables = {}    # name -> column index in DATA_ROW
        self.grid = {}         # (row, col) -> opcode value
        self.data_values = {}  # column -> initial value

        # Head tracking (positions in grid coordinates)
        self.h0 = (DATA_ROW, 0)
        self.h1 = (DATA_ROW, 0)
        self.cl = (DATA_ROW, 0)

        # Code cursor: current column on CODE_ROW where next opcode goes
        self.code_col = 0

        # Track max column and max nesting level used
        self.max_col = 0
        self.max_level = 0

    def compile(self, program):
        """Compile program to grid. Returns (rows, cols, grid_flat, header_dict)."""
        # Pass 1: collect variable declarations
        for stmt in program.stmts:
            if isinstance(stmt, VarDecl):
                col = len(self.variables)
                self.variables[stmt.name] = col
                self.data_values[col] = stmt.value

        if not self.variables:
            self.variables['_dummy'] = 0
            self.data_values[0] = 0

        num_vars = len(self.variables)

        # Pass 2: generate code
        for stmt in program.stmts:
            if isinstance(stmt, VarDecl):
                continue
            self._compile_stmt(stmt, level=0)

        # Determine grid size
        # Need enough rows for all nesting levels plus GP row
        min_rows = max(GP_ROW + 1, code_row(self.max_level) + 1)
        cols = max(self.max_col + 2, num_vars + 1, 8)
        rows = max(min_rows, 4)

        # Build flat grid
        grid_flat = [0] * (rows * cols)

        # Place variable initial values
        for col_idx, value in self.data_values.items():
            grid_flat[DATA_ROW * cols + col_idx] = value

        # Place compiled opcodes
        for (r, c), val in self.grid.items():
            if 0 <= r < rows and 0 <= c < cols:
                grid_flat[r * cols + c] = val

        # Header info
        gp_flat = GP_ROW * cols + 0
        header = {
            'rows': rows,
            'cols': cols,
            'ip_row': code_row(0),
            'ip_col': 0,
            'ip_dir': 1,   # East
            'cl': DATA_ROW * cols + 0,
            'h0': DATA_ROW * cols + 0,
            'h1': DATA_ROW * cols + 0,
            'gp': gp_flat,
            'step': 0,
        }

        return rows, cols, grid_flat, header

    def _emit(self, row, col, opcode_char):
        """Place an opcode on the grid."""
        self.grid[(row, col)] = OP[opcode_char]
        self.max_col = max(self.max_col, col)

    def _emit_at_cursor(self, opcode_char, level):
        """Emit an opcode on the code row for the given level at the current cursor."""
        self._emit(code_row(level), self.code_col, opcode_char)
        self.code_col += 1

    def _var_col(self, name):
        """Get the column of a variable."""
        if name not in self.variables:
            raise NameError(f"Undefined variable '{name}'")
        return self.variables[name]

    # ── Head movement helpers ─────────────────────────────────────

    def _move_h0_to(self, target_row, target_col, emit_fn):
        cr, cc = self.h0
        while cr < target_row:
            emit_fn('S'); cr += 1
        while cr > target_row:
            emit_fn('N'); cr -= 1
        while cc < target_col:
            emit_fn('E'); cc += 1
        while cc > target_col:
            emit_fn('W'); cc -= 1
        self.h0 = (target_row, target_col)

    def _move_h1_to(self, target_row, target_col, emit_fn):
        cr, cc = self.h1
        while cr < target_row:
            emit_fn('s'); cr += 1
        while cr > target_row:
            emit_fn('n'); cr -= 1
        while cc < target_col:
            emit_fn('e'); cc += 1
        while cc > target_col:
            emit_fn('w'); cc -= 1
        self.h1 = (target_row, target_col)

    def _move_cl_to(self, target_row, target_col, emit_fn):
        cr, cc = self.cl
        while cr < target_row:
            emit_fn('v'); cr += 1
        while cr > target_row:
            emit_fn('^'); cr -= 1
        while cc < target_col:
            emit_fn('>'); cc += 1
        while cc > target_col:
            emit_fn('<'); cc -= 1
        self.cl = (target_row, target_col)

    def _canonical_reset(self, emit_fn):
        """Reset H0, H1, CL to canonical home (DATA_ROW, 0)."""
        home = (DATA_ROW, 0)
        self._move_h0_to(*home, emit_fn)
        self._move_h1_to(*home, emit_fn)
        self._move_cl_to(*home, emit_fn)

    # ── Statement compilation ─────────────────────────────────────

    def _compile_stmt(self, stmt, level):
        """Compile a single statement at the given nesting level."""
        code_r = code_row(level)

        def emit_code(opcode_char):
            self._emit(code_r, self.code_col, opcode_char)
            self.code_col += 1

        if isinstance(stmt, AddConst):
            col = self._var_col(stmt.var)
            self._move_h0_to(DATA_ROW, col, emit_code)
            for _ in range(stmt.n):
                emit_code('+')
        elif isinstance(stmt, SubConst):
            col = self._var_col(stmt.var)
            self._move_h0_to(DATA_ROW, col, emit_code)
            for _ in range(stmt.n):
                emit_code('-')
        elif isinstance(stmt, AddVar):
            tcol = self._var_col(stmt.target)
            scol = self._var_col(stmt.source)
            self._move_h0_to(DATA_ROW, tcol, emit_code)
            self._move_h1_to(DATA_ROW, scol, emit_code)
            emit_code('.')
        elif isinstance(stmt, SubVar):
            tcol = self._var_col(stmt.target)
            scol = self._var_col(stmt.source)
            self._move_h0_to(DATA_ROW, tcol, emit_code)
            self._move_h1_to(DATA_ROW, scol, emit_code)
            emit_code(',')
        elif isinstance(stmt, Swap):
            xcol = self._var_col(stmt.x)
            ycol = self._var_col(stmt.y)
            self._move_h0_to(DATA_ROW, xcol, emit_code)
            self._move_h1_to(DATA_ROW, ycol, emit_code)
            emit_code('x')
        elif isinstance(stmt, While):
            self._compile_while(stmt, level)
        else:
            raise TypeError(f"Unknown statement type: {type(stmt)}")

    def _compile_while(self, stmt, level):
        """Compile a while loop at the given nesting level.

        Layout at this level:
          body_row(level): /  [body_n ... body_1]  \\     (body goes West)
          code_row(level): [reset] (  P  [cl_moves] [pad]  %  ]  [cl_reset]

        For nested while loops inside the body, the body opcodes include
        transitions down to the next level:
          \\  (drop from body_row to next level's code_row, going E)
          ... inner loop at level+1 ...
          /  (return from next level's code_row up to body_row, going W)
        """
        self.max_level = max(self.max_level, level)
        var_col = self._var_col(stmt.var)
        code_r = code_row(level)
        body_r = body_row(level)

        def emit_code(opcode_char):
            self._emit(code_r, self.code_col, opcode_char)
            self.code_col += 1

        # ── Step 0: Canonical reset before loop entry ──
        self._canonical_reset(emit_code)

        col_L = self.code_col  # loop starts here

        # ── Step 1: Compile the body to get its opcodes ──
        body_ops = self._compile_body_ops(stmt.body, var_col, level)
        body_width = len(body_ops)

        # ── Step 2: CL moves on code row ──
        cl_move_ops = []
        for _ in range(var_col):
            cl_move_ops.append('>')
        cl_moves_width = len(cl_move_ops)

        # ── Step 3: Rectangle dimensions ──
        ctrl_inner = 1 + cl_moves_width  # P + cl_moves
        inner_width = max(body_width, ctrl_inner)
        col_R = col_L + inner_width + 1

        # ── Step 4: Body row ──
        self._emit(body_r, col_L, '/')
        self._emit(body_r, col_R, '\\')
        for i, op_char in enumerate(body_ops):
            body_col = col_R - 1 - i
            self._emit(body_r, body_col, op_char)

        # ── Step 5: Code row ──
        self._emit(code_r, col_L, '(')
        self._emit(code_r, col_L + 1, 'P')
        for i, op_char in enumerate(cl_move_ops):
            self._emit(code_r, col_L + 2 + i, op_char)
        mirror = '?' if stmt.negated else '%'
        self._emit(code_r, col_R, mirror)
        self._emit(code_r, col_R + 1, ']')

        # ── Step 6: Update cursor and state ──
        self.code_col = col_R + 2

        # CL reset after loop exit
        self._move_cl_to(DATA_ROW, 0, emit_code)

    def _compile_body_ops(self, body_stmts, loop_var_col, level):
        """Compile loop body statements, returning a list of opcode chars.

        The body executes going West on body_row(level).
        For inner while loops, we:
          1. Emit ']' to advance GP to a fresh column
          2. Emit transition opcodes (drop to level+1, run inner loop, return)
          3. The transition is: \\ on body_row to go S, inner code on code_row(level+1),
             / on body_row to return going W.

        But since the body row opcodes are laid out right-to-left (going W),
        the \\  for entering a nested loop is to the RIGHT and / for returning
        is to the LEFT.

        Actually, let me reconsider. The body opcodes are collected as a list
        in execution order (first executed = index 0). They are then placed
        right-to-left on the body row (index 0 at col_R-1, going left).

        For an inner while loop, we need to:
        - Drop the IP from the body row (going W) down to the inner code row
        - Execute the inner loop on code_row(level+1) going E
        - Return the IP back up to the body row (going W)

        The IP is going W on body_row(level). To go down:
        - / mirror: W→S. IP goes S to body_row(level)+1 = code_row(level).
          But code_row(level) might have outer loop opcodes. Skip.
          Keep going S to GP_ROW, then to body_row(level+1)... messy.

        Better approach: use the body_row as the lane, and for inner loops,
        drop through empty rows using vertical mirror columns.

        Simplest approach for v0.2: lay out inner loops INLINE on the code row
        by "unrolling" the nesting. The body_ops list for an inner while gets
        replaced with a transition sequence that drops to the inner level's
        rows, which are placed at different columns.

        Actually, the cleanest approach: inner while loops get compiled
        DIRECTLY onto their own row pair, at the current code_col position.
        The body opcodes include transition markers that we replace with
        actual mirror placements.

        Let me think about the geometry more carefully...

        Current approach for v0.2:
        - An inner while loop inside the body gets compiled at level+1.
        - Its code_row(level+1) and body_row(level+1) are used.
        - On the outer body_row(level), we place:
            / (at the inner loop's col_R) to catch the return (N→W? No...)

        OK let me think about this concretely.

        The outer body row is body_row(level) = 1 + level*2.
        The outer code row is code_row(level) = 2 + level*2.
        The inner body row is body_row(level+1) = 3 + level*2.
        The inner code row is code_row(level+1) = 4 + level*2.

        The gap between body_row(level) and code_row(level+1) is:
          code_row(level+1) - body_row(level) = (4+level*2) - (1+level*2) = 3

        So there are 2 rows in between: code_row(level) and GP_ROW/body_row(level+1).

        Wait, for level=0:
          body_row(0) = 1
          code_row(0) = 2
          GP_ROW = 3
          body_row(1) = 3  ← COLLISION with GP_ROW!

        This is a problem. GP_ROW=3 conflicts with body_row(1)=3.

        Fix: put GP_ROW AFTER all the code/body rows, or interleave differently.

        New layout strategy:
          Row 0: DATA
          Row 1: level-0 body (going W)
          Row 2: level-0 code (going E)
          Row 3: level-1 body (going W)
          Row 4: level-1 code (going E)
          ...
          Row 2*max_level+1: deepest body
          Row 2*max_level+2: deepest code
          Last row: GP trail

        With this layout:
          body_row(L) = 1 + 2*L
          code_row(L) = 2 + 2*L
          GP row = dynamically placed after all levels

        The distance from body_row(level) to code_row(level+1) is:
          code_row(level+1) - body_row(level) = (2 + 2*(level+1)) - (1 + 2*level)
          = 2 + 2*level + 2 - 1 - 2*level = 3

        That's 3 rows apart. Between body_row(0)=1 and code_row(1)=4:
          row 2 = code_row(0)
          row 3 = body_row(1)
          row 4 = code_row(1)

        Actually wait, body_row(1) = 1 + 2*1 = 3. code_row(1) = 2 + 2*1 = 4.
        So between body_row(0)=1 and code_row(1)=4, we have:
          row 2 = code_row(0) (has outer loop's ( P % )
          row 3 = body_row(1) (inner body, going W)

        Transition from body_row(0)=1 going W:
        The IP needs to drop from row 1 to row 4 (code_row(1)).
        That's 3 rows down. Each intervening row has opcodes (rows 2 and 3).

        This means we need a COLUMN that is clear on rows 2 and 3 so the IP
        can pass through going S. But row 2 has the outer loop's code!

        Solution: the transition column must be to the RIGHT of the outer
        loop's code. The outer loop's code occupies cols [col_L, col_R+1].
        The inner loop can be placed at columns starting from col_R+2 or
        wherever the code_col cursor is.

        But the body opcodes go W (right to left). The inner loop transition
        happens somewhere in the middle of the body execution. The IP is
        going W, and at the transition point it needs to turn S.

        Going W, hit a / mirror: / reflects W→S. IP goes S.
        IP drops through rows 2, 3, 4... hitting whatever is there.
        If rows 2 and 3 at this column are NOP (0), IP passes through.
        At row 4 = code_row(1), the inner loop code starts.
        But IP arrives going S at code_row(1). Need to redirect S→E.
        Use \\ at code_row(1): S→E. Now IP goes E on the inner code row.

        After the inner loop exits (continues E past the inner loop's ]):
        Need to go back up to body_row(0) going W.
        Use / at code_row(1): E→N. Wait, / reflects E→N. IP goes N.
        Goes through rows 3, 2, 1 (if those columns are NOP).
        At body_row(0)=1, use \\ to redirect N→W: \\ reflects N→W.
        IP continues W on the body row.

        But! Rows 2 and 3 at the transition columns might have opcodes.
        We need to ensure those columns are free.

        The inner loop is placed at a column range AFTER the outer loop.
        So on row 2 (outer code row) and row 3 (inner body row), the
        transition columns are beyond the outer loop's code — they are NOP.

        Wait, the inner loop itself is on row 3 (body) and row 4 (code).
        Row 3 will have the inner loop's body opcodes at various columns.
        The transition column for going UP needs to be free on row 3.

        This is getting complex. Let me just use transition columns that
        are dedicated and known to be free.

        SIMPLIFIED APPROACH:
        Rather than sharing columns, use two dedicated transition columns
        for each inner loop: one for descent (/ on body row, \\ on inner
        code row) and one for ascent (/ on inner code row, \\ on body row).

        The descent column (col_desc) and ascent column (col_asc) must be
        free on all intervening rows.

        For the inner loop body, it goes W between col_asc and col_desc
        on body_row(level+1). The inner code has ( P % on code_row(level+1)
        between col_desc and col_asc.

        Actually, for the inner code row going E, the ( is on the LEFT
        and % is on the RIGHT. The descent puts us at the left (col_desc),
        and the ascent takes us from the right (col_asc).

        Let me just implement this step by step.
        """
        # Save compiler state
        saved_h0 = self.h0
        saved_h1 = self.h1
        saved_cl = self.cl
        saved_grid = dict(self.grid)
        saved_code_col = self.code_col
        saved_max_col = self.max_col
        saved_max_level = self.max_level

        # Head state when body starts executing:
        # H0 = home, H1 = home, CL = (DATA_ROW, loop_var_col)
        self.h0 = (DATA_ROW, 0)
        self.h1 = (DATA_ROW, 0)
        self.cl = (DATA_ROW, loop_var_col)

        ops = []  # opcodes for the body row (execution order, going W)

        def body_emit(opcode_char):
            ops.append(opcode_char)

        for s in body_stmts:
            if isinstance(s, While):
                # ── Compile inner while loop ──
                # The inner loop will be placed at level+1, starting at
                # the current code_col. We need to:
                # 1. Emit ] to advance GP (in the body opcode stream)
                # 2. Record where the descent happens
                # 3. Compile the inner loop at level+1
                # 4. Record where the ascent happens
                # The body opcodes get placeholder markers that we'll
                # convert to mirror positions.

                # First, canonical reset before the inner loop
                self._canonical_reset(body_emit)

                # Emit ] in the body stream to advance GP for the inner loop
                body_emit(']')

                # Mark descent: this will become a / on the body row
                # (going W, / reflects W→S to drop to inner code row)
                desc_marker_idx = len(ops)
                body_emit('/')  # placeholder, will be placed on body row

                # Now compile the inner loop directly on level+1 rows.
                # The inner loop's code_col starts fresh at the descent column.
                # We need to figure out the column: it depends on where this
                # body opcode ends up on the grid.
                #
                # Problem: we don't know the column yet because the body
                # opcodes are laid out right-to-left after the entire body
                # is compiled.
                #
                # Solution: compile the inner loop AFTER we know the layout.
                # For now, collect the inner While AST and handle it in a
                # second pass.
                #
                # Actually, let me take a different approach. Instead of
                # virtual compilation, let me compile inner loops directly
                # onto the grid, tracking the column cursor.

                # REVISED APPROACH: Compile everything directly onto the grid.
                # Abandon the "virtual body" strategy for nested loops.
                # Instead, use a direct placement strategy.

                # For now, record a "WHILE" marker in the ops list.
                ops.append(('WHILE', s))

                # Mark ascent: \\ on body row to catch return from inner code
                body_emit('\\')

            else:
                self._compile_stmt_to(s, body_emit)

        # Canonical reset at end of body
        self._canonical_reset(body_emit)

        # Restore state
        self.h0 = saved_h0
        self.h1 = saved_h1
        self.cl = saved_cl
        self.grid = saved_grid
        self.code_col = saved_code_col
        self.max_col = saved_max_col
        self.max_level = saved_max_level

        return ops

    def _compile_stmt_to(self, stmt, emit_fn):
        """Compile a non-loop statement using the given emit function."""
        if isinstance(stmt, AddConst):
            col = self._var_col(stmt.var)
            self._move_h0_to(DATA_ROW, col, emit_fn)
            for _ in range(stmt.n):
                emit_fn('+')
        elif isinstance(stmt, SubConst):
            col = self._var_col(stmt.var)
            self._move_h0_to(DATA_ROW, col, emit_fn)
            for _ in range(stmt.n):
                emit_fn('-')
        elif isinstance(stmt, AddVar):
            tcol = self._var_col(stmt.target)
            scol = self._var_col(stmt.source)
            self._move_h0_to(DATA_ROW, tcol, emit_fn)
            self._move_h1_to(DATA_ROW, scol, emit_fn)
            emit_fn('.')
        elif isinstance(stmt, SubVar):
            tcol = self._var_col(stmt.target)
            scol = self._var_col(stmt.source)
            self._move_h0_to(DATA_ROW, tcol, emit_fn)
            self._move_h1_to(DATA_ROW, scol, emit_fn)
            emit_fn(',')
        elif isinstance(stmt, Swap):
            xcol = self._var_col(stmt.x)
            ycol = self._var_col(stmt.y)
            self._move_h0_to(DATA_ROW, xcol, emit_fn)
            self._move_h1_to(DATA_ROW, ycol, emit_fn)
            emit_fn('x')
        elif isinstance(stmt, While):
            raise RuntimeError("While in _compile_stmt_to — should use _compile_body_ops")
        else:
            raise TypeError(f"Unknown statement type: {type(stmt)}")

    # ── Output ────────────────────────────────────────────────────

    @staticmethod
    def write_fb2d(filename, rows, cols, grid_flat, header):
        """Write a .fb2d state file."""
        with open(filename, 'w') as f:
            f.write(f"# Compiled from ifb by ifbc-02.py v0.2\n")
            f.write(f"rows={header['rows']}\n")
            f.write(f"cols={header['cols']}\n")
            f.write(f"ip_row={header['ip_row']}\n")
            f.write(f"ip_col={header['ip_col']}\n")
            f.write(f"ip_dir={header['ip_dir']}\n")
            f.write(f"cl={header['cl']}\n")
            f.write(f"h0={header['h0']}\n")
            f.write(f"h1={header['h1']}\n")
            f.write(f"gp={header['gp']}\n")
            f.write(f"step={header['step']}\n")
            f.write(f"grid={','.join(str(v) for v in grid_flat)}\n")


# ── Direct placement compiler (v0.2 approach) ────────────────────

class CompilerV2:
    """Single-code-row compiler that handles nested loops.

    All code goes on a single CODE_ROW going E. Each while loop's
    ( P [body_ops] % ] is laid out sequentially. Nested while loops
    are simply additional ( P [body_ops] % ] segments inline.

    Loop-back corridors:
      Each nesting level L uses corridor_row(L) = 1 + L for the
      loop-back rectangle. The corridor has / at col_L and \\ at col_R.

      When % reflects E→N, IP rises to corridor_row where \\ reflects
      N→W. IP goes W to /, which reflects W→S. IP drops back to
      CODE_ROW where ( catches S→E (GP!=0 on re-entry).

      Deeper corridors are on higher-numbered rows (closer to CODE_ROW),
      so inner loop reflections hit their corridor before reaching outer
      corridors. Outer loop reflections pass through inner corridors at
      columns where inner corridors are NOP.

    Layout:
      Row 0: DATA (variables)
      Row 1: Corridor for nesting level 0
      Row 2: Corridor for nesting level 1
      ...
      Row max_depth: Corridor for deepest level
      Row max_depth+1: CODE_ROW (all code, going E)
      Row max_depth+2: GP trail
    """

    def __init__(self):
        self.variables = {}
        self.grid = {}
        self.data_values = {}

        self.h0 = (DATA_ROW, 0)
        self.h1 = (DATA_ROW, 0)
        self.cl = (DATA_ROW, 0)

        self.max_col = 0
        self.max_depth = 0  # max nesting depth of while loops
        self.code_r = 2     # will be set after max_depth is known
        self.gp_row = 3     # will be set after max_depth is known

    def corridor_row(self, level):
        """Row for loop-back corridor at nesting level L."""
        return 1 + level

    def compile(self, program):
        # Pass 1: collect vars and find max nesting depth
        for stmt in program.stmts:
            if isinstance(stmt, VarDecl):
                col = len(self.variables)
                self.variables[stmt.name] = col
                self.data_values[col] = stmt.value

        if not self.variables:
            self.variables['_dummy'] = 0
            self.data_values[0] = 0

        self.max_depth = self._max_depth(program.stmts)
        # CODE_ROW is after all corridor rows
        self.code_r = 1 + max(self.max_depth, 1)  # at least row 2
        self.gp_row = self.code_r + 1

        num_vars = len(self.variables)

        # Pass 2: compile all statements onto CODE_ROW
        cursor = 0
        cursor = self._compile_stmts(program.stmts, level=0, cursor=cursor)

        # Determine grid size
        rows = max(self.gp_row + 1, 4)
        cols = max(self.max_col + 2, num_vars + 1, 8)

        grid_flat = [0] * (rows * cols)

        for col_idx, value in self.data_values.items():
            grid_flat[DATA_ROW * cols + col_idx] = value

        for (r, c), val in self.grid.items():
            if 0 <= r < rows and 0 <= c < cols:
                grid_flat[r * cols + c] = val

        gp_flat = self.gp_row * cols + 0
        header = {
            'rows': rows,
            'cols': cols,
            'ip_row': self.code_r,
            'ip_col': 0,
            'ip_dir': 1,  # East
            'cl': DATA_ROW * cols + 0,
            'h0': DATA_ROW * cols + 0,
            'h1': DATA_ROW * cols + 0,
            'gp': gp_flat,
            'step': 0,
        }

        return rows, cols, grid_flat, header

    def _max_depth(self, stmts):
        """Find the maximum nesting depth of while loops."""
        d = 0
        for s in stmts:
            if isinstance(s, While):
                d = max(d, 1 + self._max_depth(s.body))
        return d

    def _emit(self, row, col, opcode_char):
        self.grid[(row, col)] = OP[opcode_char]
        self.max_col = max(self.max_col, col)

    def _var_col(self, name):
        if name not in self.variables:
            raise NameError(f"Undefined variable '{name}'")
        return self.variables[name]

    # ── Head movement ─────────────────────────────────────────────

    def _move_h0_to(self, target_row, target_col, emit_fn):
        cr, cc = self.h0
        while cr < target_row:
            emit_fn('S'); cr += 1
        while cr > target_row:
            emit_fn('N'); cr -= 1
        while cc < target_col:
            emit_fn('E'); cc += 1
        while cc > target_col:
            emit_fn('W'); cc -= 1
        self.h0 = (target_row, target_col)

    def _move_h1_to(self, target_row, target_col, emit_fn):
        cr, cc = self.h1
        while cr < target_row:
            emit_fn('s'); cr += 1
        while cr > target_row:
            emit_fn('n'); cr -= 1
        while cc < target_col:
            emit_fn('e'); cc += 1
        while cc > target_col:
            emit_fn('w'); cc -= 1
        self.h1 = (target_row, target_col)

    def _move_cl_to(self, target_row, target_col, emit_fn):
        cr, cc = self.cl
        while cr < target_row:
            emit_fn('v'); cr += 1
        while cr > target_row:
            emit_fn('^'); cr -= 1
        while cc < target_col:
            emit_fn('>'); cc += 1
        while cc > target_col:
            emit_fn('<'); cc -= 1
        self.cl = (target_row, target_col)

    def _canonical_reset(self, emit_fn):
        home = (DATA_ROW, 0)
        self._move_h0_to(*home, emit_fn)
        self._move_h1_to(*home, emit_fn)
        self._move_cl_to(*home, emit_fn)

    @staticmethod
    def _head_move_ops(src, dst, east, west, south, north):
        """Compute opcode chars to move a head from src to dst.
        Returns list of opcode chars (e.g. ['E','E','S']).
        src/dst are (row, col) tuples."""
        ops = []
        sr, sc = src
        dr, dc = dst
        while sr < dr:
            ops.append(south); sr += 1
        while sr > dr:
            ops.append(north); sr -= 1
        while sc < dc:
            ops.append(east); sc += 1
        while sc > dc:
            ops.append(west); sc -= 1
        return ops

    # ── Compilation ───────────────────────────────────────────────

    def _compile_stmts(self, stmts, level, cursor):
        """Compile a list of statements, returning new cursor."""
        for s in stmts:
            if isinstance(s, VarDecl):
                continue
            cursor = self._compile_one(s, level, cursor)
        return cursor

    def _compile_one(self, stmt, level, cursor):
        """Compile one statement onto CODE_ROW. Returns new cursor."""
        code_r = self.code_r

        def emit_here(opcode_char):
            nonlocal cursor
            self._emit(code_r, cursor, opcode_char)
            cursor += 1

        if isinstance(stmt, AddConst):
            col = self._var_col(stmt.var)
            self._move_h0_to(DATA_ROW, col, emit_here)
            for _ in range(stmt.n):
                emit_here('+')
        elif isinstance(stmt, SubConst):
            col = self._var_col(stmt.var)
            self._move_h0_to(DATA_ROW, col, emit_here)
            for _ in range(stmt.n):
                emit_here('-')
        elif isinstance(stmt, AddVar):
            tcol = self._var_col(stmt.target)
            scol = self._var_col(stmt.source)
            self._move_h0_to(DATA_ROW, tcol, emit_here)
            self._move_h1_to(DATA_ROW, scol, emit_here)
            emit_here('.')
        elif isinstance(stmt, SubVar):
            tcol = self._var_col(stmt.target)
            scol = self._var_col(stmt.source)
            self._move_h0_to(DATA_ROW, tcol, emit_here)
            self._move_h1_to(DATA_ROW, scol, emit_here)
            emit_here(',')
        elif isinstance(stmt, Swap):
            xcol = self._var_col(stmt.x)
            ycol = self._var_col(stmt.y)
            self._move_h0_to(DATA_ROW, xcol, emit_here)
            self._move_h1_to(DATA_ROW, ycol, emit_here)
            emit_here('x')
        elif isinstance(stmt, ZeroVar):
            col = self._var_col(stmt.var)
            self._move_h0_to(DATA_ROW, col, emit_here)
            emit_here(']')   # advance GP to fresh cell (value 0)
            emit_here('Z')   # swap(grid[H0], grid[GP]) — zeros the var
        elif isinstance(stmt, While):
            cursor = self._compile_while(stmt, level, cursor)
        else:
            raise TypeError(f"Unknown statement type: {type(stmt)}")

        return cursor

    def _compile_while(self, stmt, level, cursor):
        """Compile a while loop with no canonical resets.

        The compiler tracks all head positions at all times and emits
        only the minimal delta movements needed. The corridor handles
        resetting H0/H1 from end-of-body positions back to entry
        positions for the next iteration.

        Code layout on CODE_ROW going E:
          {CL to var_col} ( P {body_ops} {CL to var_col} % ]

        Corridor on corridor_row(level):
          / at col_L, \\ at col_R.
          Between them (going W): H0 reset ops, H1 reset ops.
          CL is already at var_col on both sides, so zero CL corridor ops.

        Flow:
          First entry: IP going E hits (. GP points to 0, passes through.
            P increments. Body executes. CL moves to var_col. % checks.
            If CL!=0, reflects E→N. Rises to corridor \\ at col_R: N→W.
            H0/H1 reset ops execute going W. Hits / at col_L: W→S.
            Drops to CODE_ROW, hits (: GP!=0, reflects S→E. Re-enters.
          Exit: %: CL=0, passes E. ] advances GP. Continue E.
        """
        var_col = self._var_col(stmt.var)
        code_r = self.code_r
        corr_r = self.corridor_row(level)

        def emit_here(opcode_char):
            nonlocal cursor
            self._emit(code_r, cursor, opcode_char)
            cursor += 1

        # ── Move CL to var_col (from wherever it is) ──
        self._move_cl_to(DATA_ROW, var_col, emit_here)

        # ── Snapshot entry positions ──
        # On re-entry from corridor, heads will be restored to these.
        h0_entry = self.h0
        h1_entry = self.h1
        # CL entry is (DATA_ROW, var_col), guaranteed above

        col_L = cursor  # ( goes here

        # ── ( P ──
        emit_here('(')
        emit_here('P')

        # ── Body: compile all body stmts inline ──
        # Heads start from entry positions. They drift as body executes.
        for i, s in enumerate(stmt.body):
            if isinstance(s, While):
                # Advance GP to a fresh zero cell so the inner ( sees
                # grid[GP]=0 and passes through on first entry.
                emit_here(']')
                # Compile inner while — it takes heads from wherever they
                # are. No canonical reset needed.
                cursor = self._compile_while(s, level + 1, cursor)
                # After inner loop exit: GP still points to the inner
                # loop's breadcrumb cell (non-zero), because the inner
                # loop does NOT emit ] after %. This is exactly what the
                # outer ( needs on re-entry (grid[GP]!=0 → reflect S→E).
                # No bridge P needed.
            else:
                cursor = self._compile_one(s, level, cursor)

        # ── Move CL to var_col for % check ──
        self._move_cl_to(DATA_ROW, var_col, emit_here)

        # ── Snapshot end-of-body positions ──
        h0_end = self.h0
        h1_end = self.h1

        col_R = cursor  # % goes here

        # ── % (no ] here — see below) ──
        # The ] after % is NOT emitted here. Instead, the CALLER is
        # responsible for advancing GP after this loop exits:
        # - For inner loops: the outer loop's next ] (before the next
        #   inner while) or the outer %+] handles it.
        # - For the outermost loop: a ] is emitted after % below.
        # This saves one zero cell per iteration vs the old ] P bridge.
        mirror = '?' if stmt.negated else '%'
        emit_here(mirror)

        # ── Corridor: / at col_L, \\ at col_R ──
        self._emit(corr_r, col_L, '/')
        self._emit(corr_r, col_R, '\\')

        # ── Corridor head reset opcodes ──
        # IP traverses corridor going W (from \\ at col_R toward / at col_L).
        # We need to reset H0 and H1 from end-of-body positions back to
        # entry positions. CL is already at var_col on both sides.
        h0_ops = self._head_move_ops(h0_end, h0_entry, 'E', 'W', 'S', 'N')
        h1_ops = self._head_move_ops(h1_end, h1_entry, 'e', 'w', 's', 'n')
        corridor_ops = h0_ops + h1_ops

        # Place corridor ops going W from col_R-1
        for i, op_char in enumerate(corridor_ops):
            self._emit(corr_r, col_R - 1 - i, op_char)

        # Verify there's enough space on the corridor
        if corridor_ops:
            leftmost_op = col_R - len(corridor_ops)
            if leftmost_op <= col_L:
                raise RuntimeError(
                    f"Corridor too narrow: need {len(corridor_ops)} ops "
                    f"between col_L={col_L} and col_R={col_R}, but only "
                    f"{col_R - col_L - 1} slots available."
                )

        # ── Post-exit state ──
        # After % passes through (loop exit), heads are at end-of-body
        # positions. CL is at (DATA_ROW, var_col). No reset — the next
        # code picks up from wherever heads are.
        # (self.h0, self.h1 are already at h0_end, h1_end from body compilation)
        # (self.cl is at (DATA_ROW, var_col) from the CL move before %)

        return cursor

    # ── Output ────────────────────────────────────────────────────

    @staticmethod
    def write_fb2d(filename, rows, cols, grid_flat, header):
        with open(filename, 'w') as f:
            f.write(f"# Compiled from ifb by ifbc-02.py v0.2\n")
            f.write(f"rows={header['rows']}\n")
            f.write(f"cols={header['cols']}\n")
            f.write(f"ip_row={header['ip_row']}\n")
            f.write(f"ip_col={header['ip_col']}\n")
            f.write(f"ip_dir={header['ip_dir']}\n")
            f.write(f"cl={header['cl']}\n")
            f.write(f"h0={header['h0']}\n")
            f.write(f"h1={header['h1']}\n")
            f.write(f"gp={header['gp']}\n")
            f.write(f"step={header['step']}\n")
            f.write(f"grid={','.join(str(v) for v in grid_flat)}\n")


# ── Debug display ─────────────────────────────────────────────────

def display_grid(rows, cols, grid_flat):
    print(f"\nCompiled grid ({rows}x{cols}):")
    hdr = "     "
    for c in range(cols):
        hdr += f"{c:>4}"
    print(hdr)
    print("     " + "----" * cols)
    for r in range(rows):
        line = f" r{r}: "
        for c in range(cols):
            v = grid_flat[r * cols + c]
            if v in OPCODE_TO_CHAR:
                ch = OPCODE_TO_CHAR[v]
                line += f"   {ch}"
            elif v == 0:
                line += "   ."
            else:
                line += f" {v:>3}"
        print(line)
    print()


# ── Test programs ─────────────────────────────────────────────────

TEST_PROGRAMS = {
    'add': {
        'source': """\
var x = 3
var y = 5
x += y
""",
        'expected': {'x': 8, 'y': 5},
    },
    'sub': {
        'source': """\
var x = 10
var y = 3
x -= y
""",
        'expected': {'x': 7, 'y': 3},
    },
    'inc': {
        'source': """\
var x = 0
x += 5
""",
        'expected': {'x': 5},
    },
    'swap': {
        'source': """\
var a = 10
var b = 20
swap a b
""",
        'expected': {'a': 20, 'b': 10},
    },
    'sum': {
        'source': """\
var n = 5
var result = 0
while n do
    result += n
    n -= 1
end
""",
        'expected': {'n': 0, 'result': 15},
    },
    'multiply': {
        'source': """\
var a = 3
var b = 4
var result = 0
var count = 0
count += b
while count do
    result += a
    count -= 1
end
""",
        'expected': {'a': 3, 'b': 4, 'result': 12, 'count': 0},
    },
    # ── New: nested loop tests ──
    'nested_simple': {
        'source': """\
var outer = 3
var inner_count = 0
var result = 0
while outer do
    inner_count += outer
    while inner_count do
        result += 1
        inner_count -= 1
    end
    outer -= 1
end
// outer loops: 3, 2, 1. inner counts: 3, 2, 1. result = 3+2+1 = 6
""",
        'expected': {'outer': 0, 'inner_count': 0, 'result': 6},
    },
    'multiply_nested': {
        'source': """\
var a = 3
var b = 4
var result = 0
var count = 0
while b do
    count += a
    while count do
        result += 1
        count -= 1
    end
    b -= 1
end
// 4 iterations of outer, each adds a=3 to result via inner. result=12
""",
        'expected': {'a': 3, 'b': 0, 'result': 12, 'count': 0},
    },
    'factorial': {
        'source': """\
var n = 5
var result = 1
var acc = 0
var count = 0
while n do
    // Multiply: acc = result * n
    count += n
    while count do
        acc += result
        count -= 1
    end
    // Move acc to result, zero acc
    swap acc result
    zero acc
    n -= 1
end
// 5! = 120
""",
        'expected': {'n': 0, 'result': 120, 'acc': 0, 'count': 0},
    },
    'zero_basic': {
        'source': """\
var x = 42
var y = 0
zero x
// x should be 0 after zeroing
""",
        'expected': {'x': 0, 'y': 0},
    },
}


def run_test(name, verbose=False):
    """Compile and run a test program."""
    test = TEST_PROGRAMS[name]
    source = test['source']
    expected = test['expected']

    print(f"=== Test: {name} ===")

    ast = parse(source)
    if verbose:
        print("AST:")
        for s in ast.stmts:
            print(f"  {s}")

    compiler = CompilerV2()
    rows, cols, grid_flat, header = compiler.compile(ast)

    if verbose:
        display_grid(rows, cols, grid_flat)
        print(f"Header: {header}")

    # Run using fb2d simulator
    sys.path.insert(0, '/Volumes/briefcase/compnet/python-scripts/toy-agent')
    try:
        import importlib.util
        _spec = importlib.util.spec_from_file_location(
            'fb2d_10',
            '/Volumes/briefcase/compnet/python-scripts/toy-agent/fb2d-10.py')
        _mod = importlib.util.module_from_spec(_spec)
        _spec.loader.exec_module(_mod)
        FB2DSimulator = _mod.FB2DSimulator
    except Exception:
        from fb2d import FB2DSimulator

    sim = FB2DSimulator(rows=rows, cols=cols)
    sim.use_color = False
    sim.grid = list(grid_flat)
    sim.ip_row = header['ip_row']
    sim.ip_col = header['ip_col']
    sim.ip_dir = header['ip_dir']
    sim.cl = header['cl']
    sim.h0 = header['h0']
    sim.h1 = header['h1']
    sim.gp = header['gp']
    sim.step_count = 0

    # Find rightmost code
    max_code_col = 0
    for (r, c) in compiler.grid:
        max_code_col = max(max_code_col, c)

    max_steps = 500000
    for i in range(max_steps):
        if sim.ip_row == compiler.code_r and sim.ip_dir == 1 and sim.ip_col > max_code_col:
            break
        sim.step()

    forward_steps = sim.step_count

    # Rebuild variable map
    var_map = {}
    for s in ast.stmts:
        if isinstance(s, VarDecl):
            var_map[s.name] = len(var_map)

    results = {}
    for vname, vcol in var_map.items():
        results[vname] = sim.grid[DATA_ROW * cols + vcol]

    ok = True
    for vname, exp_val in expected.items():
        got = results.get(vname, '???')
        status = "ok" if got == exp_val else "FAIL"
        if got != exp_val:
            ok = False
        print(f"  {vname}: expected={exp_val}, got={got} {status}")

    print(f"  Forward steps: {forward_steps}")

    # Reverse test
    for i in range(forward_steps):
        sim.step_back()

    reverse_ok = True
    for s in ast.stmts:
        if isinstance(s, VarDecl):
            vcol = var_map[s.name]
            got = sim.grid[DATA_ROW * cols + vcol]
            if got != s.value:
                reverse_ok = False
                print(f"  REVERSE FAIL: {s.name}: expected={s.value}, got={got}")

    gp_start = compiler.gp_row * cols
    gp_clean = all(sim.grid[gp_start + c] == 0 for c in range(cols))

    if reverse_ok and gp_clean:
        print(f"  Reverse: ok (all restored, GP clean)")
    elif reverse_ok:
        print(f"  Reverse: ok (vars restored, GP not fully clean)")
    else:
        print(f"  Reverse: FAIL")

    overall = "PASS" if (ok and reverse_ok) else "FAIL"
    print(f"  Result: {overall}")
    print()
    return ok and reverse_ok


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage: ifbc-02.py <source.ifb> [output.fb2d]")
        print("       ifbc-02.py --test [test_name]")
        print("       ifbc-02.py --test-all")
        print(f"\nAvailable tests: {', '.join(TEST_PROGRAMS.keys())}")
        sys.exit(1)

    if sys.argv[1] == '--test-all':
        all_ok = True
        for name in TEST_PROGRAMS:
            if not run_test(name, verbose=('-v' in sys.argv)):
                all_ok = False
        print("=== All tests passed! ===" if all_ok else "=== SOME TESTS FAILED ===")
        sys.exit(0 if all_ok else 1)

    if sys.argv[1] == '--test':
        name = sys.argv[2] if len(sys.argv) > 2 else 'sum'
        verbose = '-v' in sys.argv
        ok = run_test(name, verbose=verbose)
        sys.exit(0 if ok else 1)

    source_file = sys.argv[1]
    output_file = sys.argv[2] if len(sys.argv) > 2 else source_file.replace('.ifb', '.fb2d')
    if output_file == source_file:
        output_file += '.fb2d'

    with open(source_file, 'r') as f:
        source = f.read()

    ast = parse(source)
    compiler = CompilerV2()
    rows, cols, grid_flat, header = compiler.compile(ast)
    display_grid(rows, cols, grid_flat)
    CompilerV2.write_fb2d(output_file, rows, cols, grid_flat, header)
    print(f"Written to {output_file}")
