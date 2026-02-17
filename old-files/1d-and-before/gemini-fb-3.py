#!/usr/bin/env python3
"""
Newtonian Brainf*** Simulator v3.0
"High-Byte ISA" & Compact Hex Display

State:
  tape[0..N-1] : Bytes (0-255)
  P (IP) : Instruction Pointer + Direction (Momentum)
  0 (H0) : Primary Data Head
  1 (H1) : Secondary Data Head

Instruction Encoding (The "High 10"):
  246: <   247: >   248: {   249: }
  250: +   251: -   252: .   253: ,
  254: [   255: ]
  0 is explicitly the Null/Zero byte.
"""

import sys
import os

# --- Configuration ---
DEFAULT_TAPE_SIZE = 64

# --- Instruction Encoding (High Byte Mapping) ---
# We map the characters to the top of the byte range (246-255)
OP_MAP = {
    '<': 246, '>': 247,
    '{': 248, '}': 249,
    '+': 250, '-': 251,
    '.': 252, ',': 253,
    '[': 254, ']': 255
}

# Reverse mapping for display
BYTE_TO_CHAR = {v: k for k, v in OP_MAP.items()}

class NewtonianBF:
    def __init__(self, tape_size=DEFAULT_TAPE_SIZE):
        self.tape_size = tape_size
        self.tape = [0] * tape_size
        self.ip = 0
        self.dir = 1  # +1 or -1
        self.h0 = 0
        self.h1 = 0
        self.step_count = 0

    def load_code(self, code_str):
        self.reset()
        # Clean string to just valid ops or data
        # We place opcodes as high bytes, '0' as 0
        idx = 0
        for char in code_str:
            if idx >= self.tape_size: break
            
            if char in OP_MAP:
                self.tape[idx] = OP_MAP[char]
                idx += 1
            elif char == '0':
                self.tape[idx] = 0
                idx += 1
            elif char == ' ':
                continue # Skip spaces in input
            else:
                # Optional: Treat other chars as raw ASCII data?
                # For now, let's ignore or treat as 0
                pass
        return idx

    def reset(self):
        self.tape = [0] * self.tape_size
        self.ip = 0
        self.dir = 1
        self.h0 = 0
        self.h1 = 0
        self.step_count = 0

    def step(self):
        # 1. Fetch
        val = self.tape[self.ip]
        
        # 2. Execute (Only if val is an instruction)
        # Note: Bounces happen even if we are "moving backwards" 
        # because the physics is time-symmetric.
        
        if val == OP_MAP['<']: self.h0 = (self.h0 - 1) % self.tape_size
        elif val == OP_MAP['>']: self.h0 = (self.h0 + 1) % self.tape_size
        elif val == OP_MAP['{']: self.h1 = (self.h1 - 1) % self.tape_size
        elif val == OP_MAP['}']: self.h1 = (self.h1 + 1) % self.tape_size
        elif val == OP_MAP['+']: self.tape[self.h0] = (self.tape[self.h0] + 1) & 0xFF
        elif val == OP_MAP['-']: self.tape[self.h0] = (self.tape[self.h0] - 1) & 0xFF
        elif val == OP_MAP['.']: self.tape[self.h1] = (self.tape[self.h1] + self.tape[self.h0]) & 0xFF
        elif val == OP_MAP[',']: self.tape[self.h0] = (self.tape[self.h0] + self.tape[self.h1]) & 0xFF
        
        elif val == OP_MAP['[']: # Zero Mirror
            if self.tape[self.h0] == 0: self.dir *= -1
        elif val == OP_MAP[']']: # Non-Zero Mirror
            if self.tape[self.h0] != 0: self.dir *= -1

        # 3. Move
        self.ip = (self.ip + self.dir) % self.tape_size
        self.step_count += 1
        return True

    def step_back(self):
        # 1. UnMove
        prev_ip = (self.ip - self.dir) % self.tape_size
        
        # 2. UnExecute (Logic is identical to forward for mirrors)
        val = self.tape[prev_ip]
        
        if val == OP_MAP['[']: 
            if self.tape[self.h0] == 0: self.dir *= -1
        elif val == OP_MAP[']']: 
            if self.tape[self.h0] != 0: self.dir *= -1
            
        elif val == OP_MAP['<']: self.h0 = (self.h0 + 1) % self.tape_size
        elif val == OP_MAP['>']: self.h0 = (self.h0 - 1) % self.tape_size
        elif val == OP_MAP['{']: self.h1 = (self.h1 + 1) % self.tape_size
        elif val == OP_MAP['}']: self.h1 = (self.h1 - 1) % self.tape_size
        elif val == OP_MAP['+']: self.tape[self.h0] = (self.tape[self.h0] - 1) & 0xFF
        elif val == OP_MAP['-']: self.tape[self.h0] = (self.tape[self.h0] + 1) & 0xFF
        elif val == OP_MAP['.']: self.tape[self.h1] = (self.tape[self.h1] - self.tape[self.h0]) & 0xFF
        elif val == OP_MAP[',']: self.tape[self.h0] = (self.tape[self.h0] - self.tape[self.h1]) & 0xFF
            
        self.ip = prev_ip
        self.step_count -= 1
        return True

    # --- COMPACT DISPLAY ENGINE ---
    def display(self):
        print(f"\nStep: {self.step_count} | IP: {self.ip} {'>>' if self.dir==1 else '<<'}")
        
        # Split into rows of 32 for readability
        CHUNK = 32
        for start in range(0, self.tape_size, CHUNK):
            end = min(start + CHUNK, self.tape_size)
            self._print_chunk(start, end)

    def _print_chunk(self, start, end):
        # We need 5 lines
        # Adx: Address (2 chars)
        # Val: Value (2 chars Hex)
        # Ins: Instruction char
        # IP : P> or <P
        # Hds: 0, 1, or 01
        
        lines = ["Adx:", "Val:", "Ins:", "IP :", "Hds:"]
        
        for i in range(start, end):
            # Adx: Wrap 00-99
            lines[0] += f" {i%100:02d}"
            
            # Val: Hex
            lines[1] += f" {self.tape[i]:02X}"
            
            # Ins: Char or _
            char = BYTE_TO_CHAR.get(self.tape[i], '_')
            if self.tape[i] == 0: char = '0'
            lines[2] += f"  {char}"
            
            # IP: P> or <P
            ip_str = "   "
            if i == self.ip:
                ip_str = " P>" if self.dir == 1 else " <P"
            lines[3] += ip_str
            
            # Hds: 0, 1, or 01
            hd_str = ""
            if i == self.h0: hd_str += "0"
            if i == self.h1: hd_str += "1"
            
            if len(hd_str) == 0: lines[4] += "   "
            elif len(hd_str) == 1: lines[4] += f"  {hd_str}"
            else: lines[4] += f" {hd_str}" # " 01"
            
        # Print the block
        print("-" * ((end-start)*3 + 5))
        for line in lines:
            print(line)

