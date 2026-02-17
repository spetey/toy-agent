#!/usr/bin/env python3
"""
F***brain IR Simulator v2 - Conditional Jump with IR Reversibility
Authored or modified by Claude
Version: 2025-01-31 v2.0

This version combines:
- Sam Eisenstat's IR mechanism for reversibility
- Conditional jump (tests tape[CL], swaps IP with tape[H1])
- Accumulator operations (. and ,)

Each step:
  1. swap(tape[IP], IR) - fetch instruction into IR, leave IR's old value on tape
  2. Execute IR (the instruction we just fetched)
  3. IP changes (increment, or swap for J)

State: tape[0..N-1], IP, IR, CL, H0, H1
  - IP = instruction pointer
  - IR = instruction register (holds fetched instruction)
  - CL = control locus (condition for J)
  - H0 = data head 0 (destination for +/-/./,)
  - H1 = data head 1 (jump target for J, source for ./,)

ISA:
  < : CL -= 1                                      (inverse: >)
  > : CL += 1                                      (inverse: <)
  { : H0 -= 1                                      (inverse: })
  } : H0 += 1                                      (inverse: {)
  ( : H1 -= 1                                      (inverse: ))
  ) : H1 += 1                                      (inverse: ()
  + : tape[H0] += 1                                (inverse: -)
  - : tape[H0] -= 1                                (inverse: +)
  x : swap(tape[H0], tape[H1])                     (self-inverse)
  . : tape[H0] += tape[H1]                         (inverse: ,)
  , : tape[H0] -= tape[H1]                         (inverse: .)
  J : if tape[CL] != 0: swap(IP, tape[H1]); IP++   (conditional, self-inverse)
  S : swap(tape[CL], tape[H0])                     (self-inverse)
  G : swap(CL, tape[H0])                           (self-inverse)

All operations are reversible. The tape IS the program.
"""

import sys
from typing import Optional

# Instruction encoding
OPCODES = {
    '<': 1,
    '>': 2,
    '{': 3,
    '}': 4,
    '(': 5,
    ')': 6,
    '+': 7,
    '-': 8,
    'x': 9,
    '.': 10,
    ',': 11,
    'J': 12,
    'S': 13,
    'G': 14,
}

OPCODE_TO_CHAR = {v: k for k, v in OPCODES.items()}
OPCODE_TO_CHAR[0] = '0'

DEFAULT_TAPE_SIZE = 16
ROW_WIDTH = 16


