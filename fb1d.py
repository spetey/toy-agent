#!/usr/bin/env python3
"""
F***brain 1D Tape Simulator — fb1d
Authored or modified by Claude
Version: 2026-04-15 v0.1

A 1D reversible programming model where code and data share a single tape.
Control flow uses conditional jumps with an EX-based run-length trail
for unambiguous reversal.

Execution model:
  1. Read instruction at tape[IP]
  2. Execute it (jumps may change IP)
  3. IP += 1 (always, including after jumps — the jump already set IP)

  Every step also updates the EX trail:
    Non-jump: tape[EX]++  (extend run-length count)
    Jump:     EX++; tape[EX] = IP_of_jump; EX++; tape[EX] = 1  (new run)

State:
  tape[]  — infinite-in-principle 1D array (code + data + fuel)
  IP      — instruction pointer (index into tape)
  H0      — data head 0 (index into tape)
  H1      — data head 1 (index into tape)
  CL      — control locus (index into tape)
  EX      — exteroceptor / fuel head (index into tape)

Reversibility:
  step_back() checks the EX trail:
    tape[EX] > 1  →  non-jump: Q, predecessor at IP-1, undo data op
    tape[EX] == 1 →  jump: restore fuel, read return addr, unjump

Fuel cost: 2 cells per jump, 0 per non-jump step.
"""

import sys
import os
import readline  # for line editing in REPL

# ─── Opcode Definitions ──────────────────────────────────────────────

# Simple 8-bit encoding for now (no Hamming — that's a future addition).
# Every byte 0–255 is valid: unrecognized values are NOP.

OPCODES = {
    # Data ops (H0-targeted)
    '+':  1,   # tape[H0]++
    '-':  2,   # tape[H0]--
    '.':  3,   # tape[H0] += tape[H1]
    ',':  4,   # tape[H0] -= tape[H1]
    'X':  5,   # swap(tape[H0], tape[H1])
    'x':  6,   # tape[H0] ^= tape[H1]  (XOR)
    # Head movement
    '>':  7,   # H0 move right (H0++)
    '<':  8,   # H0 move left  (H0--)
    ')':  9,   # H1 move right (H1++)
    '(':  10,  # H1 move left  (H1--)
    '}':  11,  # CL move right (CL++)
    '{':  12,  # CL move left  (CL--)
    ']':  13,  # EX move right (EX++)
    '[':  14,  # EX move left  (EX--)
    # CL data ops
    ':':  15,  # tape[CL]++
    ';':  16,  # tape[CL]--
    # EX data ops
    'P':  17,  # tape[EX]++  (leave breadcrumb)
    'Q':  18,  # tape[EX]--  (erase breadcrumb)
    'Z':  19,  # swap(tape[H0], tape[EX])
    'T':  20,  # swap(tape[CL], tape[H0])  (bridge)
    # Jump
    'J':  21,  # if tape[CL] != 0: jump to tape[H1] (with EX trail)
}

OPCODE_TO_CHAR = {v: k for k, v in OPCODES.items()}
OPCODE_TO_CHAR[0] = '·'   # NOP displayed as middle dot

CELL_BITS = 8
CELL_MOD = 1 << CELL_BITS   # 256
CELL_MASK = CELL_MOD - 1     # 0xFF

# ─── Simulator ────────────────────────────────────────────────────────

DEFAULT_SIZE = 64

