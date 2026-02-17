#!/usr/bin/env python3
"""
RBefunge Simulator - Reversible Befunge-style Language
Authored or modified by Claude
Version: 2025-01-29 v1.0

A reversible computation language using direction/momentum instead of jump-swap.
The IP has direction (Dir = +1 or -1) and mirrors conditionally reverse it.

State: tape[0..N-1], IP, Dir, H0, H1
  - IP = instruction pointer (position)
  - Dir = direction/momentum (+1 or -1)
  - H0 = data head 0 (primary, where +/- act, condition for mirrors)
  - H1 = data head 1 (secondary, for swaps and reversible copy)

ISA:
  + : tape[H0] += 1                    (inverse: -)
  - : tape[H0] -= 1                    (inverse: +)
  < : H0 -= 1                          (inverse: >)
  > : H0 += 1                          (inverse: <)
  { : H1 -= 1                          (inverse: })
  } : H1 += 1                          (inverse: {)
  . : tape[H1] += tape[H0]             (inverse: ,)  "copy to H1"
  , : tape[H0] += tape[H1]             (inverse: .)  "accumulate to H0"
  x : swap(tape[H0], tape[H1])         (self-inverse)
  [ : if tape[H0] == 0: Dir *= -1      (self-inverse) "zero mirror"
  ] : if tape[H0] != 0: Dir *= -1      (self-inverse) "nonzero mirror"

All operations are reversible. IP advances by Dir after each instruction.
"""

import sys
from typing import Optional

# Instruction encoding
OPCODES = {
    '+': 1,
    '-': 2,
    '<': 3,
    '>': 4,
    '{': 5,
    '}': 6,
    '.': 7,
    ',': 8,
    'x': 9,
    '[': 10,
    ']': 11,
}

OPCODE_TO_CHAR = {v: k for k, v in OPCODES.items()}
OPCODE_TO_CHAR[0] = '0'  # Zero byte displayed as '0'

DEFAULT_TAPE_SIZE = 32
ROW_WIDTH = 32  # Display wraps at this width


