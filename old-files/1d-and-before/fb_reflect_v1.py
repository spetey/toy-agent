#!/usr/bin/env python3
"""
F***brain Reflective Simulator v1 - Bouncing IP with Mirrors
Authored or modified by Claude
Version: 2025-02-01 v1.0

A reversible programming model where the instruction pointer has a direction
and can bounce off "walls" (unconditional) or "mirrors" (conditional).

Execution model:
  1. Read instruction at tape[IP]
  2. Execute it (may flip DIR)
  3. IP += DIR

State: tape[0..N-1], IP, DIR, CL, H0, H1
  - IP  = instruction pointer (position)
  - DIR = direction (+1 = right, -1 = left)
  - CL  = control locus (condition register, points to tape)
  - H0  = data head 0 (destination for +/-/./,)
  - H1  = data head 1 (source for ./,, swap partner for x/F)

ISA:
  > (1)  : CL++                                    (inverse: <)
  < (2)  : CL--                                    (inverse: >)
  } (3)  : H0++                                    (inverse: {)
  { (4)  : H0--                                    (inverse: })
  ) (5)  : H1++                                    (inverse: ()
  ( (6)  : H1--                                    (inverse: ))
  + (7)  : tape[H0]++                              (inverse: -)
  - (8)  : tape[H0]--                              (inverse: +)
  x (9)  : swap(tape[H0], tape[H1])                (self-inverse)
  . (10) : tape[H0] += tape[H1]                    (inverse: ,)
  , (11) : tape[H0] -= tape[H1]                    (inverse: .)
  F (12) : if tape[CL]!=0: swap(tape[H0],tape[H1]) (self-inverse, Fredkin-style)
  S (13) : swap(tape[CL], tape[H0])                (self-inverse)
  G (14) : swap(CL, tape[H0])                      (self-inverse)
  W (15) : DIR := -DIR                             (self-inverse, unconditional wall)
  M (16) : if tape[CL]!=0: DIR := -DIR             (self-inverse, reflect on nonzero)
  N (17) : if tape[CL]==0: DIR := -DIR             (self-inverse, reflect on zero)

Reversibility:
  To reverse from (IP, DIR, tape, ...):
    - prev_IP = IP - DIR
    - prev_instr = tape[prev_IP]
    - Undo the instruction
    - Determine prev_DIR based on instruction and condition
"""

import sys
from typing import Optional

# Instruction encoding
OPCODES = {
    '>': 1,
    '<': 2,
    '}': 3,
    '{': 4,
    ')': 5,
    '(': 6,
    '+': 7,
    '-': 8,
    'x': 9,
    '.': 10,
    ',': 11,
    'F': 12,
    'S': 13,
    'G': 14,
    'W': 15,
    'M': 16,
    'N': 17,
}

OPCODE_TO_CHAR = {v: k for k, v in OPCODES.items()}
OPCODE_TO_CHAR[0] = '·'  # NOP displayed as middle dot

DEFAULT_TAPE_SIZE = 16
ROW_WIDTH = 16