class FB1D:
    def __init__(self, size=DEFAULT_SIZE):
        self.size = size
        self.tape = [0] * size
        self.ip = 0
        self.h0 = 0
        self.h1 = 0
        self.cl = 0
        self.ex = 0
        self.step_count = 0
        self.history = []   # for undo beyond EX trail (optional)

    def _wrap(self, idx):
        """Wrap index to tape bounds (toroidal)."""
        return idx % self.size

    def _val(self, idx):
        """Read tape value at wrapped index."""
        return self.tape[self._wrap(idx)]

    def _set(self, idx, val):
        """Set tape value at wrapped index."""
        self.tape[self._wrap(idx)] = val & CELL_MASK

    def _add(self, idx, delta):
        """Add delta to tape value at wrapped index."""
        w = self._wrap(idx)
        self.tape[w] = (self.tape[w] + delta) & CELL_MASK

    def _deposit_jump(self, return_addr):
        """Record a jump in the EX trail. Consumes 2 fuel cells.

        Before: tape[EX] = current run count (≥ 1)
        After:  EX advanced by 2; tape[EX-1] = return_addr;
                tape[EX] = 1 (new run count)
        """
        # Advance past current count
        self.ex = self._wrap(self.ex + 1)
        # Deposit return address (cell was 0 = fuel)
        self._set(self.ex, return_addr)
        # Advance past return address
        self.ex = self._wrap(self.ex + 1)
        # Start new run count = 1
        self._add(self.ex, 1)   # 0 → 1

    def _undo_jump_deposit(self):
        """Reverse a jump deposit. Restores 2 fuel cells.

        Returns the return address.
        Before: tape[EX] == 1 (run count at jump boundary)
        After:  EX retreated by 2; both fuel cells restored to 0
        """
        # Undo the new run count: 1 → 0
        self._add(self.ex, -1)
        # Retreat past return address
        self.ex = self._wrap(self.ex - 1)
        # Read and clear return address
        ret = self._val(self.ex)
        self._set(self.ex, 0)
        # Retreat to previous count
        self.ex = self._wrap(self.ex - 1)
        return ret

    def step(self):
        """Execute one forward step."""
        op = self._val(self.ip)
        opcode = OPCODE_TO_CHAR.get(op)
        jumped = False
        ip_at = self.ip  # save for jump deposit

        # ── Execute ──
        if op == 0 or opcode is None:
            pass  # NOP

        elif opcode == '+':
            self._add(self.h0, 1)
        elif opcode == '-':
            self._add(self.h0, -1)
        elif opcode == '.':
            if self.h0 != self.h1:  # NOP guard
                self._add(self.h0, self._val(self.h1))
        elif opcode == ',':
            if self.h0 != self.h1:  # NOP guard
                self._add(self.h0, -(self._val(self.h1)))
        elif opcode == 'X':
            if self.h0 != self.h1:
                a, b = self._val(self.h0), self._val(self.h1)
                self._set(self.h0, b)
                self._set(self.h1, a)
        elif opcode == 'x':
            if self.h0 != self.h1:  # NOP guard
                self._add(self.h0, 0)  # no-op placeholder
                # XOR: tape[H0] ^= tape[H1]
                v = self._val(self.h0) ^ self._val(self.h1)
                self._set(self.h0, v)

        # Head movement
        elif opcode == '>':
            self.h0 = self._wrap(self.h0 + 1)
        elif opcode == '<':
            self.h0 = self._wrap(self.h0 - 1)
        elif opcode == ')':
            self.h1 = self._wrap(self.h1 + 1)
        elif opcode == '(':
            self.h1 = self._wrap(self.h1 - 1)
        elif opcode == '}':
            self.cl = self._wrap(self.cl + 1)
        elif opcode == '{':
            self.cl = self._wrap(self.cl - 1)
        elif opcode == ']':
            self.ex = self._wrap(self.ex + 1)
        elif opcode == '[':
            self.ex = self._wrap(self.ex - 1)

        # CL data
        elif opcode == ':':
            self._add(self.cl, 1)
        elif opcode == ';':
            self._add(self.cl, -1)

        # EX data
        elif opcode == 'P':
            self._add(self.ex, 1)
        elif opcode == 'Q':
            self._add(self.ex, -1)
        elif opcode == 'Z':
            a, b = self._val(self.h0), self._val(self.ex)
            self._set(self.h0, b)
            self._set(self.ex, a)
        elif opcode == 'T':
            a, b = self._val(self.cl), self._val(self.h0)
            self._set(self.cl, b)
            self._set(self.h0, a)

        # Jump
        elif opcode == 'J':
            if self._val(self.cl) != 0:
                target = self._val(self.h1)
                self._deposit_jump(ip_at)
                self.ip = self._wrap(target)
                jumped = True

        # ── EX trail for non-jump steps ──
        if not jumped:
            self._add(self.ex, 1)  # P: extend run count
            self.ip = self._wrap(self.ip + 1)

        self.step_count += 1

    def step_back(self):
        """Execute one reverse step using the EX trail."""
        if self.step_count <= 0:
            print("  (at step 0, cannot go back)")
            return

        ex_val = self._val(self.ex)

        if ex_val > 1:
            # ── Non-jump: predecessor at IP-1 ──
            self._add(self.ex, -1)   # Q: undo the P
            self.ip = self._wrap(self.ip - 1)
            # Read instruction at predecessor and undo
            self._undo_instruction(self.ip)

        elif ex_val == 1:
            # ── Jump boundary ──
            ret = self._undo_jump_deposit()
            # ret = IP of the J instruction that jumped here
            # Undo the jump: restore IP to the J's position
            # The J instruction itself doesn't modify data (only IP + EX)
            # but we still need to "undo" in the sense of restoring IP
            self.ip = self._wrap(ret)
            # Note: J's data effect was just the EX deposit, already undone.
            # The jump set IP = tape[H1]; undoing sets IP = ret (position of J).
            # We do NOT IP++, because the forward step's IP++ was
            # replaced by the jump.

        elif ex_val == 0:
            # At the initial sentinel — can't go further back
            print("  (EX trail exhausted, cannot go back)")
            return
        else:
            print(f"  (EX trail corrupt: tape[EX]={ex_val})")
            return

        self.step_count -= 1

    def _undo_instruction(self, pos):
        """Undo the data effects of the instruction at tape[pos].

        Called during step_back for non-jump steps. The instruction
        was already executed forward; we reverse its effect.
        """
        op = self._val(pos)
        opcode = OPCODE_TO_CHAR.get(op)

        if op == 0 or opcode is None:
            pass  # NOP

        elif opcode == '+':
            self._add(self.h0, -1)   # undo: decrement
        elif opcode == '-':
            self._add(self.h0, 1)    # undo: increment
        elif opcode == '.':
            if self.h0 != self.h1:
                self._add(self.h0, -(self._val(self.h1)))  # undo: subtract
        elif opcode == ',':
            if self.h0 != self.h1:
                self._add(self.h0, self._val(self.h1))     # undo: add
        elif opcode == 'X':
            if self.h0 != self.h1:   # swap is self-inverse
                a, b = self._val(self.h0), self._val(self.h1)
                self._set(self.h0, b)
                self._set(self.h1, a)
        elif opcode == 'x':
            if self.h0 != self.h1:   # XOR is self-inverse
                v = self._val(self.h0) ^ self._val(self.h1)
                self._set(self.h0, v)

        # Head movement: reverse direction
        elif opcode == '>':
            self.h0 = self._wrap(self.h0 - 1)
        elif opcode == '<':
            self.h0 = self._wrap(self.h0 + 1)
        elif opcode == ')':
            self.h1 = self._wrap(self.h1 - 1)
        elif opcode == '(':
            self.h1 = self._wrap(self.h1 + 1)
        elif opcode == '}':
            self.cl = self._wrap(self.cl - 1)
        elif opcode == '{':
            self.cl = self._wrap(self.cl + 1)
        elif opcode == ']':
            self.ex = self._wrap(self.ex - 1)
        elif opcode == '[':
            self.ex = self._wrap(self.ex + 1)

        # CL data: reverse
        elif opcode == ':':
            self._add(self.cl, -1)
        elif opcode == ';':
            self._add(self.cl, 1)

        # EX data: reverse
        elif opcode == 'P':
            self._add(self.ex, -1)
        elif opcode == 'Q':
            self._add(self.ex, 1)
        elif opcode == 'Z':   # swap is self-inverse
            a, b = self._val(self.h0), self._val(self.ex)
            self._set(self.h0, b)
            self._set(self.ex, a)
        elif opcode == 'T':   # swap is self-inverse
            a, b = self._val(self.cl), self._val(self.h0)
            self._set(self.cl, b)
            self._set(self.h0, a)

        elif opcode == 'J':
            # J that didn't fire (condition was false) — no data effect
            pass

    # ── Display ───────────────────────────────────────────────────────

    def display(self, window=None):
        """Display tape around IP with head markers.

        window: (start, end) range to display. Default: auto.
        """
        if window is None:
            # Show a window centered on IP, covering all heads
            heads = [self.ip, self.h0, self.h1, self.cl, self.ex]
            lo = max(0, min(heads) - 2)
            hi = min(self.size, max(heads) + 3)
            # Ensure at least 16 cells shown
            if hi - lo < 16:
                mid = (lo + hi) // 2
                lo = max(0, mid - 8)
                hi = min(self.size, lo + 16)
            window = (lo, hi)

        lo, hi = window

        # Position line
        pos_line = "Pos: "
        val_line = "Val: "
        opc_line = "Opc: "
        hdr_line = "     "

        for i in range(lo, hi):
            val = self.tape[i]
            ch = OPCODE_TO_CHAR.get(val, '·')
            pos_line += f"{i:>4} "
            val_line += f"{val:>4} "
            opc_line += f"   {ch} "

            # Head markers
            markers = []
            if i == self.ip:
                markers.append('IP')
            if i == self.h0:
                markers.append('H0')
            if i == self.h1:
                markers.append('H1')
            if i == self.cl:
                markers.append('CL')
            if i == self.ex:
                markers.append('EX')
            if markers:
                hdr_line += f"{''.join(markers):>4} "
            else:
                hdr_line += "     "

        print(pos_line)
        print(val_line)
        print(opc_line)
        print(hdr_line)

    def display_ex_trail(self):
        """Display the EX trail interpretation."""
        # Find the start of the trail (scan left from EX for nonzero cells)
        # Simple approach: find the leftmost nonzero cell near EX
        # For now, just show raw values around EX
        print(f"  EX trail (EX={self.ex}, tape[EX]={self._val(self.ex)}):")

        # Find trail start: scan left from EX until we find a 0
        # (fuel cells are 0; trail cells are nonzero)
        start = self.ex
        while start > 0 and self._val(start - 1) != 0:
            start -= 1

        if start == self.ex:
            print(f"    [{self._val(self.ex)}]  (just the count)")
            return

        parts = []
        i = start
        while i <= self.ex:
            v = self._val(i)
            if i == self.ex:
                parts.append(f"[{v}]")  # current count (bracketed)
            else:
                parts.append(str(v))
            i += 1
        print(f"    tape[{start}..{self.ex}] = {', '.join(parts)}")

    # ── Save / Load ──────────────────────────────────────────────────

    def save_state(self, filename):
        """Save state to file."""
        with open(filename, 'w') as f:
            f.write(f"# fb1d state\n")
            f.write(f"size={self.size}\n")
            f.write(f"ip={self.ip}\nh0={self.h0}\nh1={self.h1}\n")
            f.write(f"cl={self.cl}\nex={self.ex}\n")
            f.write(f"step={self.step_count}\n")
            f.write(f"tape={','.join(str(v) for v in self.tape)}\n")

    def load_state(self, filename):
        """Load state from file."""
        data = {}
        with open(filename, 'r') as f:
            for line in f:
                line = line.strip()
                if '=' in line and not line.startswith('#'):
                    k, v = line.split('=', 1)
                    data[k] = v

        self.size = int(data.get('size', DEFAULT_SIZE))
        self.ip = int(data.get('ip', 0))
        self.h0 = int(data.get('h0', 0))
        self.h1 = int(data.get('h1', 0))
        self.cl = int(data.get('cl', 0))
        self.ex = int(data.get('ex', 0))
        self.step_count = int(data.get('step', 0))

        if 'tape' in data:
            vals = [int(x) for x in data['tape'].split(',')]
            self.tape = vals[:self.size]
            if len(self.tape) < self.size:
                self.tape.extend([0] * (self.size - len(self.tape)))
        else:
            self.tape = [0] * self.size

    # ── Built-in examples ────────────────────────────────────────────

    def load_example(self, name):
        """Load a named example."""
        if name == 'dec_loop':
            # Decrement tape[H0] from 3 to 0 using J loop.
            # Code at positions 1-2: DEC, J
            # H0 → position 20 (counter = 3)
            # H1 → position 21 (jump target = 1)
            # CL → position 20 (same as H0, so J tests the counter)
            # EX → position 30 (fuel area, initialized with sentinel 1)
            self.size = 64
            self.tape = [0] * self.size
            # Code
            self.tape[1] = OPCODES['-']   # DEC
            self.tape[2] = OPCODES['J']   # conditional jump
            # Data
            self.tape[20] = 3             # counter
            self.tape[21] = 1             # jump target (position 1)
            # EX sentinel
            self.tape[30] = 1             # run-length count starts at 1
            # Registers
            self.ip = 1
            self.h0 = 20    # points to counter
            self.h1 = 21    # points to jump target
            self.cl = 20    # J tests tape[CL] = counter
            self.ex = 30    # fuel area
            self.step_count = 0
            print("Loaded 'dec_loop': decrement from 3 to 0")
            print("  Code:  tape[1]=DEC, tape[2]=J")
            print("  Data:  tape[20]=3 (counter), tape[21]=1 (jump target)")
            print("  Heads: IP=1 H0=20 H1=21 CL=20 EX=30")
            print("  EX trail: tape[30]=1 (sentinel)")
            return True

        print(f"Unknown example: {name}")
        print("Available: dec_loop")
        return False


