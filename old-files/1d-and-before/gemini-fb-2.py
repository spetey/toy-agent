#!/usr/bin/env python3
"""
Newtonian Brainf*** Simulator (Ricochet BF) - v2.0
Concept by User & Gemini | Display refined for maximum readability

State: 
  tape[0..N-1] : The universe (Program + Data)
  P (IP) : Instruction Pointer (Has Momentum/Direction!)
  0 (H0) : Primary Data Head
  1 (H1) : Secondary Data Head

ISA (The "Physics"):
  < > : Move H0 Left/Right
  { } : Move H1 Left/Right
  + - : Inc/Dec tape[H0]
  .   : tape[H1] += tape[H0]  (Accumulate/Copy)
  ,   : tape[H0] += tape[H1]  (Accumulate/Copy)
  [   : Zero Mirror     (if tape[H0] == 0: Dir *= -1)
  ]   : Non-Zero Mirror (if tape[H0] != 0: Dir *= -1)
  _   : NOP (Pass through)
"""

import sys
import os

# --- Configuration ---
DEFAULT_TAPE_SIZE = 64

# --- Instruction Encoding ---
OPCODES = {
    '<': ord('<'), '>': ord('>'),
    '{': ord('{'), '}': ord('}'),
    '+': ord('+'), '-': ord('-'),
    '.': ord('.'), ',': ord(','),
    '[': ord('['), ']': ord(']'),
    '_': ord('_')
}
BYTE_TO_CHAR = {v: k for k, v in OPCODES.items()}

class NewtonianBF:
    def __init__(self, tape_size=DEFAULT_TAPE_SIZE):
        self.tape_size = tape_size
        self.tape = [0] * tape_size
        
        # The Physics State
        self.ip = 0
        self.dir = 1  # +1 (Right) or -1 (Left)
        
        # The Heads
        self.h0 = 0
        self.h1 = 0
        
        # Meta
        self.step_count = 0

    def load_code(self, code_str):
        self.reset()
        for i, char in enumerate(code_str):
            if i >= self.tape_size: break
            if char in OPCODES:
                self.tape[i] = OPCODES[char]
            elif char == '0':
                self.tape[i] = 0
            else:
                self.tape[i] = ord(char)

    def reset(self):
        self.tape = [0] * self.tape_size
        self.ip = 0
        self.dir = 1
        self.h0 = 0
        self.h1 = 0
        self.step_count = 0

    def step(self):
        op = self.tape[self.ip]
        
        # Execute Actions (Time Symmetric)
        if op == OPCODES['<']: self.h0 = (self.h0 - 1) % self.tape_size
        elif op == OPCODES['>']: self.h0 = (self.h0 + 1) % self.tape_size
        elif op == OPCODES['{']: self.h1 = (self.h1 - 1) % self.tape_size
        elif op == OPCODES['}']: self.h1 = (self.h1 + 1) % self.tape_size
        elif op == OPCODES['+']: self.tape[self.h0] = (self.tape[self.h0] + 1) & 0xFF
        elif op == OPCODES['-']: self.tape[self.h0] = (self.tape[self.h0] - 1) & 0xFF
        elif op == OPCODES['.']: self.tape[self.h1] = (self.tape[self.h1] + self.tape[self.h0]) & 0xFF
        elif op == OPCODES[',']: self.tape[self.h0] = (self.tape[self.h0] + self.tape[self.h1]) & 0xFF
        elif op == OPCODES['[']: 
            if self.tape[self.h0] == 0: self.dir *= -1
        elif op == OPCODES[']']: 
            if self.tape[self.h0] != 0: self.dir *= -1
            
        # Move (Apply Momentum)
        self.ip = (self.ip + self.dir) % self.tape_size
        self.step_count += 1
        return True

    def step_back(self):
        # 1. UnMove
        prev_ip = (self.ip - self.dir) % self.tape_size
        
        # 2. UnExecute
        op = self.tape[prev_ip]
        
        # Check mirrors for bounce logic
        if op == OPCODES['[']: 
            if self.tape[self.h0] == 0: self.dir *= -1
        elif op == OPCODES[']']: 
            if self.tape[self.h0] != 0: self.dir *= -1
                
        # Inverse Data Ops
        elif op == OPCODES['<']: self.h0 = (self.h0 + 1) % self.tape_size
        elif op == OPCODES['>']: self.h0 = (self.h0 - 1) % self.tape_size
        elif op == OPCODES['{']: self.h1 = (self.h1 + 1) % self.tape_size
        elif op == OPCODES['}']: self.h1 = (self.h1 - 1) % self.tape_size
        elif op == OPCODES['+']: self.tape[self.h0] = (self.tape[self.h0] - 1) & 0xFF
        elif op == OPCODES['-']: self.tape[self.h0] = (self.tape[self.h0] + 1) & 0xFF
        elif op == OPCODES['.']: self.tape[self.h1] = (self.tape[self.h1] - self.tape[self.h0]) & 0xFF
        elif op == OPCODES[',']: self.tape[self.h0] = (self.tape[self.h0] - self.tape[self.h1]) & 0xFF
            
        self.ip = prev_ip
        self.step_count -= 1
        return True

    def save_state(self, filename):
        with open(filename, 'w') as f:
            f.write(f"tape_size={self.tape_size}\n")
            f.write(f"ip={self.ip}\n")
            f.write(f"dir={self.dir}\n")
            f.write(f"h0={self.h0}\n")
            f.write(f"h1={self.h1}\n")
            f.write(f"step={self.step_count}\n")
            f.write(f"tape={','.join(map(str, self.tape))}\n")
            
    def load_state(self, filename):
        if not os.path.exists(filename): return False
        with open(filename, 'r') as f:
            for line in f:
                k, v = line.strip().split('=')
                if k == 'tape_size': 
                    self.tape_size = int(v)
                    self.tape = [0]*self.tape_size 
                elif k == 'ip': self.ip = int(v)
                elif k == 'dir': self.dir = int(v)
                elif k == 'h0': self.h0 = int(v)
                elif k == 'h1': self.h1 = int(v)
                elif k == 'step': self.step_count = int(v)
                elif k == 'tape': self.tape = [int(x) for x in v.split(',')]
        return True

    # --- NEW DISPLAY ENGINE ---
    def display(self, compact=False):
        print(f"\nStep: {self.step_count} | Momentum: {'>>' if self.dir==1 else '<<'}")
        
        # 32 columns per row is a good standard for terminal width
        CHUNK = 32
        
        for start_idx in range(0, self.tape_size, CHUNK):
            end_idx = min(start_idx + CHUNK, self.tape_size)
            self._print_row(start_idx, end_idx)

    def _print_row(self, start, end):
        # We build 4 lines: Address, Value, Instruction, Pointers
        line_addr = "Addr: "
        line_val  = "Val : "
        line_ins  = "Ins : "
        line_ptr  = "Ptr : "
        
        for i in range(start, end):
            # 1. Address
            line_addr += f"{i:<4}"
            
            # 2. Value
            line_val += f"{self.tape[i]:<4}"
            
            # 3. Instruction
            char = BYTE_TO_CHAR.get(self.tape[i], '.')
            if self.tape[i] == 0: char = '0'
            line_ins += f" {char:<3}"
            
            # 4. Pointers (P, 0, 1)
            # We overlap them if they are on the same cell
            ptrs = ""
            if i == self.ip: 
                # Add direction arrow to P
                ptrs += "P>" if self.dir == 1 else "<P"
            if i == self.h0: ptrs += "0"
            if i == self.h1: ptrs += "1"
            
            # Pad or trim pointer string to fit column width of 4
            if len(ptrs) > 3: 
                # If very crowded (e.g. <P01), just show it tight
                line_ptr += f"{ptrs[:4]}" 
            else:
                line_ptr += f"{ptrs:<4}"

        print("-" * 80)
        print(line_addr)
        print(line_val)
        print(line_ins)
        print(line_ptr)

