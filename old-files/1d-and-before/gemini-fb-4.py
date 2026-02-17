#!/usr/bin/env python3
"""
Newtonian Brainf*** Simulator v5.0
Feature: Dual-Head Mirrors (Lock & Key) + Hardware Interlock

State:
  tape[0..N-1] : Bytes (0-255)
  P (IP) : Instruction Pointer + Momentum
  0 (H0) : Primary Data Head
  1 (H1) : Secondary Data Head

Instruction Encoding:
  ( : 244 : H1 Zero Mirror     (Reflect if tape[H1] == 0)
  ) : 245 : H1 Non-Zero Mirror (Reflect if tape[H1] != 0)
  < : 246 : Move H0 Left
  > : 247 : Move H0 Right
  { : 248 : Move H1 Left
  } : 249 : Move H1 Right
  + : 250 : Inc tape[H0]
  - : 251 : Dec tape[H0]
  . : 252 : tape[H1] += tape[H0] (Copy/Lock)
  , : 253 : tape[H0] += tape[H1]
  [ : 254 : H0 Zero Mirror
  ] : 255 : H0 Non-Zero Mirror
"""

import sys

# --- Configuration ---
DEFAULT_TAPE_SIZE = 64

# --- Instruction Encoding ---
OP_MAP = {
    '(': 244, ')': 245,
    '<': 246, '>': 247,
    '{': 248, '}': 249,
    '+': 250, '-': 251,
    '.': 252, ',': 253,
    '[': 254, ']': 255
}
BYTE_TO_CHAR = {v: k for k, v in OP_MAP.items()}