# ═══════════════════════════════════════════════════════════════════════
#  REPL
# ═══════════════════════════════════════════════════════════════════════

HELP_TEXT = """
═══════════════════════════════════════════════════════════════════════
 fb1d — 1D Reversible Tape Simulator
═══════════════════════════════════════════════════════════════════════

REPL commands:
  s [N]         Step forward N times (default 1)
  b [N]         Step backward N times (default 1)
  d [lo hi]     Display tape (default: auto window around heads)
  ex            Show EX trail interpretation
  set POS VAL   Set tape[POS] = VAL
  seto POS OP   Set tape[POS] = opcode for OP (e.g. 'seto 1 -')
  head H POS    Set head H (ip/h0/h1/cl/ex) to POS
  load FILE     Load state from .fb1d file
  save FILE     Save state to .fb1d file
  example NAME  Load built-in example (try: dec_loop)
  ops           Show opcode reference
  help          Show this help
  q / quit      Exit

ISA summary (type 'ops' for full reference):
  Data:    +  -  .  ,  X  x
  Heads:   >< (H0)  )( (H1)  }{ (CL)  ][ (EX)
  CL:      :  ;
  EX:      P  Q  Z
  Bridge:  T
  Jump:    J  (if tape[CL]!=0: jump to tape[H1], deposit EX trail)
═══════════════════════════════════════════════════════════════════════
"""

