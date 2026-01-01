# FHP-III Lattice Gas Simulation

A toy physics world for studying thermodynamic entropy and Deacon-style morphodynamic agency using the FHP-III lattice gas model on a hexagonal lattice.

## Overview

This implementation uses the FHP-III lattice gas model, which features:
- Hexagonal lattice with 6 moving directions + 1 rest particle
- Proper thermalization (unlike HPP which has spurious invariants)
- Reversible, deterministic microdynamics
- Conservation of particle number and momentum
- Increasing entropy and equilibration

## Quick Start

### Installation

```bash
pip install -r requirements.txt
```

**Note:** Numba is optional but highly recommended for ~400x speedup on large lattices.

### Running the Simulation

```bash
# Quick physics verification test
python fhp_iii_simulation.py --test

# Interactive visualization (64x64 lattice)
python fhp_iii_simulation.py

# Larger lattice (128x128)
python fhp_iii_simulation.py 128

# Save frames to disk
python fhp_iii_simulation.py 128 --save
```

## Simulation Modes

### Default Mode
Interactive visualization with blob initialization. Watch particles diffuse and thermalize.
```bash
python fhp_iii_simulation.py [size]
```

### Test Mode (`--test`)
Quick physics verification - checks particle/momentum conservation and entropy increase.
```bash
python fhp_iii_simulation.py --test
```

### Gradient Mode (`--gradient`)
Non-equilibrium steady state with particle injection on the left boundary and absorption on the right. Creates density gradients for studying dissipative dynamics.
```bash
python fhp_iii_simulation.py --gradient
python fhp_iii_simulation.py 128 --gradient
```

Features:
- Particles injected at left boundary (configurable p_inject rate)
- Particles absorbed at right boundary (configurable p_absorb rate)
- Walls at x=0 and x=Lx-1 (bounce-back)
- Periodic boundaries in y direction
- Real-time density profile visualization
- Net flux tracking (injected - absorbed)

### Vortex Mode (`--vortex`)
Morphodynamics experiment with obstacle in flow. Attempts to create vortex structures (von Kármán vortex street). Measures entropy production rate (EPR) and vorticity.
```bash
python fhp_iii_simulation.py --vortex
python fhp_iii_simulation.py 256 --vortex   # 512x256 lattice
```

Features:
- Flat plate obstacle to disturb flow
- 6-panel visualization: density, velocity, vorticity, profile, EPR, parameters
- EPR calculation: throughput × density gradient
- Vorticity field visualization

**Note:** Due to high viscosity of FHP at mesoscale, coherent vortices are difficult to achieve. See "Future Directions" for alternatives.

### Batch Mode (`--batch`)
Headless mode for fast parameter sweeps and large-scale experiments. No visualization overhead - saves snapshots periodically.
```bash
python fhp_iii_simulation.py --batch              # 512x256, 10000 steps
python fhp_iii_simulation.py --batch 256 5000     # 512x256, 5000 steps
python fhp_iii_simulation.py --batch 1024 20000   # 2048x1024, 20000 steps
```

Output: Saves PNG snapshots to `vortex_batch_*.png`

## Performance

With Numba installed, the simulation achieves ~400x speedup through JIT compilation and parallel execution:
- Without Numba: ~500ms per step (128x64)
- With Numba: ~1.3ms per step (128x64)

For large lattices (512+), Numba is essential.

## Files

- **fhp_iii_simulation.py** - Main simulation code (ready to run)
- **fhp_collision_builder_v2.py** - Collision table builder
- **FHP_III_SUMMARY.md** - High-level project summary and results
- **FHP_III_SPEC.md** - Detailed technical specification

## Verified Physics

✅ Particle conservation
✅ Momentum conservation
✅ Entropy increases and plateaus
✅ Good mixing (no ghost artifacts like HPP)
✅ Rest particles created dynamically
✅ Equilibration to uniform density
✅ Gradient-driven steady states

## Future Directions

### Alternative Morphodynamic Structures

The current vortex experiments show that FHP has high effective viscosity at mesoscale, making vortex formation difficult. Promising alternatives:

1. **Bénard Convection** - Temperature gradient (hot bottom, cold top) driving convection cells. Requires adding energy exchange mechanics.

2. **Turing Patterns** - Reaction-diffusion with two "species" of particles having different diffusion rates.

3. **Catalytic Surfaces** - Obstacles that preferentially absorb/emit certain particle directions, creating local organization.

### Other Extensions

- **Kolmogorov complexity** - Compress states with zlib to estimate K(x)
- **Lattice Boltzmann Method (LBM)** - Continuous distribution functions for lower viscosity
- **Multi-speed particles** - Higher energy states for temperature dynamics

## Key Physics Concepts

### Why FHP over HPP?
HPP (square lattice, 4 directions) has "spurious invariants" - conserved quantities beyond mass and momentum. This causes poor mixing and ghost artifacts. FHP's hexagonal geometry breaks these invariants.

### Reynolds Number and Vortices
Vortex formation requires Re = (flow speed × obstacle size) / viscosity > ~40. FHP's high viscosity at mesoscale makes this challenging without very large lattices.

### Entropy Production Rate (EPR)
In gradient mode, EPR ≈ throughput × gradient. Morphodynamic structures would ideally maintain low local entropy while increasing total EPR.

## References

1. Frisch, Hasslacher, Pomeau (1986) - Original FHP paper
2. Wolf-Gladrow "Lattice-Gas Cellular Automata and Lattice Boltzmann Models"
3. Chopard & Droz "Cellular Automata Modeling of Physical Systems"
4. Ebtekar & Hutter "Foundations of Algorithmic Thermodynamics"
