#!/usr/bin/env python3
"""
F***brain Simulator - Reversible Brainf***
Authored or modified by Claude
Version: 2025-01-28 v1.0

F***brain is a reversible computation language using Fredkin-style
conditional swaps. The F is for Fredkin!

State: tape[0..N-1], IP, CL, H0, H1 (all mod N)
  - IP = instruction pointer
  - CL = control locus (condition register for F, swap target for J/S)
  - H0 = data head 0 (accumulator - where +/- act)
  - H1 = data head 1 (auxiliary - swap partner)

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
  F : if tape[CL] != 0: swap(tape[H0], tape[H1])   (self-inverse)
  J : swap(IP, tape[CL])                           (self-inverse)
  S : swap(tape[CL], tape[H0])                     (self-inverse)
  G : swap(CL, tape[H0])                           (self-inverse) [NEW!]

The G instruction enables indirect addressing for CL:
  - Load a computed address into CL by storing it in tape[H0], then G
  - This is essential for loops that need a exteroceptor

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
    'F': 10,
    'J': 11,
    'S': 12,
    'G': 13,  # NEW: swap(CL, tape[H0])
}

OPCODE_TO_CHAR = {v: k for k, v in OPCODES.items()}
OPCODE_TO_CHAR[0] = '0'  # Zero byte displayed as '0'

DEFAULT_TAPE_SIZE = 64  # Larger default for garbage area support


class FBSimulator:
    def __init__(self, tape_size: int = DEFAULT_TAPE_SIZE):
        self.tape_size = tape_size
        self.tape = [0] * tape_size
        self.ip = 0
        self.cl = 0   # Control locus
        self.h0 = 0   # Data head 0
        self.h1 = 0   # Data head 1
        self.step_count = 0
        self.history = []  # For reverse execution
        
    def load_program(self, code: str, data_offset: Optional[int] = None,
                     initial_data: Optional[list] = None):
        """
        Load an F***brain program onto the tape.
        
        code: string of instructions (< > { } ( ) + - x F J S G)
        data_offset: where to place initial data (defaults to after program)
        initial_data: list of byte values to place at data_offset
        """
        # Reset state
        self.tape = [0] * self.tape_size
        self.ip = 0
        self.cl = 0
        self.h0 = 0
        self.h1 = 0
        self.step_count = 0
        self.history = []
        
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
        Execute one instruction.
        Returns True always (no halt - runs forever unless externally stopped).
        """
        # IP wraps modulo tape_size
        self.ip = self.ip % self.tape_size
        
        opcode = self.tape[self.ip]
        
        # Save state for history (enabling reverse execution)
        self.history.append({
            'tape': self.tape.copy(),
            'ip': self.ip,
            'cl': self.cl,
            'h0': self.h0,
            'h1': self.h1,
        })
        
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
            self.tape[self.h0] = (self.tape[self.h0] + 1) & 0xFF
            self.ip = (self.ip + 1) % self.tape_size
            
        elif opcode == OPCODES['-']:
            self.tape[self.h0] = (self.tape[self.h0] - 1) & 0xFF
            self.ip = (self.ip + 1) % self.tape_size
            
        elif opcode == OPCODES['x']:
            # Unconditional swap of tape[H0] and tape[H1]
            tmp = self.tape[self.h0]
            self.tape[self.h0] = self.tape[self.h1]
            self.tape[self.h1] = tmp
            self.ip = (self.ip + 1) % self.tape_size
            
        elif opcode == OPCODES['F']:
            # Fredkin: if tape[CL] != 0, swap tape[H0] and tape[H1]
            if self.tape[self.cl] != 0:
                tmp = self.tape[self.h0]
                self.tape[self.h0] = self.tape[self.h1]
                self.tape[self.h1] = tmp
            self.ip = (self.ip + 1) % self.tape_size
            
        elif opcode == OPCODES['J']:
            # Jump: swap IP and tape[CL]
            tmp = self.ip
            self.ip = self.tape[self.cl] % self.tape_size
            self.tape[self.cl] = tmp
            # Note: IP not incremented here - we jumped!
            
        elif opcode == OPCODES['S']:
            # Bridge: swap tape[CL] and tape[H0]
            tmp = self.tape[self.cl]
            self.tape[self.cl] = self.tape[self.h0]
            self.tape[self.h0] = tmp
            self.ip = (self.ip + 1) % self.tape_size
        
        elif opcode == OPCODES['G']:
            # G: swap CL (the register) with tape[H0] (a memory cell)
            # This enables indirect addressing for CL
            tmp = self.cl
            self.cl = self.tape[self.h0] % self.tape_size
            self.tape[self.h0] = tmp
            self.ip = (self.ip + 1) % self.tape_size
            
        else:
            # Unknown opcode - NOP, just advance IP
            self.ip = (self.ip + 1) % self.tape_size
            
        self.step_count += 1
        return True
    
    def step_back(self) -> bool:
        """
        Reverse one instruction by restoring from history.
        Returns True if successful, False if no history.
        """
        if not self.history:
            return False
        
        state = self.history.pop()
        self.tape = state['tape']
        self.ip = state['ip']
        self.cl = state['cl']
        self.h0 = state['h0']
        self.h1 = state['h1']
        self.step_count -= 1
        return True
    
    def display(self, compact: bool = False):
        """
        Display the current state of the simulator.
        """
        print(f"\n=== Step {self.step_count} ===")
        print(f"IP={self.ip}, CL={self.cl}, H0={self.h0}, H1={self.h1}")
        
        if compact:
            self._display_compact(0, self.tape_size)
        else:
            self._display_full(0, self.tape_size)
    
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
        
        # Control markers line (IP and CL)
        ctrl_line = "      "
        for i in range(start, end):
            markers = []
            if i == self.ip:
                markers.append("IP")
            if i == self.cl:
                markers.append("CL")
            
            if markers:
                mark_str = ",".join(markers)
                ctrl_line += f"{mark_str:>4s} "
            else:
                ctrl_line += "     "
        print(ctrl_line)
        
        # Data markers line (H0 and H1)
        data_line = "      "
        for i in range(start, end):
            markers = []
            if i == self.h0:
                markers.append("H0")
            if i == self.h1:
                markers.append("H1")
            
            if markers:
                mark_str = ",".join(markers)
                data_line += f"{mark_str:>4s} "
            else:
                data_line += "     "
        print(data_line)
    
    def _display_compact(self, start: int, end: int):
        """Single-line compact display."""
        line = ""
        for i in range(start, end):
            char = OPCODE_TO_CHAR.get(self.tape[i], '?')
            
            # Build marker prefix
            markers = ""
            if i == self.ip:
                markers += "→"
            if i == self.cl:
                markers += "C"
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
    """Run an interactive F***brain session."""
    sim = FBSimulator(tape_size=DEFAULT_TAPE_SIZE)
    
    print("=" * 70)
    print("F***brain Simulator - Reversible Brainf*** - Interactive Mode")
    print("=" * 70)
    print(f"""
Tape size: {DEFAULT_TAPE_SIZE} (all addresses wrap mod {DEFAULT_TAPE_SIZE})

State: tape[0..{DEFAULT_TAPE_SIZE-1}], IP, CL, H0, H1

ISA:
  <  CL--          >  CL++          {{  H0--          }}  H0++
  (  H1--          )  H1++          +  tape[H0]++     -  tape[H0]--
  x  swap tape[H0],tape[H1]         F  if tape[CL]: swap tape[H0],tape[H1]
  J  swap IP,tape[CL]               S  swap tape[CL],tape[H0]
  G  swap CL,tape[H0]  [indirect CL addressing]

Commands:
  tape <code>           Load program onto tape (e.g., tape x}}))F>J)
  load <file>           Load saved state from file (adds .fb)
  save <name>           Save state to file (adds .fb extension)
  data <pos> <val>...   Set tape values at position
  cl/h0/h1 <pos>        Set pointer position
  step / s              Execute one instruction
  back / b              Reverse one instruction
  run [n]               Run n steps (default: until 1000)
  compact               Toggle compact display mode
  reset                 Reset simulator
  help                  Show this help
  quit / q              Exit

Example:
  tape >}}+
  step
  save mystate
  reset
  load mystate
""")
    
    compact_mode = False
    
    # Show initial state
    sim.display(compact=compact_mode)
    
    while True:
        try:
            cmd = input("\nFB> ").strip()
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
                print("Commands: tape, load, save, data, cl, h0, h1, step, back, run, compact, reset, quit")
                sim.display(compact=compact_mode)
                
            elif command == 'tape':
                if len(parts) < 2:
                    print("Usage: tape <code>  (e.g., tape x}}))F>J)")
                    sim.display(compact=compact_mode)
                    continue
                code = ' '.join(parts[1:])
                prog_len = sim.load_program(code)
                print(f"Loaded {prog_len} instructions onto tape")
                sim.display(compact=compact_mode)
            
            elif command == 'load':
                if len(parts) < 2:
                    print("Usage: load <file>  (loads state from file.fb)")
                    sim.display(compact=compact_mode)
                    continue
                arg = parts[1]
                filename = arg if arg.endswith('.fb') else arg + '.fb'
                import os
                if os.path.exists(filename):
                    try:
                        with open(filename, 'r') as f:
                            for line in f:
                                line = line.strip()
                                if line.startswith('#') or not line:
                                    continue
                                if '=' in line:
                                    key, val = line.split('=', 1)
                                    if key == 'tape_size':
                                        new_size = int(val)
                                        if new_size != sim.tape_size:
                                            sim = FBSimulator(tape_size=new_size)
                                    elif key == 'ip':
                                        sim.ip = int(val)
                                    elif key == 'cl':
                                        sim.cl = int(val)
                                    elif key == 'h0':
                                        sim.h0 = int(val)
                                    elif key == 'h1':
                                        sim.h1 = int(val)
                                    elif key == 'step':
                                        sim.step_count = int(val)
                                    elif key == 'tape':
                                        values = [int(v) for v in val.split(',')]
                                        for i, v in enumerate(values):
                                            if i < sim.tape_size:
                                                sim.tape[i] = v
                        print(f"Loaded state from {filename}")
                        sim.display(compact=compact_mode)
                    except Exception as e:
                        print(f"Error loading: {e}")
                else:
                    print(f"File not found: {filename}")
                    print("(Use 'tape <code>' to load a program onto tape)")
                
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
                
            elif command == 'cl':
                if len(parts) < 2:
                    print("Usage: cl <position>")
                    sim.display(compact=compact_mode)
                    continue
                sim.cl = int(parts[1]) % sim.tape_size
                print(f"CL = {sim.cl}")
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
                
            elif command in ('step', 's'):
                sim.step()
                sim.display(compact=compact_mode)
                
            elif command in ('back', 'b'):
                if sim.step_back():
                    print("Reversed one step")
                else:
                    print("No history to reverse")
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
                sim = FBSimulator(tape_size=DEFAULT_TAPE_SIZE)
                print("Simulator reset")
                sim.display(compact=compact_mode)
            
            elif command == 'save':
                if len(parts) < 2:
                    print("Usage: save <name>")
                else:
                    filename = parts[1] if parts[1].endswith('.fb') else parts[1] + '.fb'
                    try:
                        with open(filename, 'w') as f:
                            f.write(f"# F***brain state: {filename}\n")
                            f.write(f"tape_size={sim.tape_size}\n")
                            f.write(f"ip={sim.ip}\n")
                            f.write(f"cl={sim.cl}\n")
                            f.write(f"h0={sim.h0}\n")
                            f.write(f"h1={sim.h1}\n")
                            f.write(f"step={sim.step_count}\n")
                            # Save tape as comma-separated values
                            tape_str = ','.join(str(v) for v in sim.tape)
                            f.write(f"tape={tape_str}\n")
                        print(f"Saved state to {filename}")
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