OPS_TEXT = """
═══════════════════════════════════════════════════════════════════════
 fb1d Instruction Set (v0.1, 21 opcodes + NOP)
═══════════════════════════════════════════════════════════════════════

Data operations (H0-targeted):
  +  (1)   tape[H0]++                              inverse: -
  -  (2)   tape[H0]--                              inverse: +
  .  (3)   tape[H0] += tape[H1]                    inverse: ,
  ,  (4)   tape[H0] -= tape[H1]                    inverse: .
  X  (5)   swap(tape[H0], tape[H1])                self-inverse
  x  (6)   tape[H0] ^= tape[H1]  (XOR)            self-inverse

Head movement:
  >  (7)   H0++  (move right)                      inverse: <
  <  (8)   H0--  (move left)                       inverse: >
  )  (9)   H1++  (move right)                      inverse: (
  (  (10)  H1--  (move left)                       inverse: )
  }  (11)  CL++  (move right)                      inverse: {
  {  (12)  CL--  (move left)                       inverse: }
  ]  (13)  EX++  (move right)                      inverse: [
  [  (14)  EX--  (move left)                       inverse: ]

CL data:
  :  (15)  tape[CL]++                              inverse: ;
  ;  (16)  tape[CL]--                              inverse: :

EX data:
  P  (17)  tape[EX]++  (leave breadcrumb)          inverse: Q
  Q  (18)  tape[EX]--  (erase breadcrumb)          inverse: P
  Z  (19)  swap(tape[H0], tape[EX])                self-inverse

Bridge:
  T  (20)  swap(tape[CL], tape[H0])                self-inverse

Jump:
  J  (21)  if tape[CL] != 0:                       (conditional)
             deposit IP into EX trail (2 fuel cells)
             IP = tape[H1]
           else:
             NOP (just extends EX run count like other non-jumps)

NOP:
  ·  (0)   No operation. All unrecognized bytes are NOP.

═══════════════════════════════════════════════════════════════════════
 Reversibility
═══════════════════════════════════════════════════════════════════════

Every step records a breadcrumb in the EX trail:
  Non-jump:  tape[EX]++ (run-length count of consecutive non-jumps)
  Jump:      EX advances 2 cells — deposits return address + new count

step_back() reads the EX trail:
  tape[EX] > 1:  non-jump — Q, undo instruction at IP-1
  tape[EX] == 1: jump boundary — restore fuel, unjump

Fuel cost: 0 cells per non-jump, 2 cells per jump.

NOP guards: . , X x are NOP when H0==H1 (prevents non-bijective ops).
═══════════════════════════════════════════════════════════════════════
"""