class RBefungeSimulator:
    def __init__(self, tape_size: int = DEFAULT_TAPE_SIZE):
        self.tape_size = tape_size
        self.tape = [0] * tape_size
        self.ip = 0
        self.dir = 1    # Direction: +1 (right) or -1 (left)
        self.h0 = 0     # Data head 0
        self.h1 = 0     # Data head 1
        self.step_count = 0
        
    def load_program(self, code: str, data_offset: Optional[int] = None,
                     initial_data: Optional[list] = None):
        """
        Load an RBefunge program onto the tape.
        
        code: string of instructions (+ - < > { } . , x [ ])
        data_offset: where to place initial data (defaults to after program)
        initial_data: list of byte values to place at data_offset
        """
        # Reset state
        self.tape = [0] * self.tape_size
        self.ip = 0
        self.dir = 1
        self.h0 = 0
        self.h1 = 0
        self.step_count = 0
        
        # Encode program onto tape
        program_len = 0
        for char in code:
            if char in OPCODES:
                if program_len >= self.tape_size:
                    raise ValueError(f"Program too long for tape size {self.tape_size}")
                self.tape[program_len] = OPCODES[char]
                program_len += 1
            elif char.isspace():
                continue  # Skip whitespace
            else:
                raise ValueError(f"Unknown instruction: {char}")
        
        # Place initial data
        if initial_data:
            offset = data_offset if data_offset is not None else program_len
            for i, val in enumerate(initial_data):
                if offset + i >= self.tape_size:
                    raise ValueError("Initial data extends beyond tape")
                self.tape[(offset + i) % self.tape_size] = val & 0xFF
                
        return program_len
    
    def step(self) -> bool:
        """
        Execute one instruction, then advance IP by Dir.
        Returns True always.
        """
        self.ip = self.ip % self.tape_size
        opcode = self.tape[self.ip]
        
        if opcode == OPCODES['+']:
            self.tape[self.h0] = (self.tape[self.h0] + 1) & 0xFF
            
        elif opcode == OPCODES['-']:
            self.tape[self.h0] = (self.tape[self.h0] - 1) & 0xFF
            
        elif opcode == OPCODES['<']:
            self.h0 = (self.h0 - 1) % self.tape_size
            
        elif opcode == OPCODES['>']:
            self.h0 = (self.h0 + 1) % self.tape_size
            
        elif opcode == OPCODES['{']:
            self.h1 = (self.h1 - 1) % self.tape_size
            
        elif opcode == OPCODES['}']:
            self.h1 = (self.h1 + 1) % self.tape_size
            
        elif opcode == OPCODES['.']:
            # Reversible copy: tape[H1] += tape[H0]
            self.tape[self.h1] = (self.tape[self.h1] + self.tape[self.h0]) & 0xFF
            
        elif opcode == OPCODES[',']:
            # Reversible accumulate: tape[H0] += tape[H1]
            self.tape[self.h0] = (self.tape[self.h0] + self.tape[self.h1]) & 0xFF
            
        elif opcode == OPCODES['x']:
            # Swap tape[H0] and tape[H1]
            tmp = self.tape[self.h0]
            self.tape[self.h0] = self.tape[self.h1]
            self.tape[self.h1] = tmp
            
        elif opcode == OPCODES['[']:
            # Zero mirror: if tape[H0] == 0, reverse direction
            if self.tape[self.h0] == 0:
                self.dir *= -1
                
        elif opcode == OPCODES[']']:
            # Nonzero mirror: if tape[H0] != 0, reverse direction
            if self.tape[self.h0] != 0:
                self.dir *= -1
        
        # else: NOP (opcode 0 or unknown)
        
        # Advance IP by direction
        self.ip = (self.ip + self.dir) % self.tape_size
        self.step_count += 1
        return True
    
    def step_back(self) -> bool:
        """
        Reverse one instruction by computing the previous state.
        TRUE reversibility - no history needed!
        
        Key insight: to reverse, we:
        1. Move IP backwards (opposite of Dir)
        2. Execute the inverse of the instruction at that position
        
        But wait - Dir might have been different before! If we hit a mirror,
        it flipped Dir. So we need to:
        1. Move IP backwards by current Dir (undo the last IP += Dir)
        2. Check if that instruction is a mirror that would have flipped
        3. If so, flip Dir back, then undo the instruction
        
        Actually simpler: 
        1. prev_ip = (IP - Dir) mod tape_size
        2. Look at instruction at prev_ip
        3. If it's a mirror, check if it would fire (and thus flipped Dir)
        4. Execute inverse operation
        5. Set IP = prev_ip
        """
        # Where did we come from?
        prev_ip = (self.ip - self.dir) % self.tape_size
        opcode = self.tape[prev_ip]
        
        # Handle mirrors specially - they might have flipped Dir
        if opcode == OPCODES['[']:
            # Zero mirror: flips Dir if tape[H0] == 0
            if self.tape[self.h0] == 0:
                self.dir *= -1  # Undo the flip
            # Mirror is self-inverse, no other undo needed
            
        elif opcode == OPCODES[']']:
            # Nonzero mirror: flips Dir if tape[H0] != 0
            if self.tape[self.h0] != 0:
                self.dir *= -1  # Undo the flip
            # Mirror is self-inverse, no other undo needed
            
        elif opcode == OPCODES['+']:
            # + incremented tape[H0], so decrement to undo
            self.tape[self.h0] = (self.tape[self.h0] - 1) & 0xFF
            
        elif opcode == OPCODES['-']:
            # - decremented tape[H0], so increment to undo
            self.tape[self.h0] = (self.tape[self.h0] + 1) & 0xFF
            
        elif opcode == OPCODES['<']:
            # < decremented H0, so increment to undo
            self.h0 = (self.h0 + 1) % self.tape_size
            
        elif opcode == OPCODES['>']:
            # > incremented H0, so decrement to undo
            self.h0 = (self.h0 - 1) % self.tape_size
            
        elif opcode == OPCODES['{']:
            # { decremented H1, so increment to undo
            self.h1 = (self.h1 + 1) % self.tape_size
            
        elif opcode == OPCODES['}']:
            # } incremented H1, so decrement to undo
            self.h1 = (self.h1 - 1) % self.tape_size
            
        elif opcode == OPCODES['.']:
            # . did tape[H1] += tape[H0], so subtract to undo
            self.tape[self.h1] = (self.tape[self.h1] - self.tape[self.h0]) & 0xFF
            
        elif opcode == OPCODES[',']:
            # , did tape[H0] += tape[H1], so subtract to undo
            self.tape[self.h0] = (self.tape[self.h0] - self.tape[self.h1]) & 0xFF
            
        elif opcode == OPCODES['x']:
            # x is self-inverse: swap again
            tmp = self.tape[self.h0]
            self.tape[self.h0] = self.tape[self.h1]
            self.tape[self.h1] = tmp
        
        # else: NOP, nothing to undo
        
        self.ip = prev_ip
        self.step_count -= 1
        return True
    
    def display(self, compact: bool = False):
        """Display the current state of the simulator."""
        dir_str = "→" if self.dir == 1 else "←"
        print(f"\n=== Step {self.step_count} ===")
        print(f"IP={self.ip}, Dir={self.dir} ({dir_str}), H0={self.h0}, H1={self.h1}")
        
        # Display in rows of ROW_WIDTH
        for row_start in range(0, self.tape_size, ROW_WIDTH):
            row_end = min(row_start + ROW_WIDTH, self.tape_size)
            if compact:
                self._display_compact(row_start, row_end)
            else:
                self._display_full(row_start, row_end)
            if row_end < self.tape_size:
                print()  # Blank line between rows
    
    def _display_full(self, start: int, end: int):
        """Full multi-line display with markers."""
        # Index line
        idx_line = "Idx:  "
        for i in range(start, end):
            idx_line += f"{i:4d} "
        print(idx_line)
        
        # Tape values (decimal)
        val_line = "Val:  "
        for i in range(start, end):
            val_line += f"{self.tape[i]:4d} "
        print(val_line)
        
        # Tape as instructions
        ins_line = "Ins:  "
        for i in range(start, end):
            char = OPCODE_TO_CHAR.get(self.tape[i], '?')
            ins_line += f"   {char} "
        print(ins_line)
        
        # Pointer markers line
        ptr_line = "      "
        for i in range(start, end):
            markers = []
            if i == self.ip:
                markers.append("→" if self.dir == 1 else "←")
            if i == self.h0:
                markers.append("H0")
            if i == self.h1:
                markers.append("H1")
            
            if markers:
                mark_str = ",".join(markers)
                ptr_line += f"{mark_str:>4s} "
            else:
                ptr_line += "     "
        print(ptr_line)
    
    def _display_compact(self, start: int, end: int):
        """Single-line compact display."""
        line = ""
        for i in range(start, end):
            char = OPCODE_TO_CHAR.get(self.tape[i], '?')
            
            # Build marker prefix
            markers = ""
            if i == self.ip:
                markers += "→" if self.dir == 1 else "←"
            if i == self.h0:
                markers += "⁰"
            if i == self.h1:
                markers += "¹"
            
            if markers:
                line += f"[{markers}{char}]"
            else:
                line += f" {char} "
        print(line)