class NewtonianBF:
    def __init__(self, tape_size=DEFAULT_TAPE_SIZE):
        self.tape_size = tape_size
        self.tape = [0] * tape_size
        self.ip = 0
        self.dir = 1
        self.h0 = 0
        self.h1 = 0
        self.step_count = 0

    def load_code(self, code_str):
        self.reset()
        idx = 0
        for char in code_str:
            if idx >= self.tape_size: break
            if char in OP_MAP:
                self.tape[idx] = OP_MAP[char]
                idx += 1
            elif char == '0':
                self.tape[idx] = 0
                idx += 1
            elif char == ' ': continue
            else: pass # Ignore unknown chars
        return idx

    def reset(self):
        self.tape = [0] * self.tape_size
        self.ip = 0
        self.dir = 1
        self.h0 = 0
        self.h1 = 0
        self.step_count = 0

    def step(self):
        val = self.tape[self.ip]
        
        # Execute (with Hardware Interlock)
        if val == OP_MAP['<']: self.h0 = (self.h0 - 1) % self.tape_size
        elif val == OP_MAP['>']: self.h0 = (self.h0 + 1) % self.tape_size
        elif val == OP_MAP['{']: self.h1 = (self.h1 - 1) % self.tape_size
        elif val == OP_MAP['}']: self.h1 = (self.h1 + 1) % self.tape_size
        
        elif val == OP_MAP['+']: 
            if self.h0 != self.ip: self.tape[self.h0] = (self.tape[self.h0] + 1) & 0xFF
        elif val == OP_MAP['-']: 
            if self.h0 != self.ip: self.tape[self.h0] = (self.tape[self.h0] - 1) & 0xFF
        elif val == OP_MAP['.']: 
            if self.h1 != self.ip: self.tape[self.h1] = (self.tape[self.h1] + self.tape[self.h0]) & 0xFF
        elif val == OP_MAP[',']: 
            if self.h0 != self.ip: self.tape[self.h0] = (self.tape[self.h0] + self.tape[self.h1]) & 0xFF
        
        # Mirrors
        elif val == OP_MAP['[']: # H0 Zero
            if self.tape[self.h0] == 0: self.dir *= -1
        elif val == OP_MAP[']']: # H0 Non-Zero
            if self.tape[self.h0] != 0: self.dir *= -1
        elif val == OP_MAP['(']: # H1 Zero
            if self.tape[self.h1] == 0: self.dir *= -1
        elif val == OP_MAP[')']: # H1 Non-Zero
            if self.tape[self.h1] != 0: self.dir *= -1

        self.ip = (self.ip + self.dir) % self.tape_size
        self.step_count += 1
        return True

    def step_back(self):
        prev_ip = (self.ip - self.dir) % self.tape_size
        val = self.tape[prev_ip]
        
        # Check Mirrors (Did we bounce?)
        # Reversibility Logic: If condition met, we MUST have bounced.
        if val == OP_MAP['[']: 
            if self.tape[self.h0] == 0: self.dir *= -1
        elif val == OP_MAP[']']: 
            if self.tape[self.h0] != 0: self.dir *= -1
        elif val == OP_MAP['(']: 
            if self.tape[self.h1] == 0: self.dir *= -1
        elif val == OP_MAP[')']: 
            if self.tape[self.h1] != 0: self.dir *= -1
            
        # Inverse Logic (Inverse Moves & Math)
        elif val == OP_MAP['<']: self.h0 = (self.h0 + 1) % self.tape_size
        elif val == OP_MAP['>']: self.h0 = (self.h0 - 1) % self.tape_size
        elif val == OP_MAP['{']: self.h1 = (self.h1 + 1) % self.tape_size
        elif val == OP_MAP['}']: self.h1 = (self.h1 - 1) % self.tape_size
        
        elif val == OP_MAP['+']: 
            if self.h0 != prev_ip: self.tape[self.h0] = (self.tape[self.h0] - 1) & 0xFF
        elif val == OP_MAP['-']: 
            if self.h0 != prev_ip: self.tape[self.h0] = (self.tape[self.h0] + 1) & 0xFF
        elif val == OP_MAP['.']: 
            if self.h1 != prev_ip: self.tape[self.h1] = (self.tape[self.h1] - self.tape[self.h0]) & 0xFF
        elif val == OP_MAP[',']: 
            if self.h0 != prev_ip: self.tape[self.h0] = (self.tape[self.h0] - self.tape[self.h1]) & 0xFF
            
        self.ip = prev_ip
        self.step_count -= 1
        return True

    def display(self):
        print(f"\nStep: {self.step_count} | IP: {self.ip} {'>>' if self.dir==1 else '<<'}")
        CHUNK = 32
        for start in range(0, self.tape_size, CHUNK):
            end = min(start + CHUNK, self.tape_size)
            lines = ["Adx:", "Val:", "Ins:", "IP :", "Hds:"]
            for i in range(start, end):
                lines[0] += f" {i%100:02d}"
                lines[1] += f" {self.tape[i]:02X}"
                char = BYTE_TO_CHAR.get(self.tape[i], '_')
                if self.tape[i] == 0: char = '0'
                lines[2] += f"  {char}"
                lines[3] += (" P>" if self.dir==1 else " <P") if i==self.ip else "   "
                h = ""
                if i==self.h0: h+="0"
                if i==self.h1: h+="1"
                lines[4] += f" {h:<2}" if h else "   "
            print("-" * ((end-start)*3 + 5))
            for l in lines: print(l)

def main():
    sim = NewtonianBF()
    print("Newtonian BF v5 (Dual-Head Mirrors + Interlock)")
    print("Ops: < > { } + - . , [ ] ( )")
    while True:
        try:
            cmd = input("NBF5> ").strip().split()
        except: break
        if not cmd: continue
        op = cmd[0].lower()
        if op in ['q', 'quit']: break
        elif op == 'tape': sim.load_code("".join(cmd[1:])); sim.display()
        elif op in ['s', 'step']: sim.step(); sim.display()
        elif op in ['b', 'back']: sim.step_back(); sim.display()
        elif op == 'run': 
            for _ in range(int(cmd[1]) if len(cmd)>1 else 100): sim.step()
            sim.display()
        elif op == 'reset': sim.reset(); sim.display()
        elif op == 'data':
            pos = int(cmd[1])
            for v in cmd[2:]: sim.tape[pos%64] = int(v); pos+=1
            sim.display()
        else: print("Unknown")

if __name__ == "__main__":
    main()