class FBReflectSimulator:
    def __init__(self, tape_size: int = DEFAULT_TAPE_SIZE):
        self.tape_size = tape_size
        self.tape = [0] * tape_size
        self.ip = 0
        self.dir = 1  # +1 = right, -1 = left
        self.cl = 0
        self.h0 = 0
        self.h1 = 0
        self.step_count = 0
        
    def load_program(self, code: str):
        """Load program onto tape."""
        self.tape = [0] * self.tape_size
        self.ip = 0
        self.dir = 1
        self.cl = 0
        self.h0 = 0
        self.h1 = 0
        self.step_count = 0
        
        program_len = 0
        for char in code:
            if char in OPCODES:
                if program_len >= self.tape_size:
                    raise ValueError(f"Program too long for tape size {self.tape_size}")
                self.tape[program_len] = OPCODES[char]
                program_len += 1
        
        return program_len
    
    def step(self) -> bool:
        """
        Execute one instruction.
        
        Each step:
          1. Read tape[IP]
          2. Execute it
          3. IP += DIR
        """
        self.ip = self.ip % self.tape_size
        opcode = self.tape[self.ip]
        
        if opcode == OPCODES['>']:
            self.cl = (self.cl + 1) % self.tape_size
            
        elif opcode == OPCODES['<']:
            self.cl = (self.cl - 1) % self.tape_size
            
        elif opcode == OPCODES['}']:
            self.h0 = (self.h0 + 1) % self.tape_size
            
        elif opcode == OPCODES['{']:
            self.h0 = (self.h0 - 1) % self.tape_size
            
        elif opcode == OPCODES[')']:
            self.h1 = (self.h1 + 1) % self.tape_size
            
        elif opcode == OPCODES['(']:
            self.h1 = (self.h1 - 1) % self.tape_size
            
        elif opcode == OPCODES['+']:
            h0_idx = self.h0 % self.tape_size
            self.tape[h0_idx] = (self.tape[h0_idx] + 1) & 0xFF
            
        elif opcode == OPCODES['-']:
            h0_idx = self.h0 % self.tape_size
            self.tape[h0_idx] = (self.tape[h0_idx] - 1) & 0xFF
            
        elif opcode == OPCODES['x']:
            h0_idx = self.h0 % self.tape_size
            h1_idx = self.h1 % self.tape_size
            self.tape[h0_idx], self.tape[h1_idx] = self.tape[h1_idx], self.tape[h0_idx]
            
        elif opcode == OPCODES['.']:
            h0_idx = self.h0 % self.tape_size
            h1_idx = self.h1 % self.tape_size
            self.tape[h0_idx] = (self.tape[h0_idx] + self.tape[h1_idx]) & 0xFF
            
        elif opcode == OPCODES[',']:
            h0_idx = self.h0 % self.tape_size
            h1_idx = self.h1 % self.tape_size
            self.tape[h0_idx] = (self.tape[h0_idx] - self.tape[h1_idx]) & 0xFF
            
        elif opcode == OPCODES['F']:
            # Fredkin-style: conditional swap
            cl_idx = self.cl % self.tape_size
            if self.tape[cl_idx] != 0:
                h0_idx = self.h0 % self.tape_size
                h1_idx = self.h1 % self.tape_size
                self.tape[h0_idx], self.tape[h1_idx] = self.tape[h1_idx], self.tape[h0_idx]
            
        elif opcode == OPCODES['S']:
            cl_idx = self.cl % self.tape_size
            h0_idx = self.h0 % self.tape_size
            self.tape[cl_idx], self.tape[h0_idx] = self.tape[h0_idx], self.tape[cl_idx]
        
        elif opcode == OPCODES['G']:
            h0_idx = self.h0 % self.tape_size
            self.cl, self.tape[h0_idx] = self.tape[h0_idx], self.cl
            
        elif opcode == OPCODES['W']:
            # Unconditional wall: flip direction
            self.dir = -self.dir
            
        elif opcode == OPCODES['M']:
            # Mirror: reflect if tape[CL] != 0
            cl_idx = self.cl % self.tape_size
            if self.tape[cl_idx] != 0:
                self.dir = -self.dir
                
        elif opcode == OPCODES['N']:
            # Mirror: reflect if tape[CL] == 0
            cl_idx = self.cl % self.tape_size
            if self.tape[cl_idx] == 0:
                self.dir = -self.dir
        
        # else: NOP (opcode 0 or unknown)
        
        # Move IP in current direction
        self.ip = (self.ip + self.dir) % self.tape_size
        self.step_count += 1
        return True
    
    def step_back(self) -> bool:
        """
        Reverse one instruction.
        
        To reverse from (IP, DIR, tape, ...):
          1. prev_IP = IP - DIR
          2. prev_instr = tape[prev_IP]
          3. Determine prev_DIR based on instruction
          4. Undo the instruction's effect
        """
        # Find previous position
        prev_ip = (self.ip - self.dir) % self.tape_size
        opcode = self.tape[prev_ip]
        
        # Determine prev_DIR
        if opcode == OPCODES['W']:
            # W always flips, so prev_DIR was opposite
            prev_dir = -self.dir
        elif opcode == OPCODES['M']:
            # M reflects if tape[CL] != 0
            cl_idx = self.cl % self.tape_size
            if self.tape[cl_idx] != 0:
                prev_dir = -self.dir  # reflected
            else:
                prev_dir = self.dir   # passed through
        elif opcode == OPCODES['N']:
            # N reflects if tape[CL] == 0
            cl_idx = self.cl % self.tape_size
            if self.tape[cl_idx] == 0:
                prev_dir = -self.dir  # reflected
            else:
                prev_dir = self.dir   # passed through
        else:
            # All other instructions don't change direction
            prev_dir = self.dir
        
        # Now undo the instruction's effect
        if opcode == OPCODES['>']:
            # Was CL++, undo with CL--
            self.cl = (self.cl - 1) % self.tape_size
            
        elif opcode == OPCODES['<']:
            # Was CL--, undo with CL++
            self.cl = (self.cl + 1) % self.tape_size
            
        elif opcode == OPCODES['}']:
            # Was H0++, undo with H0--
            self.h0 = (self.h0 - 1) % self.tape_size
            
        elif opcode == OPCODES['{']:
            # Was H0--, undo with H0++
            self.h0 = (self.h0 + 1) % self.tape_size
            
        elif opcode == OPCODES[')']:
            # Was H1++, undo with H1--
            self.h1 = (self.h1 - 1) % self.tape_size
            
        elif opcode == OPCODES['(']:
            # Was H1--, undo with H1++
            self.h1 = (self.h1 + 1) % self.tape_size
            
        elif opcode == OPCODES['+']:
            # Was tape[H0]++, undo with --
            h0_idx = self.h0 % self.tape_size
            self.tape[h0_idx] = (self.tape[h0_idx] - 1) & 0xFF
            
        elif opcode == OPCODES['-']:
            # Was tape[H0]--, undo with ++
            h0_idx = self.h0 % self.tape_size
            self.tape[h0_idx] = (self.tape[h0_idx] + 1) & 0xFF
            
        elif opcode == OPCODES['x']:
            # swap is self-inverse
            h0_idx = self.h0 % self.tape_size
            h1_idx = self.h1 % self.tape_size
            self.tape[h0_idx], self.tape[h1_idx] = self.tape[h1_idx], self.tape[h0_idx]
            
        elif opcode == OPCODES['.']:
            # Was tape[H0] += tape[H1], undo with -=
            h0_idx = self.h0 % self.tape_size
            h1_idx = self.h1 % self.tape_size
            self.tape[h0_idx] = (self.tape[h0_idx] - self.tape[h1_idx]) & 0xFF
            
        elif opcode == OPCODES[',']:
            # Was tape[H0] -= tape[H1], undo with +=
            h0_idx = self.h0 % self.tape_size
            h1_idx = self.h1 % self.tape_size
            self.tape[h0_idx] = (self.tape[h0_idx] + self.tape[h1_idx]) & 0xFF
            
        elif opcode == OPCODES['F']:
            # Conditional swap is self-inverse
            cl_idx = self.cl % self.tape_size
            if self.tape[cl_idx] != 0:
                h0_idx = self.h0 % self.tape_size
                h1_idx = self.h1 % self.tape_size
                self.tape[h0_idx], self.tape[h1_idx] = self.tape[h1_idx], self.tape[h0_idx]
            
        elif opcode == OPCODES['S']:
            # swap(tape[CL], tape[H0]) is self-inverse
            cl_idx = self.cl % self.tape_size
            h0_idx = self.h0 % self.tape_size
            self.tape[cl_idx], self.tape[h0_idx] = self.tape[h0_idx], self.tape[cl_idx]
        
        elif opcode == OPCODES['G']:
            # swap(CL, tape[H0]) is self-inverse
            h0_idx = self.h0 % self.tape_size
            self.cl, self.tape[h0_idx] = self.tape[h0_idx], self.cl
        
        # W, M, N don't modify tape/registers (only DIR), and we've handled DIR above
        # NOP: nothing to undo
        
        # Restore state
        self.ip = prev_ip
        self.dir = prev_dir
        self.step_count -= 1
        return True
    
    def display(self, compact: bool = False):
        """Display current state."""
        dir_char = '→' if self.dir > 0 else '←'
        print(f"\n=== Step {self.step_count} ===")
        print(f"IP={self.ip}, DIR={dir_char}, CL={self.cl}, H0={self.h0}, H1={self.h1}")
        
        if compact:
            self._display_compact(0, min(self.tape_size, ROW_WIDTH))
            if self.tape_size > ROW_WIDTH:
                self._display_compact(ROW_WIDTH, min(self.tape_size, ROW_WIDTH * 2))
        else:
            self._display_full(0, min(self.tape_size, ROW_WIDTH))
            if self.tape_size > ROW_WIDTH:
                print()
                self._display_full(ROW_WIDTH, min(self.tape_size, ROW_WIDTH * 2))
    
    def _display_full(self, start: int, end: int):
        """Full display with values, chars, and pointers."""
        # Index row
        idx_str = "     "
        for i in range(start, end):
            idx_str += f"{i:>5} "
        print(idx_str)
        
        # Value row
        val_str = "Val: "
        for i in range(start, end):
            val_str += f"{self.tape[i]:>5} "
        print(val_str)
        
        # Char row
        char_str = "Chr: "
        for i in range(start, end):
            c = OPCODE_TO_CHAR.get(self.tape[i], '?')
            char_str += f"{c:>5} "
        print(char_str)
        
        # IP/CL pointer row
        ptr1_str = "     "
        for i in range(start, end):
            markers = []
            if i == self.ip:
                markers.append("→" if self.dir > 0 else "←")
            if i == self.cl % self.tape_size:
                markers.append("CL")
            ptr1_str += f"{''.join(markers):>5} " if markers else "      "
        print(ptr1_str)
        
        # H0/H1 pointer row
        ptr2_str = "     "
        for i in range(start, end):
            markers = []
            if i == self.h0 % self.tape_size:
                markers.append("H0")
            if i == self.h1 % self.tape_size:
                markers.append("H1")
            ptr2_str += f"{','.join(markers):>5} " if markers else "      "
        print(ptr2_str)
    
    def _display_compact(self, start: int, end: int):
        """Compact single-line display."""
        print(f"{start:3d}: ", end="")
        for i in range(start, end):
            char = OPCODE_TO_CHAR.get(self.tape[i], '?')
            markers = []
            if i == self.ip:
                markers.append('→' if self.dir > 0 else '←')
            if i == self.cl % self.tape_size:
                markers.append('C')
            if i == self.h0 % self.tape_size:
                markers.append('⁰')
            if i == self.h1 % self.tape_size:
                markers.append('¹')
            
            if markers:
                print(f"[{''.join(markers)}{char}]", end="")
            else:
                print(f" {char} ", end="")
        print()


