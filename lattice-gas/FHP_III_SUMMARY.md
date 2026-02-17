# FHP-III Lattice Gas Implementation Summary

## Project Context

This is a toy physics world for studying thermodynamic entropy and Deacon-style morphodynamic agency. The implementation uses the FHP-III lattice gas model on a hexagonal lattice, chosen because it has proper thermalization (unlike HPP on a square lattice which has spurious invariants).

## Files in this Project

1. **FHP_III_SPEC.md** - Detailed specification of the model
2. **fhp_collision_builder_v2.py** - Builds the collision lookup tables
3. **fhp_iii_simulation.py** - Main simulation code (ready to run)
4. **fhp_iii_summary.png** - Sample output showing good mixing behavior

## Key Implementation Details

### State Representation
- 7 bits per site (uint8): 6 moving directions + 1 rest particle
- Directions numbered 0-5 counter-clockwise from East, 6 = rest
- Total 128 possible states per site

### Hexagonal Lattice
- Offset coordinates: odd rows shifted right by 0.5
- Neighbor offsets differ for even/odd rows (see NEIGHBORS_EVEN, NEIGHBORS_ODD arrays)
- Integer momentum vectors that sum correctly around hexagon

### Collision Rules
- Pre-computed 128-element lookup tables (one for even parity sites, one for odd)
- Rules form bijections (reversible dynamics)
- Conserve particle number and momentum
- Key collisions:
  - Head-on pairs (0+3, 1+4, 2+5) rotate in 3-cycles
  - Symmetric triplets (0+2+4 ↔ 1+3+5)
  - Various 3+ body collisions with momentum-conserving swaps

### Streaming Step
- Each direction streams to appropriate neighbor
- Must handle even/odd row differences
- Rest particles (direction 6) don't move
- Currently using loop-based implementation (correct but slow)
- TODO: Fully vectorized version for speed

## Running the Code

```bash
# Quick physics test
python fhp_iii_simulation.py --test

# Interactive visualization (64x64 default)
python fhp_iii_simulation.py

# Larger lattice
python fhp_iii_simulation.py 128

# Save frames to disk
python fhp_iii_simulation.py 128 --save
```

## Verified Physics

✅ Particle conservation  
✅ Momentum conservation  
✅ Entropy increases and plateaus  
✅ Good mixing (no ghost artifacts like HPP)  
✅ Rest particles created dynamically through collisions  
✅ Equilibration to uniform density  

## Current Limitations / Future Work

### Performance
- Streaming step uses Python loops (slow for large lattices)
- Could be vectorized or ported to CUDA

### Physics Extensions
1. **Boundary driving**: Inject particles on left, absorb on right to create steady-state gradient
2. **Better entropy calculation**: Current is approximate; could compute full microstate counting
3. **Kolmogorov complexity approximation**: Compress state with zlib to estimate K(x)
4. **Deacon-style agents**: Localized structures that maintain organization by coupling to gradients

### Code Organization
- Could split into modules (lattice.py, collision.py, visualization.py)
- Add configuration file support
- Add logging/checkpointing for long runs

## Key Physics Concepts

### Why FHP over HPP?
HPP (square lattice, 4 directions) has "spurious invariants" - conserved quantities beyond mass and momentum. This causes:
- Poor mixing (ghost artifacts persist forever)
- Regular oscillations in entropy
- Density gradients that don't equilibrate

FHP's hexagonal geometry breaks these invariants, giving proper thermalization.

### Rest Particles (FHP-III vs FHP-I)
FHP-I has only 6 moving directions. FHP-III adds a rest particle (0 velocity) which enables:
- Energy redistribution between kinetic and "rest energy"
- Temperature-like behavior (moving/rest ratio)
- Better thermalization

### Markovian Coarse-Graining (MCG)
The Ebtekar-Hutter framework defines algorithmic entropy:
$$S_\pi(x) = K(x) + \log \pi(x)$$

For this to work, the coarse-grained dynamics should be approximately Markovian:
$$\Pr(M_{t+1} = m' | M_t = m, M_{t-1}, ...) \approx \Pr(M_{t+1} = m' | M_t = m)$$

FHP should satisfy this after a few collision times when coarse-graining over spatial blocks.

## Sample Results

From a 64x64 lattice with initial blob:
- Entropy: ~3000 → ~6000 (doubles)
- Rest particles: 0 → ~200-250 (created dynamically)
- Mixing time: ~100-200 timesteps for visual uniformity
- No oscillatory artifacts like HPP

## References

1. Frisch, Hasslacher, Pomeau (1986) - Original FHP paper
2. Wolf-Gladrow "Lattice-Gas Cellular Automata and Lattice Boltzmann Models"
3. Chopard & Droz "Cellular Automata Modeling of Physical Systems"
4. Ebtekar & Hutter "Foundations of Algorithmic Thermodynamics"
5. Toffoli & Margolus "Cellular Automata Machines"
