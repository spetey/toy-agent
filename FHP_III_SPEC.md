# FHP-III Lattice Gas Specification

## Overview

FHP-III is a lattice gas automaton on a hexagonal (triangular) lattice with:
- 6 moving directions at 60° intervals
- 1 rest particle (zero velocity)
- 7 bits per site, 128 possible states
- Reversible, deterministic microdynamics
- Proper thermalization via rest particles

This document specifies the implementation for use in studying thermodynamic entropy
and Deacon-style morphodynamic agency.

## Why FHP over HPP?

HPP (square lattice, 4 directions) has "spurious invariants"—conserved quantities
beyond mass and momentum that prevent proper thermalization. The hexagonal geometry
of FHP breaks these invariants, giving:

- Isotropic viscosity (correct Navier-Stokes in continuum limit)
- Proper mixing and equilibration
- No "ghost" artifacts from resonant modes

## Hexagonal Lattice Geometry

### Coordinate System

We use an "offset coordinates" system where:
- Even rows (y % 2 == 0): sites at integer x positions
- Odd rows (y % 2 == 1): sites shifted right by 0.5

```
Row 2:  O   O   O   O   O      (y=2, even)
       / \ / \ / \ / \ / \
Row 1:   O   O   O   O   O    (y=1, odd, shifted)
       \ / \ / \ / \ / \ /
Row 0:  O   O   O   O   O      (y=0, even)
        0   1   2   3   4      (x coordinates)
```

### The 6 Directions

Directions are numbered 0-5, going counter-clockwise from East:

```
        2   1
         \ /
     3 ---O--- 0
         / \
        4   5
```

| Dir | Name | Angle | Velocity (vx, vy) |
|-----|------|-------|-------------------|
| 0   | E    | 0°    | (1, 0)            |
| 1   | NE   | 60°   | (0.5, √3/2) ≈ (0.5, 0.866) |
| 2   | NW   | 120°  | (-0.5, √3/2)      |
| 3   | W    | 180°  | (-1, 0)           |
| 4   | SW   | 240°  | (-0.5, -√3/2)     |
| 5   | SE   | 300°  | (0.5, -√3/2)      |
| 6   | R    | -     | (0, 0) [rest]     |

**Important**: For momentum conservation, we work with integer momentum by scaling.
Define unit vectors in a coordinate system where they form a closed hexagon:

```python
# Integer momentum vectors (sum to zero around hexagon)
VELOCITY = [
    (2, 0),    # 0: E
    (1, 1),    # 1: NE  
    (-1, 1),   # 2: NW
    (-2, 0),   # 3: W
    (-1, -1),  # 4: SW
    (1, -1),   # 5: SE
    (0, 0),    # 6: R (rest)
]
```

With this convention, opposite directions sum to zero:
- 0 + 3 = (2,0) + (-2,0) = (0,0) ✓
- 1 + 4 = (1,1) + (-1,-1) = (0,0) ✓
- 2 + 5 = (-1,1) + (1,-1) = (0,0) ✓

### Neighbor Indexing

The neighbor in direction d from site (x, y) depends on row parity:

```python
# For even rows (y % 2 == 0):
NEIGHBORS_EVEN = [
    (1, 0),    # 0: E
    (0, 1),    # 1: NE
    (-1, 1),   # 2: NW
    (-1, 0),   # 3: W
    (-1, -1),  # 4: SW
    (0, -1),   # 5: SE
]

# For odd rows (y % 2 == 1):
NEIGHBORS_ODD = [
    (1, 0),    # 0: E
    (1, 1),    # 1: NE
    (0, 1),    # 2: NW
    (-1, 0),   # 3: W
    (0, -1),   # 4: SW
    (1, -1),   # 5: SE
]
```

## State Representation

Each site has 7 bits:
- Bits 0-5: Particle in direction 0-5
- Bit 6: Rest particle

State is an integer 0-127. Examples:
- State 9 = 0001001 = directions 0 and 3 = E+W (head-on collision)
- State 73 = 1001001 = E+W+R (head-on with rest spectator)
- State 127 = 1111111 = all 7 particles present