# --- Interactive CLI ---
def main():
    sim = NewtonianBF()
    print("Newtonian BF Simulator v2.0")
    print("Isa: < > { } + - . , [ ]")
    
    compact = False
    
    while True:
        try:
            cmd = input("NBF> ").strip().split()
        except: break
        if not cmd: continue
        
        op = cmd[0].lower()
        
        if op in ['quit', 'exit', 'q']: break
        elif op == 'tape':
            code = " ".join(cmd[1:])
            sim.load_code(code)
            sim.display()
        elif op in ['step', 's']:
            sim.step()
            sim.display()
        elif op in ['back', 'b', 'r']:
            sim.step_back()
            sim.display()
        elif op == 'run':
            n = int(cmd[1]) if len(cmd) > 1 else 100
            for _ in range(n): sim.step()
            sim.display()
        elif op == 'save':
            fn = cmd[1] if len(cmd)>1 else "state.bf"
            sim.save_state(fn)
            print(f"Saved to {fn}")
        elif op == 'load':
            fn = cmd[1] if len(cmd)>1 else "state.bf"
            if sim.load_state(fn): print(f"Loaded {fn}")
            else: print("File not found")
            sim.display()
        elif op == 'reset':
            sim.reset()
            sim.display()
        elif op == 'data': 
            pos = int(cmd[1])
            for i, val in enumerate(cmd[2:]):
                sim.tape[(pos+i)%sim.tape_size] = int(val)
            sim.display()
        else:
            print("Unknown command.")

if __name__ == "__main__":
    main()
