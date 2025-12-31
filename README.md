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

## Files

- **FHP_III_SUMMARY.md** - High-level project summary and results
- **FHP_III_SPEC.md** - Detailed technical specification
- **fhp_iii_simulation.py** - Main simulation code (ready to run)
- **fhp_collision_builder_v2.py** - Collision table builder
- **fhp_iii_summary.png** - Sample output visualization

## Verified Physics

✅ Particle conservation
✅ Momentum conservation
✅ Entropy increases and plateaus
✅ Good mixing (no ghost artifacts like HPP)
✅ Rest particles created dynamically
✅ Equilibration to uniform density

## Next Steps

Potential extensions:
1. **Boundary driving** - Inject/absorb particles to create steady-state gradients
2. **Better entropy calculation** - Full microstate counting
3. **Kolmogorov complexity** - Compress states with zlib to estimate K(x)
4. **Deacon-style agents** - Localized structures maintaining organization via gradients
5. **Performance** - Vectorized streaming or CUDA acceleration

## References

1. Frisch, Hasslacher, Pomeau (1986) - Original FHP paper
2. Wolf-Gladrow "Lattice-Gas Cellular Automata and Lattice Boltzmann Models"
3. Chopard & Droz "Cellular Automata Modeling of Physical Systems"
4. Ebtekar & Hutter "Foundations of Algorithmic Thermodynamics"