## Collision Rules

### Conservation Laws

Every collision must conserve:
1. **Particle number**: Same number of bits set in/out
2. **Momentum**: Sum of velocity vectors unchanged

### Key Collision Types

#### Two-body head-on collisions
When two particles meet head-on (opposite directions), they can scatter:

```
Before:     After (option A):    After (option B):
   \           |                    /
    O          O                   O
   /           |                    \
```

- Directions 0+3 (E+W) can become 1+4 (NE+SW) or 2+5 (NW+SE)
- Directions 1+4 (NE+SW) can become 0+3 or 2+5
- Directions 2+5 (NW+SE) can become 0+3 or 1+4

To maintain reversibility with determinism, we choose based on site parity.

#### Three-body collisions
Three particles at 120° apart can rotate:

```
Before:        After:
  \              |
   O--           O
  /               \
```

- Directions 0+2+4 can become 1+3+5
- Directions 1+3+5 can become 0+2+4

#### Rest particle involvement (FHP-III)
The rest particle enables energy redistribution:

- **Creation**: Two head-on particles + one other → rest + scattered pair
- **Annihilation**: Rest + pair → three moving particles

Example:
- E + W + NE (0+3+1) → R + NE + SW (6+1+4) — rest created
- R + NE + SW (6+1+4) → E + W + NE (0+3+1) — rest annihilated

### Collision Table Strategy

With 128 states, we build a lookup table. The table must be a **bijection** (permutation)
on the 128 states to ensure reversibility.

For states where multiple valid outputs exist (e.g., 2-body head-on), we choose
deterministically based on *site parity* (x + y) mod 2:
- Even sites: rotate clockwise
- Odd sites: rotate counter-clockwise

This breaks spatial symmetry and improves mixing.

### Detailed Collision Rules

#### Zero or one particle: Identity
States 0-6 (0, 1, 2, 4, 8, 16, 32, 64 particles): pass through unchanged.

#### Two-body head-on (no rest)
| Input     | Bits  | Output (even site) | Output (odd site) |
|-----------|-------|--------------------|--------------------|
| E+W       | 0+3   | NE+SW (1+4)        | NW+SE (2+5)        |
| NE+SW     | 1+4   | NW+SE (2+5)        | E+W (0+3)          |
| NW+SE     | 2+5   | E+W (0+3)          | NE+SW (1+4)        |

#### Two-body non-head-on: Identity
Adjacent or skip-one pairs don't collide (no momentum-conserving alternative).

#### Three-body symmetric
| Input     | Bits    | Output  |
|-----------|---------|---------|
| 0+2+4     | E+NW+SW | 1+3+5 (NE+W+SE) |
| 1+3+5     | NE+W+SE | 0+2+4 (E+NW+SW) |

#### With rest particle
The rest particle enables conversions between 2-body and 3-body states:

| Input         | Output        | Notes                    |
|---------------|---------------|--------------------------|
| E+W+R (0+3+6) | 1+4+6 or 2+5+6 | 2-body rotates, R spectator |
| 0+3+1 (E+W+NE)| 6+1+4 (R+NE+SW)| Rest creation (site parity) |
| 6+1+4         | 0+3+1         | Rest annihilation        |

And similar for all rotations of these patterns.

## Implementation Notes

### Efficient Collision via Lookup Table

```python
# Build 128-element table for each parity
COLLISION_TABLE_EVEN = np.zeros(128, dtype=np.uint8)
COLLISION_TABLE_ODD = np.zeros(128, dtype=np.uint8)

for state in range(128):
    COLLISION_TABLE_EVEN[state] = compute_collision(state, parity=0)
    COLLISION_TABLE_ODD[state] = compute_collision(state, parity=1)
```

### Efficient Streaming

Streaming is trickier than HPP due to hexagonal geometry:

```python
def streaming_step(state, Lx, Ly):
    new_state = np.zeros_like(state)
    
    for y in range(Ly):
        for x in range(Lx):
            for d in range(6):
                if state[x, y] & (1 << d):
                    # Find neighbor in direction d
                    if y % 2 == 0:
                        nx, ny = x + NEIGHBORS_EVEN[d][0], y + NEIGHBORS_EVEN[d][1]
                    else:
                        nx, ny = x + NEIGHBORS_ODD[d][0], y + NEIGHBORS_ODD[d][1]
                    
                    # Periodic boundaries
                    nx, ny = nx % Lx, ny % Ly
                    
                    # Particle arrives at neighbor in same direction
                    new_state[nx, ny] |= (1 << d)
            
            # Rest particles don't move
            if state[x, y] & (1 << 6):
                new_state[x, y] |= (1 << 6)
    
    return new_state
```

For efficiency, this can be vectorized using `np.roll` with different shifts for 
even/odd rows, but the logic is more complex than HPP.

### Vectorized Streaming (Advanced)

```python
def streaming_step_vectorized(state, Lx, Ly):
    new_state = np.zeros_like(state)
    
    # Create row parity mask
    even_rows = np.arange(Ly) % 2 == 0
    
    for d in range(6):
        particles = (state >> d) & 1
        
        # Shift differently for even/odd rows
        dx_even, dy_even = NEIGHBORS_EVEN[d]
        dx_odd, dy_odd = NEIGHBORS_ODD[d]
        
        # This requires careful indexing - see implementation
        # ...
        
    # Rest particles stay
    new_state |= (state & (1 << 6))
    
    return new_state
```

## Verification Checks

1. **Reversibility**: Apply collision twice with velocity reversal → original state
2. **Particle conservation**: `popcount(state_in) == popcount(state_out)`
3. **Momentum conservation**: `sum(velocities_in) == sum(velocities_out)`
4. **Equilibration**: Starting from blob, density should become uniform
5. **No ghosts**: Rest particles should spread (unlike HPP)
6. **H-theorem**: Coarse-grained entropy should increase

## Physical Parameters

### Density
- ρ = (number of particles) / (7 × number of sites)
- Maximum density ρ = 1 (all sites full)
- Typical simulation: ρ ≈ 0.1 to 0.5

### Temperature Proxy
- T ∝ (moving particles) / (rest particles)
- High T: most particles moving
- Low T: most particles at rest

### Pressure
- P ∝ ρ × T (ideal gas-like at low density)

## Boundary Conditions

### Periodic
Standard wrap-around. Good for equilibrium studies.

### Walls (bounce-back)
Particles hitting wall reverse direction. Conserves particles and momentum = 0 at wall.

### Injection/Absorption (for gradients)
- Left boundary: Inject particles moving East with probability p_in
- Right boundary: Absorb particles moving East with probability p_out

This creates a steady-state density gradient that can drive "work" in the 
Deacon sense.

## Connection to Thermodynamics

### Boltzmann Entropy
For a macrostate defined by particle counts in spatial blocks:

$$S_B = k_B \ln W$$

where W is the number of microstates compatible with the macrostate.

### Ebtekar-Hutter Algorithmic Entropy
$$S_\pi(x) = K(x) + \log \pi(x)$$

where K(x) is Kolmogorov complexity and π(x) is the stationary measure.

For "typical" microstates, this reduces to Boltzmann entropy.
For "special" microstates (low K), entropy is lower.

### MCG Postulate
The Markovian Coarse-Graining postulate should hold for FHP:
after a few collision times, coarse-grained dynamics are approximately Markovian.

This enables the Ebtekar-Hutter framework for tracking entropy along 
individual trajectories.

## References

1. Frisch, Hasslacher, Pomeau (1986) - Original FHP paper
2. Wolf-Gladrow "Lattice-Gas Cellular Automata and Lattice Boltzmann Models"
3. Chopard & Droz "Cellular Automata Modeling of Physical Systems"
4. Ebtekar & Hutter "Foundations of Algorithmic Thermodynamics"