class FBIRv2Simulator:
    def __init__(self, tape_size: int = DEFAULT_TAPE_SIZE):
        self.tape_size = tape_size
        self.tape = [0] * tape_size
        self.ip = 0
        self.ir = 0
        self.cl = 0
        self.h0 = 0
        self.h1 = 0
        self.step_count = 0
        
    def load_program(self, code: str):
        """Load program onto tape."""
        self.tape = [0] * self.tape_size
        self.ip = 0
        self.ir = 0
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
        Execute one instruction with IR mechanism.
        
        Each step:
          1. swap(tape[IP], IR)
          2. Execute IR
          3. IP changes
        """
        self.ip = self.ip % self.tape_size
        
        # Step 1: Swap tape[IP] with IR
        tmp = self.tape[self.ip]
        self.tape[self.ip] = self.ir
        self.ir = tmp
        
        # Step 2: Execute IR
        opcode = self.ir
        
        if opcode == OPCODES['<']:
            self.cl = (self.cl - 1) % self.tape_size
            self.ip = (self.ip + 1) % self.tape_size
            
        elif opcode == OPCODES['>']:
            self.cl = (self.cl + 1) % self.tape_size
            self.ip = (self.ip + 1) % self.tape_size
            
        elif opcode == OPCODES['{']:
            self.h0 = (self.h0 - 1) % self.tape_size
            self.ip = (self.ip + 1) % self.tape_size
            
        elif opcode == OPCODES['}']:
            self.h0 = (self.h0 + 1) % self.tape_size
            self.ip = (self.ip + 1) % self.tape_size
            
        elif opcode == OPCODES['(']:
            self.h1 = (self.h1 - 1) % self.tape_size
            self.ip = (self.ip + 1) % self.tape_size
            
        elif opcode == OPCODES[')']:
            self.h1 = (self.h1 + 1) % self.tape_size
            self.ip = (self.ip + 1) % self.tape_size
            
        elif opcode == OPCODES['+']:
            h0_idx = self.h0 % self.tape_size
            self.tape[h0_idx] = (self.tape[h0_idx] + 1) & 0xFF
            self.ip = (self.ip + 1) % self.tape_size
            
        elif opcode == OPCODES['-']:
            h0_idx = self.h0 % self.tape_size
            self.tape[h0_idx] = (self.tape[h0_idx] - 1) & 0xFF
            self.ip = (self.ip + 1) % self.tape_size
            
        elif opcode == OPCODES['x']:
            h0_idx = self.h0 % self.tape_size
            h1_idx = self.h1 % self.tape_size
            tmp = self.tape[h0_idx]
            self.tape[h0_idx] = self.tape[h1_idx]
            self.tape[h1_idx] = tmp
            self.ip = (self.ip + 1) % self.tape_size
            
        elif opcode == OPCODES['.']:
            h0_idx = self.h0 % self.tape_size
            h1_idx = self.h1 % self.tape_size
            self.tape[h0_idx] = (self.tape[h0_idx] + self.tape[h1_idx]) & 0xFF
            self.ip = (self.ip + 1) % self.tape_size
            
        elif opcode == OPCODES[',']:
            h0_idx = self.h0 % self.tape_size
            h1_idx = self.h1 % self.tape_size
            self.tape[h0_idx] = (self.tape[h0_idx] - self.tape[h1_idx]) & 0xFF
            self.ip = (self.ip + 1) % self.tape_size
            
        elif opcode == OPCODES['J']:
            # Conditional Jump: if tape[CL] != 0, swap(IP, tape[H1])
            cl_idx = self.cl % self.tape_size
            h1_idx = self.h1 % self.tape_size
            if self.tape[cl_idx] != 0:
                tmp = self.ip
                self.ip = self.tape[h1_idx] % self.tape_size
                self.tape[h1_idx] = tmp
            self.ip = (self.ip + 1) % self.tape_size
            
        elif opcode == OPCODES['S']:
            cl_idx = self.cl % self.tape_size
            h0_idx = self.h0 % self.tape_size
            tmp = self.tape[cl_idx]
            self.tape[cl_idx] = self.tape[h0_idx]
            self.tape[h0_idx] = tmp
            self.ip = (self.ip + 1) % self.tape_size
        
        elif opcode == OPCODES['G']:
            h0_idx = self.h0 % self.tape_size
            tmp = self.cl
            self.cl = self.tape[h0_idx]
            self.tape[h0_idx] = tmp
            self.ip = (self.ip + 1) % self.tape_size
            
        else:
            # NOP
            self.ip = (self.ip + 1) % self.tape_size
            
        self.step_count += 1
        return True
    
    def step_back(self) -> bool:
        """Reverse one instruction - not yet implemented."""
        print("step_back not yet implemented")
        return False
    
    def display(self, compact: bool = False):
        """Display current state."""
        ir_char = OPCODE_TO_CHAR.get(self.ir, '?')
        print(f"\n=== Step {self.step_count} ===")
        print(f"IR={self.ir} ({ir_char}), IP={self.ip}, CL={self.cl}, H0={self.h0}, H1={self.h1}")
        
        num_rows = (self.tape_size + ROW_WIDTH - 1) // ROW_WIDTH
        
        for row in range(num_rows):
            start = row * ROW_WIDTH
            end = min(start + ROW_WIDTH, self.tape_size)
            
            if compact:
                self._display_compact(start, end)
            else:
                if row > 0:
                    print()
                self._display_full(start, end)
    
    def _display_full(self, start: int, end: int):
        idx_str = "Idx: "
        for i in range(start, end):
            idx_str += f"{i:5d} "
        print(idx_str)
        
        val_str = "Val: "
        for i in range(start, end):
            val_str += f"{self.tape[i]:5d} "
        print(val_str)
        
        ins_str = "Ins: "
        for i in range(start, end):
            char = OPCODE_TO_CHAR.get(self.tape[i], '?')
            ins_str += f"{char:>5} "
        print(ins_str)
        
        ptr1_str = "     "
        for i in range(start, end):
            markers = []
            if i == self.ip:
                markers.append("IP")
            if i == self.cl % self.tape_size:
                markers.append("CL")
            ptr1_str += f"{','.join(markers):>5} " if markers else "      "
        print(ptr1_str)
        
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
        print(f"{start:3d}: ", end="")
        for i in range(start, end):
            char = OPCODE_TO_CHAR.get(self.tape[i], '?')
            markers = []
            if i == self.ip:
                markers.append('→')
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
    sim = FBIRv2Simulator(tape_size=DEFAULT_TAPE_SIZE)
    compact_mode = False
    
    print("=" * 70)
    print("F***brain IR Simulator v2 - Conditional Jump")
    print("=" * 70)
    print(f"""
