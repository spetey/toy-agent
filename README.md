# fuckbrain 2D (fb2d)

A reversible, valid-everywhere, Turing-complete 2D programming language
and simulator.

fb2d is designed as the substrate for a "toy agent" that can resist its
own degradation by noise. It's based on Google's BFF from the
[Computational Life paper](https://arxiv.org/abs/2406.19108), which is
in turn based on brainfuck. (fuckbrain = reversible brainfuck.)

## Key Properties

- **Reversible**: every state has a unique predecessor, inferred from the
  current state alone. The simulator can step backward as easily as
  forward.
- **Valid everywhere**: every byte (0-255) is either a known opcode or
  NOP. Any byte sequence is a valid program. There are no syntax errors.
- **Turing-complete**: via counter machine simulation with Fredkin
  dispatch blocks. See `docs/tc_proof_sketch.md`.

## Quick Start

```bash
# Interactive simulator
python3 fb2d.py

# Inside the simulator:
#   load factorial        — load a program from programs/
#   run 1000              — run 1000 steps forward
#   back 1000             — run 1000 steps backward (perfectly reversed)
#   show                  — display the grid
#   help                  — full command reference

# Compile an ifb program to fb2d
python3 ifbc.py programs/factorial.ifb programs/factorial-out.fb2d

# Run compiler tests
python3 ifbc.py --test-all

# Run carry arithmetic demo
python3 programs/carry-demo.py
```

## Architecture

An instruction pointer (IP) moves on a toroidal 2D grid. Mirrors (`/`,
`\`) and conditional mirrors change the IP's direction. Four heads point
into the grid for data access:

| Head | Purpose |
|------|---------|
| H0 | Primary data head |
| H1 | Secondary data head |
| CL | Condition latch (used by conditional mirrors) |
| GP | Garbage pointer (breadcrumb trail for reversibility) |

Code and data share the same surface (von Neumann architecture). The
ISA has 43 opcodes (byte-level arithmetic, bit-level operations, head
movement, mirrors, and garbage-pointer operations). See `CLAUDE.md` for
the full ISA reference.

## ifb (intermediate fuckbrain)

A Janus-like imperative language that compiles to fb2d grid files:

```
var n = 5
var result = 1
var acc = 0
var count = 0

while n do
    count += n
    while count do
        acc += result
        count -= 1
    end
    swap acc result
    zero acc
    n -= 1
end
// result = 120 (5!)
```

## Project Structure

```
fb2d.py              Simulator (interactive REPL)
ifbc.py              ifb-to-fb2d compiler
programs/            Example programs (.fb2d state files, .ifb source)
  factorial.ifb      Factorial in ifb
  factorial.fb2d     Compiled factorial
  carry-demo.py      Multi-cell carry arithmetic demo
docs/                Design documents and proofs
  tc_proof_sketch.md Turing completeness proof sketch
CLAUDE.md            Detailed project context for AI assistants
```

## Status

This is active research software. The language design is stabilizing but
not frozen. Current work focuses on unbounded integers (multi-cell carry
arithmetic) and bit-level error correction primitives.

## License

Research use. License TBD.