def repl():
    sim = FB1D()

    print("fb1d — 1D reversible tape simulator")
    print("Type 'help' for commands, 'ops' for instruction set")
    print()

    # Default: load the dec_loop example
    sim.load_example('dec_loop')
    print()
    sim.display()

    while True:
        try:
            raw = input(f"\nfb1d[{sim.step_count}]> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if not raw:
            continue

        parts = raw.split()
        cmd = parts[0].lower()

        if cmd in ('q', 'quit', 'exit'):
            break

        elif cmd == 'help':
            print(HELP_TEXT)

        elif cmd == 'ops':
            print(OPS_TEXT)

        elif cmd in ('s', 'step'):
            n = int(parts[1]) if len(parts) > 1 else 1
            for i in range(n):
                # Show pre-step state for single steps
                if n == 1:
                    op = sim._val(sim.ip)
                    ch = OPCODE_TO_CHAR.get(op, '·')
                    print(f"  step {sim.step_count}: IP={sim.ip} op={ch}({op})"
                          f"  H0={sim.h0}[{sim._val(sim.h0)}]"
                          f"  H1={sim.h1}[{sim._val(sim.h1)}]"
                          f"  CL={sim.cl}[{sim._val(sim.cl)}]"
                          f"  EX={sim.ex}[{sim._val(sim.ex)}]")
                sim.step()
                if n == 1:
                    print(f"  → step {sim.step_count}: IP={sim.ip}"
                          f"  H0={sim.h0}[{sim._val(sim.h0)}]"
                          f"  CL={sim.cl}[{sim._val(sim.cl)}]"
                          f"  EX={sim.ex}[{sim._val(sim.ex)}]")
            sim.display()

        elif cmd in ('b', 'back'):
            n = int(parts[1]) if len(parts) > 1 else 1
            for i in range(n):
                if n == 1:
                    print(f"  undo step {sim.step_count}: EX={sim.ex}[{sim._val(sim.ex)}]", end="")
                    if sim._val(sim.ex) == 1:
                        print("  → jump boundary (unjump)")
                    elif sim._val(sim.ex) > 1:
                        print(f"  → non-jump (predecessor at IP-1={sim._wrap(sim.ip-1)})")
                    else:
                        print("  → trail exhausted")
                sim.step_back()
                if n == 1:
                    print(f"  → step {sim.step_count}: IP={sim.ip}"
                          f"  H0={sim.h0}[{sim._val(sim.h0)}]"
                          f"  CL={sim.cl}[{sim._val(sim.cl)}]"
                          f"  EX={sim.ex}[{sim._val(sim.ex)}]")
            sim.display()

        elif cmd in ('d', 'display'):
            if len(parts) >= 3:
                lo, hi = int(parts[1]), int(parts[2])
                sim.display(window=(lo, hi))
            else:
                sim.display()

        elif cmd == 'ex':
            sim.display_ex_trail()

        elif cmd == 'set':
            if len(parts) < 3:
                print("Usage: set POS VAL")
            else:
                pos, val = int(parts[1]), int(parts[2])
                sim._set(pos, val)
                print(f"  tape[{pos}] = {val}")

        elif cmd == 'seto':
            if len(parts) < 3:
                print("Usage: seto POS OPCHAR  (e.g. 'seto 1 -')")
            else:
                pos = int(parts[1])
                ch = parts[2]
                if ch in OPCODES:
                    sim._set(pos, OPCODES[ch])
                    print(f"  tape[{pos}] = {OPCODES[ch]} ({ch})")
                else:
                    print(f"  Unknown opcode char: {ch}")
                    print(f"  Available: {' '.join(sorted(OPCODES.keys()))}")

        elif cmd == 'head':
            if len(parts) < 3:
                print("Usage: head H POS  (H = ip/h0/h1/cl/ex)")
            else:
                h, pos = parts[1].lower(), int(parts[2])
                if h == 'ip':
                    sim.ip = sim._wrap(pos)
                elif h == 'h0':
                    sim.h0 = sim._wrap(pos)
                elif h == 'h1':
                    sim.h1 = sim._wrap(pos)
                elif h == 'cl':
                    sim.cl = sim._wrap(pos)
                elif h == 'ex':
                    sim.ex = sim._wrap(pos)
                else:
                    print(f"  Unknown head: {h} (use ip/h0/h1/cl/ex)")
                    continue
                print(f"  {h} = {pos}")

        elif cmd == 'load':
            if len(parts) < 2:
                print("Usage: load FILENAME")
            else:
                fn = parts[1]
                if not fn.endswith('.fb1d'):
                    fn += '.fb1d'
                try:
                    sim.load_state(fn)
                    print(f"  Loaded {fn}")
                    sim.display()
                except FileNotFoundError:
                    print(f"  File not found: {fn}")

        elif cmd == 'save':
            if len(parts) < 2:
                print("Usage: save FILENAME")
            else:
                fn = parts[1]
                if not fn.endswith('.fb1d'):
                    fn += '.fb1d'
                sim.save_state(fn)
                print(f"  Saved {fn}")

        elif cmd == 'example':
            name = parts[1] if len(parts) > 1 else ''
            if sim.load_example(name):
                sim.display()

        elif cmd == 'state':
            print(f"  IP={sim.ip}  H0={sim.h0}  H1={sim.h1}  CL={sim.cl}  EX={sim.ex}")
            print(f"  step={sim.step_count}  tape_size={sim.size}")

        else:
            print(f"  Unknown command: {cmd}  (type 'help')")


if __name__ == '__main__':
    repl()