# --- CLI ---
def main():
    sim = NewtonianBF()
    print("Newtonian BF v3 (High-Byte ISA)")
    print("Commands: tape <code>, step, back, run [n], reset, data <pos> <val>...")
    
    while True:
        try:
            cmd = input("NBF> ").strip().split()
        except: break
        if not cmd: continue
        op = cmd[0].lower()
        
        if op in ['q', 'quit', 'exit']: break
        
        elif op == 'tape':
            # Load code string
            code = "".join(cmd[1:])
            sim.load_code(code)
            sim.display()
            
        elif op in ['s', 'step']:
            sim.step()
            sim.display()
            
        elif op in ['b', 'back']:
            sim.step_back()
            sim.display()
            
        elif op == 'run':
            n = 100
            if len(cmd) > 1: n = int(cmd[1])
            for _ in range(n): sim.step()
            sim.display()
            
        elif op == 'reset':
            sim.reset()
            sim.display()
            
        elif op == 'data':
            # Inject raw values (decimal) at position
            if len(cmd) > 2:
                pos = int(cmd[1])
                for v in cmd[2:]:
                    sim.tape[pos % sim.tape_size] = int(v) & 0xFF
                    pos += 1
                sim.display()
                
        else:
            print("Unknown command")

if __name__ == "__main__":
    main()
