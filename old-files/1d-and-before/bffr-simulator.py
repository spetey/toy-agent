#!/usr/bin/env python3
"""
BFFR Simulator - A reversible variant of BFF (Brainfuck-F)
Authored or modified by Claude
Version: 2025-01-27 v1.1

BFFR is BFF with the copy operations (. and ,) replaced by a single swap (x).

ISA:
  < : head0 = head0 - 1
  > : head0 = head0 + 1
  { : head1 = head1 - 1
  } : head1 = head1 + 1
  - : tape[head0] = tape[head0] - 1
  + : tape[head0] = tape[head0] + 1
  x : swap(tape[head0], tape[head1])
  [ : if tape[head0] == 0, jump forward to matching ]
  ] : if tape[head0] != 0, jump backward to matching [

The tape IS the program - instructions are encoded as byte values.
Tape size is 16; all head positions wrap modulo 16.
"""

import sys
from typing import Optional

# Instruction encoding
OPCODES = {
    '<': 1,
    '>': 2,
    '{': 3,
    '}': 4,
    '+': 5,
    '-': 6,
    'x': 7,
    '[': 8,
    ']': 9,
}

OPCODE_TO_CHAR = {v: k for k, v in OPCODES.items()}
OPCODE_TO_CHAR[0] = '0'  # Zero byte displayed as '0' (used for conditional tests)


class BFFRSimulator:
    def __init__(self, tape_size: int = 16):
        self.tape_size = tape_size
        self.tape = [0] * tape_size
        self.ip = 0
        self.head0 = 0
        self.head1 = 0
        self.step_count = 0
        self.history = []  # For potential future "reverse" feature
        
    def load_program(self, code: str, data_offset: Optional[int] = None, 
                     initial_data: Optional[list] = None):
        """
        Load a BFFR program onto the tape.
        
        code: string of BFFR instructions (< > { } + - x [ ])
        data_offset: where to place initial data (defaults to after program)
        initial_data: list of byte values to place at data_offset
        """
        # Reset state
        self.tape = [0] * self.tape_size
        self.ip = 0
        self.head0 = 0
        self.head1 = 0
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
                self.tape[offset + i] = val & 0xFF
                
        return program_len
    
    def find_matching_bracket(self, pos: int, forward: bool) -> int:
        """Find the matching bracket for the one at pos."""
        depth = 1
        direction = 1 if forward else -1
        current = pos + direction
        
        while 0 <= current < self.tape_size and depth > 0:
            if self.tape[current] == OPCODES['[']:
                depth += (1 if forward else -1)
            elif self.tape[current] == OPCODES[']']:
                depth += (-1 if forward else 1)
            if depth == 0:
                return current
            current += direction
            
        raise RuntimeError(f"Unmatched bracket at position {pos}")
    
    def step(self) -> bool:
        """
        Execute one instruction.
        Returns True always (no halt condition - runs forever unless externally stopped).
        """
        # IP wraps modulo tape_size
        self.ip = self.ip % self.tape_size
            
        opcode = self.tape[self.ip]
        
        # Save state for history
        self.history.append({
            'tape': self.tape.copy(),
            'ip': self.ip,
            'head0': self.head0,
            'head1': self.head1,
        })
        
        if opcode == OPCODES['<']:
            self.head0 = (self.head0 - 1) % self.tape_size
            self.ip = (self.ip + 1) % self.tape_size
            
        elif opcode == OPCODES['>']:
            self.head0 = (self.head0 + 1) % self.tape_size
            self.ip = (self.ip + 1) % self.tape_size
            
        elif opcode == OPCODES['{']:
            self.head1 = (self.head1 - 1) % self.tape_size
            self.ip = (self.ip + 1) % self.tape_size
            
        elif opcode == OPCODES['}']:
            self.head1 = (self.head1 + 1) % self.tape_size
            self.ip = (self.ip + 1) % self.tape_size
            
        elif opcode == OPCODES['+']:
            self.tape[self.head0] = (self.tape[self.head0] + 1) & 0xFF
            self.ip = (self.ip + 1) % self.tape_size
            
        elif opcode == OPCODES['-']:
            self.tape[self.head0] = (self.tape[self.head0] - 1) & 0xFF
            self.ip = (self.ip + 1) % self.tape_size
            
        elif opcode == OPCODES['x']:
            # Swap tape[head0] and tape[head1]
            tmp = self.tape[self.head0]
            self.tape[self.head0] = self.tape[self.head1]
            self.tape[self.head1] = tmp
            self.ip = (self.ip + 1) % self.tape_size
            
        elif opcode == OPCODES['[']:
            if self.tape[self.head0] == 0:
                self.ip = (self.find_matching_bracket(self.ip, forward=True) + 1) % self.tape_size
            else:
                self.ip = (self.ip + 1) % self.tape_size
                
        elif opcode == OPCODES[']']:
            if self.tape[self.head0] != 0:
                self.ip = (self.find_matching_bracket(self.ip, forward=False) + 1) % self.tape_size
            else:
                self.ip = (self.ip + 1) % self.tape_size
                
        else:
            # Unknown opcode - NOP, just advance IP
            self.ip = (self.ip + 1) % self.tape_size
            
        self.step_count += 1
        return True
    
    def display(self, compact: bool = False):
        """
        Display the current state of the simulator.
        Always shows full tape (all 16 positions).
        """
        print(f"\n=== Step {self.step_count} ===")
        print(f"IP={self.ip}, head0={self.head0}, head1={self.head1}")
        
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
        
        # Markers line
        mark_line = "      "
        for i in range(start, end):
            markers = []
            if i == self.ip:
                markers.append("IP")
            if i == self.head0:
                markers.append("h0")
            if i == self.head1:
                markers.append("h1")
            
            if markers:
                mark_str = ",".join(markers)
                mark_line += f"{mark_str:>4s} "
            else:
                mark_line += "     "
        print(mark_line)
    
    def _display_compact(self, start: int, end: int):
        """Single-line compact display."""
        line = ""
        for i in range(start, end):
            char = OPCODE_TO_CHAR.get(self.tape[i], '?')
            
            # Build marker prefix
            prefix = ""
            if i == self.ip:
                prefix += "→"
            if i == self.head0:
                prefix += "⁰"
            if i == self.head1:
                prefix += "¹"
            
            if prefix:
                line += f"[{prefix}{char}]"
            else:
                line += f" {char} "
        print(line)


