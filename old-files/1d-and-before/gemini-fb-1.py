#!/usr/bin/env python3
"""
Newtonian Brainf*** Simulator (Ricochet BF)
Concept by User & Gemini | Coded by Gemini
Version: 2026-01-28 v1.0

A reversible, 1D "Momentum-based" Turing Machine.
The instruction pointer (IP) has velocity (Direction).
Loops are formed by bouncing between "Mirrors".

State: 
  tape[0..N-1] : The universe (Program + Data)
  IP  : Instruction Pointer location
  Dir : Direction (+1 or -1)
  H0  : Primary Data Head
  H1  : Secondary Data Head

ISA (The "Physics"):
  < > : Move H0 Left/Right
  { } : Move H1 Left/Right
  + - : Inc/Dec tape[H0]
  .   : tape[H1] += tape[H0]  (Accumulate/Copy)
  ,   : tape[H0] += tape[H1]  (Accumulate/Copy)
  [   : Zero Mirror     (if tape[H0] == 0: Dir *= -1)
  ]   : Non-Zero Mirror (if tape[H0] != 0: Dir *= -1)
  _   : NOP (Pass through)
  
  (All other bytes are treated as NOPs/Data)
"""

import sys
import os

# --- Configuration ---
DEFAULT_TAPE_SIZE = 64

# --- Instruction Encoding ---
# We map characters to byte values. 
# You can put these ASCII chars directly on the tape.
OPCODES = {
    '<': ord('<'), '>': ord('>'),
    '{': ord('{'), '}': ord('}'),
    '+': ord('+'), '-': ord('-'),
    '.': ord('.'), ',': ord(','),
    '[': ord('['), ']': ord(']'),
    '_': ord('_')
}