def interactive_session():
    sim = FBReflectSimulator(tape_size=DEFAULT_TAPE_SIZE)
    compact_mode = False
    
    print("=" * 70)
    print("F***brain Reflective Simulator v1 - Bouncing IP with Mirrors")
    print("=" * 70)
    print(f"""
Tape size: {sim.tape_size}

ISA (opcode in parens):
  > (1)  CL++                  < (2)  CL--
  }} (3)  H0++                  {{ (4)  H0--
  ) (5)  H1++                  ( (6)  H1--
  + (7)  tape[H0]++            - (8)  tape[H0]--
  x (9)  swap tape[H0],tape[H1]
  . (10) tape[H0] += tape[H1]  , (11) tape[H0] -= tape[H1]
  F (12) if tape[CL]!=0: swap tape[H0],tape[H1]  (Fredkin)
  S (13) swap tape[CL],tape[H0]
  G (14) swap CL,tape[H0]
  W (15) flip DIR              (unconditional wall)
  M (16) if tape[CL]!=0: flip DIR  (mirror, reflect on nonzero)
  N (17) if tape[CL]==0: flip DIR  (mirror, reflect on zero)

Execution: read tape[IP], execute, IP += DIR

Commands: tape, load, save, data, cl/h0/h1/ip/dir, step/s, back/b, run, 
          runback, show, compact, length, reset, help, quit
""")
    
    sim.display(compact=compact_mode)
    
    while True:
        try:
            line = input("\nFB> ").strip()
            if not line:
                continue
                
            parts = line.split()
            cmd = parts[0].lower()
            
            if cmd in ('quit', 'q'):
                print("Goodbye!")
                break
            elif cmd == 'tape':
                if len(parts) > 1:
                    prog_len = sim.load_program(' '.join(parts[1:]))
                    print(f"Loaded {prog_len} instructions")
                sim.display(compact=compact_mode)
            elif cmd in ('step', 's'):
                n = 1
                if len(parts) > 1:
                    n = int(parts[1])
                for _ in range(n):
                    sim.step()
                sim.display(compact=compact_mode)
            elif cmd in ('back', 'b', 'r'):
                n = 1
                if len(parts) > 1:
                    n = int(parts[1])
                for _ in range(n):
                    if sim.step_count > 0:
                        sim.step_back()
                    else:
                        print("Already at step 0")
                        break
                sim.display(compact=compact_mode)
            elif cmd == 'run':
                n = int(parts[1]) if len(parts) > 1 else 100
                for _ in range(n):
                    sim.step()
                print(f"Ran {n} steps forward")
                sim.display(compact=compact_mode)
            elif cmd == 'runback':
                n = int(parts[1]) if len(parts) > 1 else 100
                actual = 0
                for _ in range(n):
                    if sim.step_count > 0:
                        sim.step_back()
                        actual += 1
                    else:
                        break
                print(f"Ran {actual} steps backward")
                sim.display(compact=compact_mode)
            elif cmd == 'compact':
                compact_mode = not compact_mode
                print(f"Compact mode: {'ON' if compact_mode else 'OFF'}")
                sim.display(compact=compact_mode)
            elif cmd == 'length':
                if len(parts) > 1:
                    new_size = int(parts[1])
                    old_tape = sim.tape
                    sim.tape_size = new_size
                    sim.tape = [0] * new_size
                    for i in range(min(len(old_tape), new_size)):
                        sim.tape[i] = old_tape[i]
                    print(f"Tape size: {new_size}")
                else:
                    print(f"Tape size: {sim.tape_size}")
                sim.display(compact=compact_mode)
            elif cmd == 'data':
                if len(parts) >= 3:
                    offset = int(parts[1])
                    values = [int(v) for v in parts[2:]]
                    for i, v in enumerate(values):
                        sim.tape[(offset + i) % sim.tape_size] = v & 0xFF
                    print(f"Set tape[{offset}:{offset+len(values)}] = {values}")
                sim.display(compact=compact_mode)
            elif cmd == 'ip':
                if len(parts) > 1:
                    sim.ip = int(parts[1]) % sim.tape_size
                print(f"IP = {sim.ip}")
                sim.display(compact=compact_mode)
            elif cmd == 'dir':
                if len(parts) > 1:
                    val = parts[1].lower()
                    if val in ('r', 'right', '+', '+1', '1'):
                        sim.dir = 1
                    elif val in ('l', 'left', '-', '-1'):
                        sim.dir = -1
                    else:
                        sim.dir = int(parts[1])
                        if sim.dir >= 0:
                            sim.dir = 1
                        else:
                            sim.dir = -1
                dir_char = '→ (right)' if sim.dir > 0 else '← (left)'
                print(f"DIR = {dir_char}")
                sim.display(compact=compact_mode)
            elif cmd == 'cl':
                if len(parts) > 1:
                    sim.cl = int(parts[1])
                print(f"CL = {sim.cl}")
                sim.display(compact=compact_mode)
            elif cmd == 'h0':
                if len(parts) > 1:
                    sim.h0 = int(parts[1])
                print(f"H0 = {sim.h0}")
                sim.display(compact=compact_mode)
            elif cmd == 'h1':
                if len(parts) > 1:
                    sim.h1 = int(parts[1])
                print(f"H1 = {sim.h1}")
                sim.display(compact=compact_mode)
            elif cmd == 'reset':
                sim = FBReflectSimulator(tape_size=sim.tape_size)
                print("Reset")
                sim.display(compact=compact_mode)
            elif cmd == 'show':
                sim.display(compact=compact_mode)
            elif cmd == 'help':
                print(f"""
Tape size: {sim.tape_size}

ISA (opcode in parens):
  > (1)  CL++                  < (2)  CL--
  }} (3)  H0++                  {{ (4)  H0--
  ) (5)  H1++                  ( (6)  H1--
  + (7)  tape[H0]++            - (8)  tape[H0]--
  x (9)  swap tape[H0],tape[H1]
  . (10) tape[H0] += tape[H1]  , (11) tape[H0] -= tape[H1]
  F (12) if tape[CL]!=0: swap tape[H0],tape[H1]  (Fredkin)
  S (13) swap tape[CL],tape[H0]
  G (14) swap CL,tape[H0]
  W (15) flip DIR              (unconditional wall)
  M (16) if tape[CL]!=0: flip DIR  (mirror, reflect on nonzero)
  N (17) if tape[CL]==0: flip DIR  (mirror, reflect on zero)

Execution: read tape[IP], execute, IP += DIR
Reversibility: condition value at M/N disambiguates path taken

Commands:
  tape <code>       Load program onto tape (resets state)
  data <pos> <v>... Set tape values at position
  ip/dir/cl/h0/h1 <v> Set register value (dir: r/l/+/-)
  step / s [n]      Execute n instructions (default 1)
  back / b [n]      Reverse n instructions (default 1)
  run [n]           Run n steps forward (default 100)
  runback [n]       Run n steps backward (default 100)
  show              Display current state
  compact           Toggle compact display
  length [n]        Show/set tape length
  save <file>       Save state to file
  load <file>       Load state from file
  reset             Reset simulator
  help              Show this help
  quit / q          Exit
""")
            elif cmd == 'save':
                if len(parts) > 1:
                    fn = parts[1] if parts[1].endswith('.fb') else parts[1] + '.fb'
                    with open(fn, 'w') as f:
                        f.write(f"# F***brain Reflective v1 state\n")
                        f.write(f"tape_size={sim.tape_size}\n")
                        f.write(f"ip={sim.ip}\ndir={sim.dir}\ncl={sim.cl}\nh0={sim.h0}\nh1={sim.h1}\n")
                        f.write(f"step={sim.step_count}\n")
                        f.write(f"tape={','.join(str(v) for v in sim.tape)}\n")
                    print(f"Saved to {fn}")
            elif cmd == 'load':
                if len(parts) > 1:
                    fn = parts[1] if parts[1].endswith('.fb') else parts[1] + '.fb'
                    with open(fn, 'r') as f:
                        for line in f:
                            line = line.strip()
                            if '=' in line and not line.startswith('#'):
                                k, v = line.split('=', 1)
                                if k == 'tape_size':
                                    sim = FBReflectSimulator(tape_size=int(v))
                                elif k == 'ip': sim.ip = int(v)
                                elif k == 'dir': sim.dir = int(v)
                                elif k == 'cl': sim.cl = int(v)
                                elif k == 'h0': sim.h0 = int(v)
                                elif k == 'h1': sim.h1 = int(v)
                                elif k == 'step': sim.step_count = int(v)
                                elif k == 'tape':
                                    for i, val in enumerate(v.split(',')):
                                        if i < sim.tape_size:
                                            sim.tape[i] = int(val)
                    print(f"Loaded {fn}")
                sim.display(compact=compact_mode)
            else:
                print(f"Unknown command: {cmd}. Type 'help' for commands.")
                
        except KeyboardInterrupt:
            print("\nInterrupted")
        except EOFError:
            print("\nGoodbye!")
            break
        except Exception as e:
            print(f"Error: {e}")


if __name__ == "__main__":
    interactive_session()