Tape size: {sim.tape_size}

ISA (opcode in parens):
  < (1)  CL--                  > (2)  CL++
  {{ (3)  H0--                  }} (4)  H0++
  ( (5)  H1--                  ) (6)  H1++
  + (7)  tape[H0]++            - (8)  tape[H0]--
  x (9)  swap tape[H0],tape[H1]
  . (10) tape[H0] += tape[H1]  , (11) tape[H0] -= tape[H1]
  J (12) if tape[CL]: swap(IP, tape[H1]); IP++
  S (13) swap tape[CL],tape[H0]
  G (14) swap CL,tape[H0]

Commands: tape, load, save, data, cl/h0/h1/ip/ir, step/s, run, show, compact, length, reset, help, quit
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
                sim.step()
                sim.display(compact=compact_mode)
            elif cmd in ('back', 'b', 'r'):
                sim.step_back()
                sim.display(compact=compact_mode)
            elif cmd == 'run':
                n = int(parts[1]) if len(parts) > 1 else 100
                for _ in range(n):
                    sim.step()
                print(f"Ran {n} steps")
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
            elif cmd == 'ir':
                if len(parts) > 1:
                    sim.ir = int(parts[1]) & 0xFF
                print(f"IR = {sim.ir} ({OPCODE_TO_CHAR.get(sim.ir, '?')})")
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
                sim = FBIRv2Simulator(tape_size=sim.tape_size)
                print("Reset")
                sim.display(compact=compact_mode)
            elif cmd == 'show':
                sim.display(compact=compact_mode)
            elif cmd == 'help':
                print(f"""
Tape size: {sim.tape_size}

ISA (opcode in parens):
  < (1)  CL--                  > (2)  CL++
  {{ (3)  H0--                  }} (4)  H0++
  ( (5)  H1--                  ) (6)  H1++
  + (7)  tape[H0]++            - (8)  tape[H0]--
  x (9)  swap tape[H0],tape[H1]
  . (10) tape[H0] += tape[H1]  , (11) tape[H0] -= tape[H1]
  J (12) if tape[CL]: swap(IP, tape[H1]); IP++
  S (13) swap tape[CL],tape[H0]
  G (14) swap CL,tape[H0]

Each step: 1) swap(tape[IP], IR)  2) execute IR  3) IP changes

Commands:
  tape <code>       Load program onto tape
  data <pos> <v>... Set tape values at position
  ip/ir/cl/h0/h1 <v> Set register value
  step / s          Execute one instruction
  back / b / r      Reverse one instruction (stub)
  run [n]           Run n steps (default 100)
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
                        f.write(f"tape_size={sim.tape_size}\n")
                        f.write(f"ip={sim.ip}\nir={sim.ir}\ncl={sim.cl}\nh0={sim.h0}\nh1={sim.h1}\n")
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
                                    sim = FBIRv2Simulator(tape_size=int(v))
                                elif k == 'ip': sim.ip = int(v)
                                elif k == 'ir': sim.ir = int(v)
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
                print(f"Unknown: {cmd}")
                
        except Exception as e:
            print(f"Error: {e}")


if __name__ == "__main__":
    interactive_session()