# Inverse mappings for display
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
        """Load a string of code into the tape starting at 0."""
        self.reset()
        for i, char in enumerate(code_str):
            if i >= self.tape_size: break
            if char in OPCODES:
                self.tape[i] = OPCODES[char]
            elif char == '0':
                self.tape[i] = 0 # Explicit zero
            else:
                # Store raw ASCII for other chars (comments/data)
                self.tape[i] = ord(char)
        return len(code_str)

    def reset(self):
        self.tape = [0] * self.tape_size
        self.ip = 0
        self.dir = 1
        self.h0 = 0
        self.h1 = 0
        self.step_count = 0

    def step(self):
        """
        Execute one 'Momentum' step.
        Physics: Fetch -> Execute (Action/Bounce) -> Move
        """
        # 1. Fetch
        op = self.tape[self.ip]
        
        # 2. Execute (Constructive Actions or Bounces)
        # Note: Actions happen regardless of direction (Time Symmetric)
        
        if op == OPCODES['<']:
            self.h0 = (self.h0 - 1) % self.tape_size
        elif op == OPCODES['>']:
            self.h0 = (self.h0 + 1) % self.tape_size
            
        elif op == OPCODES['{']:
            self.h1 = (self.h1 - 1) % self.tape_size
        elif op == OPCODES['}']:
            self.h1 = (self.h1 + 1) % self.tape_size
            
        elif op == OPCODES['+']:
            self.tape[self.h0] = (self.tape[self.h0] + 1) & 0xFF
        elif op == OPCODES['-']:
            self.tape[self.h0] = (self.tape[self.h0] - 1) & 0xFF
            
        elif op == OPCODES['.']:
            # Accumulate H0 into H1 (H1 += H0)
            self.tape[self.h1] = (self.tape[self.h1] + self.tape[self.h0]) & 0xFF
        elif op == OPCODES[',']:
            # Accumulate H1 into H0 (H0 += H1)
            self.tape[self.h0] = (self.tape[self.h0] + self.tape[self.h1]) & 0xFF
            
        elif op == OPCODES['[']: # Zero Mirror
            if self.tape[self.h0] == 0:
                self.dir *= -1
        elif op == OPCODES[']']: # Non-Zero Mirror
            if self.tape[self.h0] != 0:
                self.dir *= -1
                
        # (All other bytes are NOPs)

        # 3. Move (Apply Velocity)
        self.ip = (self.ip + self.dir) % self.tape_size
        self.step_count += 1
        return True

    def step_back(self):
        """
        True Reversibility.
        Physics: UnMove -> UnExecute
        """
        # 1. UnMove (Where did we come from?)
        # Since IP = OldIP + OldDir, then OldIP = IP - CurrentDir?
        # WAIT! If we reflected, CurrentDir is -OldDir.
        # But reflection happens inside Execute.
        # Let's trace backward:
        # We are at IP. We arrived here via `prev_dir`.
        # So `prev_ip = IP - dir` (using current dir).
        # Why? 
        # Case A (Pass): OldDir=1. Execute(Pass). NewDir=1. Move(+1).
        #    Reverse: IP-1 is OldIP. Correct.
        # Case B (Reflect): OldDir=1. Execute(Reflect). NewDir=-1. Move(-1).
        #    Reverse: IP - (-1) = IP+1. This is OldIP. Correct.
        
        prev_ip = (self.ip - self.dir) % self.tape_size
        
        # 2. UnExecute (at prev_ip)
        op = self.tape[prev_ip]
        
        # For mirrors, we must check if we bounced.
        # If the condition is met, we MUST have bounced to get this direction.
        # So we flip dir back.
        
        if op == OPCODES['[']: # Zero Mirror
            if self.tape[self.h0] == 0:
                self.dir *= -1
        elif op == OPCODES[']']: # Non-Zero Mirror
            if self.tape[self.h0] != 0:
                self.dir *= -1
                
        # For data ops, apply inverse
        elif op == OPCODES['<']: # Inverse is >
            self.h0 = (self.h0 + 1) % self.tape_size
        elif op == OPCODES['>']: # Inverse is <
            self.h0 = (self.h0 - 1) % self.tape_size
            
        elif op == OPCODES['{']: # Inverse is }
            self.h1 = (self.h1 + 1) % self.tape_size
        elif op == OPCODES['}']: # Inverse is {
            self.h1 = (self.h1 - 1) % self.tape_size
            
        elif op == OPCODES['+']: # Inverse is -
            self.tape[self.h0] = (self.tape[self.h0] - 1) & 0xFF
        elif op == OPCODES['-']: # Inverse is +
            self.tape[self.h0] = (self.tape[self.h0] + 1) & 0xFF
            
        elif op == OPCODES['.']: # Inverse is Subtract (H1 -= H0)
            self.tape[self.h1] = (self.tape[self.h1] - self.tape[self.h0]) & 0xFF
        elif op == OPCODES[',']: # Inverse is Subtract (H0 -= H1)
            self.tape[self.h0] = (self.tape[self.h0] - self.tape[self.h1]) & 0xFF
            
        # 3. Restore IP
        self.ip = prev_ip
        self.step_count -= 1
        return True

    # --- IO Utilities ---
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
                    self.tape = [0]*self.tape_size # Resize if needed
                elif k == 'ip': self.ip = int(v)
                elif k == 'dir': self.dir = int(v)
                elif k == 'h0': self.h0 = int(v)
                elif k == 'h1': self.h1 = int(v)
                elif k == 'step': self.step_count = int(v)
                elif k == 'tape': self.tape = [int(x) for x in v.split(',')]
        return True

    def display(self, compact=False):
        # Header
        dir_sym = ">>" if self.dir == 1 else "<<"
        print(f"\nStep: {self.step_count} | IP: {self.ip} {dir_sym} | H0: {self.h0} | H1: {self.h1}")
        
        # Tape View
        start = max(0, self.ip - 8)
        end = min(self.tape_size, self.ip + 9)
        
        if compact:
            # Compact Line View
            row = ""
            for i in range(start, end):
                val = self.tape[i]
                char = BYTE_TO_CHAR.get(val, '0' if val==0 else '?')
                
                markers = ""
                if i == self.ip: markers += "►" if self.dir == 1 else "◄"
                if i == self.h0: markers += "⁰"
                if i == self.h1: markers += "¹"
                
                if markers:
                    row += f"[{markers}{char}]"
                else:
                    row += f" {char} "
            print(row)
        else:
            # Detailed View
            print("-" * 40)
            print("Addr: " + "".join(f"{i:4}" for i in range(start, end)))
            print("Val : " + "".join(f"{self.tape[i]:4}" for i in range(start, end)))
            
            chars = ""
            for i in range(start, end):
                c = BYTE_TO_CHAR.get(self.tape[i], '.')
                if self.tape[i] == 0: c = '0'
                chars += f"{c:4}"
            print("Chr : " + chars)
            
            # Markers
            mk = ""
            for i in range(start, end):
                m = ""
                if i == self.ip: m += "IP"
                if i == self.h0: m += "H0"
                if i == self.h1: m += "H1"
                mk += f"{m:4}"
            print("Ptr : " + mk)

# --- Interactive CLI ---
def main():
    sim = NewtonianBF()
    print("Newtonian BF Simulator (Reversible & Momentum-based)")
    print("Commands: tape <code>, step (s), back (b), run [n], save [f], load [f], compact, quit")
    
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
            sim.display(compact)
        elif op in ['step', 's']:
            sim.step()
            sim.display(compact)
        elif op in ['back', 'b', 'r']:
            sim.step_back()
            sim.display(compact)
        elif op == 'run':
            n = int(cmd[1]) if len(cmd) > 1 else 100
            for _ in range(n): sim.step()
            sim.display(compact)
        elif op == 'compact':
            compact = not compact
            print(f"Compact mode: {compact}")
        elif op == 'save':
            fn = cmd[1] if len(cmd)>1 else "state.bf"
            sim.save_state(fn)
            print(f"Saved to {fn}")
        elif op == 'load':
            fn = cmd[1] if len(cmd)>1 else "state.bf"
            if sim.load_state(fn): print(f"Loaded {fn}")
            else: print("File not found")
            sim.display(compact)
        elif op == 'reset':
            sim.reset()
            sim.display(compact)
        elif op == 'data': # Inject raw data
            pos = int(cmd[1])
            for i, val in enumerate(cmd[2:]):
                sim.tape[(pos+i)%sim.tape_size] = int(val)
            sim.display(compact)
        else:
            print("Unknown command.")

if __name__ == "__main__":
    main()