def interactive_session():
    """Run an interactive RBefunge session."""
    sim = RBefungeSimulator(tape_size=DEFAULT_TAPE_SIZE)
    
    print("=" * 70)
    print("RBefunge Simulator - Reversible Befunge - Interactive Mode")
    print("=" * 70)
    print(f"""
Tape size: {DEFAULT_TAPE_SIZE} (all addresses wrap mod {DEFAULT_TAPE_SIZE})

State: tape[0..{DEFAULT_TAPE_SIZE-1}], IP, Dir, H0, H1
  Dir = direction (+1 right, -1 left)

ISA:
  +  tape[H0]++        -  tape[H0]--        <  H0--            >  H0++
  {{  H1--              }}  H1++              x  swap tape[H0],tape[H1]
  .  tape[H1]+=tape[H0]                     ,  tape[H0]+=tape[H1]
  [  if tape[H0]==0: Dir*=-1 (zero mirror)
  ]  if tape[H0]!=0: Dir*=-1 (nonzero mirror)

Commands:
  tape <code>           Load program onto tape (e.g., tape +>+>+)
  load <file>           Load saved state from file (adds .rbf extension)
  save <name>           Save state to file (adds .rbf extension)
  data <pos> <val>...   Set tape values at position
  h0/h1 <pos>           Set head position
  dir <+1|-1>           Set direction
  step / s              Execute one instruction
  back / b / r          Reverse one instruction (TRUE reversibility!)
  run [n]               Run n steps (default: 1000)
  compact               Toggle compact display mode
  reset                 Reset simulator
  help                  Show this help
  quit / q              Exit

Example:
  tape +>+>]+<+<[
  data 10 5
  h0 10
  run 20
""")
    
    compact_mode = False
    
    # Show initial state
    sim.display(compact=compact_mode)
    
    while True:
        try:
            cmd = input("\nRBF> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye!")
            break
            
        if not cmd:
            continue
            
        parts = cmd.split()
        command = parts[0].lower()
        
        try:
            if command in ('quit', 'q', 'exit'):
                print("Goodbye!")
                break
                
            elif command == 'help':
                print("Commands: tape, load, save, data, h0, h1, dir, step, back, run, compact, reset, quit")
                sim.display(compact=compact_mode)
                
            elif command == 'tape':
                if len(parts) < 2:
                    print("Usage: tape <code>  (e.g., tape +>+>]+<+<[)")
                    sim.display(compact=compact_mode)
                    continue
                code = ' '.join(parts[1:])
                prog_len = sim.load_program(code)
                print(f"Loaded {prog_len} instructions onto tape")
                sim.display(compact=compact_mode)
            
            elif command == 'load':
                if len(parts) < 2:
                    print("Usage: load <file>  (loads state from file.rbf)")
                    sim.display(compact=compact_mode)
                    continue
                arg = parts[1]
                filename = arg if arg.endswith('.rbf') else arg + '.rbf'
                import os
                if os.path.exists(filename):
                    try:
                        # First pass: read tape to infer size
                        tape_values = None
                        other_values = {}
                        with open(filename, 'r') as f:
                            for line in f:
                                line = line.strip()
                                if line.startswith('#') or not line:
                                    continue
                                if '=' in line:
                                    key, val = line.split('=', 1)
                                    if key == 'tape':
                                        tape_values = [int(v) for v in val.split(',')]
                                    elif key != 'tape_size':  # Ignore explicit tape_size, infer from tape
                                        other_values[key] = val
                        
                        # Infer tape size from tape data length
                        if tape_values:
                            inferred_size = len(tape_values)
                            if inferred_size != sim.tape_size:
                                sim = RBefungeSimulator(tape_size=inferred_size)
                            for i, v in enumerate(tape_values):
                                sim.tape[i] = v
                        
                        # Apply other values
                        if 'ip' in other_values:
                            sim.ip = int(other_values['ip'])
                        if 'dir' in other_values:
                            sim.dir = int(other_values['dir'])
                        if 'h0' in other_values:
                            sim.h0 = int(other_values['h0'])
                        if 'h1' in other_values:
                            sim.h1 = int(other_values['h1'])
                        if 'step' in other_values:
                            sim.step_count = int(other_values['step'])
                        
                        print(f"Loaded state from {filename} (tape size: {sim.tape_size})")
                        sim.display(compact=compact_mode)
                    except Exception as e:
                        print(f"Error loading: {e}")
                else:
                    print(f"File not found: {filename}")
                
            elif command == 'data':
                if len(parts) < 3:
                    print("Usage: data <position> <value> [value2 ...]")
                    sim.display(compact=compact_mode)
                    continue
                pos = int(parts[1]) % sim.tape_size
                vals = [int(v) for v in parts[2:]]
                for i, v in enumerate(vals):
                    sim.tape[(pos + i) % sim.tape_size] = v & 0xFF
                print(f"Set tape[{pos}:{pos+len(vals)}] = {vals}")
                sim.display(compact=compact_mode)
                
            elif command == 'h0':
                if len(parts) < 2:
                    print("Usage: h0 <position>")
                    sim.display(compact=compact_mode)
                    continue
                sim.h0 = int(parts[1]) % sim.tape_size
                print(f"H0 = {sim.h0}")
                sim.display(compact=compact_mode)
                
            elif command == 'h1':
                if len(parts) < 2:
                    print("Usage: h1 <position>")
                    sim.display(compact=compact_mode)
                    continue
                sim.h1 = int(parts[1]) % sim.tape_size
                print(f"H1 = {sim.h1}")
                sim.display(compact=compact_mode)
                
            elif command == 'dir':
                if len(parts) < 2:
                    print("Usage: dir <+1|-1>")
                    sim.display(compact=compact_mode)
                    continue
                d = int(parts[1])
                sim.dir = 1 if d >= 0 else -1
                print(f"Dir = {sim.dir}")
                sim.display(compact=compact_mode)
                
            elif command in ('step', 's'):
                sim.step()
                sim.display(compact=compact_mode)
                
            elif command in ('back', 'b', 'r', 'reverse'):
                sim.step_back()
                print("Reversed one step (TRUE reversibility)")
                sim.display(compact=compact_mode)
                    
            elif command == 'run':
                max_steps = 1000
                if len(parts) > 1:
                    max_steps = int(parts[1])
                steps_run = 0
                while steps_run < max_steps:
                    sim.step()
                    steps_run += 1
                print(f"Ran {steps_run} steps")
                sim.display(compact=compact_mode)
                
            elif command == 'compact':
                compact_mode = not compact_mode
                print(f"Compact mode: {'ON' if compact_mode else 'OFF'}")
                sim.display(compact=compact_mode)
                
            elif command == 'reset':
                sim = RBefungeSimulator(tape_size=DEFAULT_TAPE_SIZE)
                print("Simulator reset")
                sim.display(compact=compact_mode)
            
            elif command == 'save':
                if len(parts) < 2:
                    print("Usage: save <name>")
                else:
                    filename = parts[1] if parts[1].endswith('.rbf') else parts[1] + '.rbf'
                    try:
                        with open(filename, 'w') as f:
                            f.write(f"# RBefunge state: {filename}\n")
                            f.write(f"# Tape size inferred from tape data\n")
                            f.write(f"ip={sim.ip}\n")
                            f.write(f"dir={sim.dir}\n")
                            f.write(f"h0={sim.h0}\n")
                            f.write(f"h1={sim.h1}\n")
                            f.write(f"step={sim.step_count}\n")
                            tape_str = ','.join(str(v) for v in sim.tape)
                            f.write(f"tape={tape_str}\n")
                        print(f"Saved state to {filename} (tape size: {sim.tape_size})")
                    except Exception as e:
                        print(f"Error saving: {e}")
                
            else:
                print(f"Unknown command: {command}")
                print("Type 'help' for available commands")
                sim.display(compact=compact_mode)
                
        except Exception as e:
            print(f"Error: {e}")
            sim.display(compact=compact_mode)


if __name__ == "__main__":
    interactive_session()