def interactive_session():
    """Run an interactive BFFR session."""
    sim = BFFRSimulator(tape_size=16)
    
    print("=" * 60)
    print("BFFR Simulator - Interactive Mode")
    print("=" * 60)
    print("""
Commands:
  load <code>           Load BFFR program (e.g., "load [-]")
  data <pos> <val>...   Set tape values at position
  head0 <pos>           Set head0 position  
  head1 <pos>           Set head1 position
  step / s              Execute one instruction
  run [n]               Run n steps (default: until halt, max 1000)
  compact               Toggle compact display mode
  reset                 Reset simulator
  help                  Show this help
  quit / q              Exit

Tape size is 16, all positions wrap modulo 16.

Example session:
  load [-]
  data 3 2
  head0 3
  step
""")
    
    compact_mode = False
    
    # Show initial state
    sim.display(compact=compact_mode)
    
    while True:
        try:
            cmd = input("\nBFFR> ").strip()
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
                print("Commands: load, data, head0, head1, step, run, compact, reset, quit")
                sim.display(compact=compact_mode)
                
            elif command == 'load':
                if len(parts) < 2:
                    print("Usage: load <code>")
                    sim.display(compact=compact_mode)
                    continue
                code = ' '.join(parts[1:])
                prog_len = sim.load_program(code)
                print(f"Loaded {prog_len} instructions")
                sim.display(compact=compact_mode)
                
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
                
            elif command == 'head0':
                if len(parts) < 2:
                    print("Usage: head0 <position>")
                    sim.display(compact=compact_mode)
                    continue
                sim.head0 = int(parts[1]) % sim.tape_size
                print(f"head0 = {sim.head0}")
                sim.display(compact=compact_mode)
                
            elif command == 'head1':
                if len(parts) < 2:
                    print("Usage: head1 <position>")
                    sim.display(compact=compact_mode)
                    continue
                sim.head1 = int(parts[1]) % sim.tape_size
                print(f"head1 = {sim.head1}")
                sim.display(compact=compact_mode)
                
            elif command in ('step', 's'):
                sim.step()
                sim.display(compact=compact_mode)
                    
            elif command == 'run':
                max_steps = 1000
                if len(parts) > 1:
                    max_steps = int(parts[1])
                steps_run = 0
                while steps_run < max_steps and sim.step():
                    steps_run += 1
                print(f"Ran {steps_run} steps")
                sim.display(compact=compact_mode)
                
            elif command == 'compact':
                compact_mode = not compact_mode
                print(f"Compact mode: {'ON' if compact_mode else 'OFF'}")
                sim.display(compact=compact_mode)
                
            elif command == 'reset':
                sim = BFFRSimulator(tape_size=16)
                print("Simulator reset")
                sim.display(compact=compact_mode)
                
            else:
                print(f"Unknown command: {command}")
                print("Type 'help' for available commands")
                sim.display(compact=compact_mode)
                
        except Exception as e:
            print(f"Error: {e}")
            sim.display(compact=compact_mode)


def demo():
    """Run a demonstration of the irreversibility counterexample."""
    print("=" * 60)
    print("BFFR Irreversibility Demonstration")
    print("=" * 60)
    print("""
We'll show that the program [-] (decrement until zero) is irreversible
by demonstrating two different initial states that reach the same final state.

Program: [-]  (encoded as bytes [8, 6, 9] at positions 0-2)
head0 points to position 3 (the data cell)
""")
    
    for initial_val in [1, 2]:
        print(f"\n{'='*40}")
        print(f"Starting with tape[3] = {initial_val}")
        print('='*40)
        
        sim = BFFRSimulator(tape_size=16)
        sim.load_program("[-]", data_offset=3, initial_data=[initial_val])
        sim.head0 = 3  # Point to data cell
        
        sim.display()
        
        step = 0
        while sim.step() and step < 20:
            step += 1
            sim.display()
        
        print(f"\nFinal state: tape[3] = {sim.tape[3]}")
    
    print("\n" + "="*60)
    print("CONCLUSION: Both initial states (tape[3]=1 and tape[3]=2)")
    print("reached the SAME final state (tape[3]=0).")
    print("This proves BFFR is NOT reversible.")
    print("="*60)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--demo":
        demo()
    else:
        interactive_session()
