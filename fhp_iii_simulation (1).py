# Authored or modified by Claude
# Version: 2024-12-31 v1.0 - FHP-III Lattice Gas Simulation

"""
FHP-III Lattice Gas Automaton

A lattice gas on a hexagonal lattice with:
- 6 moving directions (60° apart)
- 1 rest particle
- 7 bits per site, 128 possible states
- Reversible, deterministic microdynamics
- Proper thermalization (unlike HPP)

Usage:
    python fhp_iii_simulation.py [size] [--test] [--save]
"""

import numpy as np
import matplotlib.pyplot as plt
import matplotlib.animation as animation

# Direction indices
E, NE, NW, W, SW, SE, R = 0, 1, 2, 3, 4, 5, 6
DIR_NAMES = ['E', 'NE', 'NW', 'W', 'SW', 'SE', 'R']

# Velocity vectors (integer-scaled for exact momentum)
VELOCITY = np.array([
    [2, 0],    # E
    [1, 1],    # NE
    [-1, 1],   # NW
    [-2, 0],   # W
    [-1, -1],  # SW
    [1, -1],   # SE
    [0, 0],    # R
], dtype=np.int32)

# Neighbor offsets for hexagonal lattice
# Even rows (y % 2 == 0)
NEIGHBORS_EVEN = np.array([
    [1, 0],    # E
    [0, 1],    # NE
    [-1, 1],   # NW
    [-1, 0],   # W
    [-1, -1],  # SW
    [0, -1],   # SE
], dtype=np.int32)

# Odd rows (y % 2 == 1)
NEIGHBORS_ODD = np.array([
    [1, 0],    # E
    [1, 1],    # NE
    [0, 1],    # NW
    [-1, 0],   # W
    [0, -1],   # SW
    [1, -1],   # SE
], dtype=np.int32)

# Pre-computed collision tables (from fhp_collision_builder_v2.py)
COLLISION_TABLE_EVEN = np.array([0, 1, 2, 3, 4, 66, 6, 7, 8, 18, 68, 38, 12, 22, 14, 15, 16, 96, 36, 37, 72, 42, 74, 75, 24, 52, 44, 45, 28, 90, 30, 110, 32, 33, 65, 35, 9, 98, 69, 39, 80, 50, 21, 83, 84, 54, 77, 87, 48, 49, 81, 51, 104, 105, 27, 107, 56, 57, 89, 117, 60, 122, 93, 63, 64, 34, 5, 67, 10, 11, 70, 71, 20, 82, 13, 102, 76, 86, 78, 79, 40, 41, 100, 101, 26, 106, 46, 47, 88, 116, 108, 109, 92, 62, 94, 95, 17, 97, 19, 99, 73, 43, 23, 103, 25, 114, 85, 55, 29, 118, 31, 111, 112, 113, 53, 115, 58, 59, 91, 119, 120, 121, 61, 123, 124, 125, 126, 127], dtype=np.uint8)

COLLISION_TABLE_ODD = np.array([0, 1, 2, 3, 4, 66, 6, 7, 8, 36, 68, 69, 12, 74, 14, 15, 16, 96, 9, 98, 72, 42, 13, 102, 24, 104, 84, 54, 28, 108, 30, 110, 32, 33, 65, 35, 18, 19, 11, 39, 80, 81, 21, 101, 26, 27, 86, 87, 48, 49, 41, 51, 25, 114, 45, 107, 56, 57, 116, 117, 60, 122, 93, 63, 64, 34, 5, 67, 10, 38, 70, 71, 20, 100, 22, 23, 76, 46, 78, 79, 40, 50, 73, 43, 44, 106, 77, 47, 88, 58, 29, 118, 92, 62, 94, 95, 17, 97, 37, 99, 82, 83, 75, 103, 52, 53, 85, 55, 90, 91, 31, 111, 112, 113, 105, 115, 89, 59, 109, 119, 120, 121, 61, 123, 124, 125, 126, 127], dtype=np.uint8)


class FHPLattice:
    """FHP-III Lattice Gas Simulation."""
    
    def __init__(self, Lx, Ly, boundary='periodic'):
        """
        Initialize lattice.
        
        Args:
            Lx: Width (number of sites in x)
            Ly: Height (number of sites in y)
            boundary: 'periodic' or 'walls'
        """
        self.Lx = Lx
        self.Ly = Ly
        self.boundary = boundary
        
        # State array: 7 bits per site packed into uint8
        self.state = np.zeros((Lx, Ly), dtype=np.uint8)
        
        # Pre-compute parity mask
        x_idx, y_idx = np.meshgrid(np.arange(Lx), np.arange(Ly), indexing='ij')
        self.parity = (x_idx + y_idx) % 2
        
        # Pre-compute row parity for streaming
        self.row_parity = np.arange(Ly) % 2
        
        self.time = 0
    
    def get_density(self):
        """Get total particle count at each site (0-7)."""
        density = np.zeros((self.Lx, self.Ly), dtype=np.int32)
        for d in range(7):
            density += (self.state >> d) & 1
        return density
    
    def get_moving_count(self):
        """Get moving particle count (directions 0-5)."""
        count = np.zeros((self.Lx, self.Ly), dtype=np.int32)
        for d in range(6):
            count += (self.state >> d) & 1
        return count
    
    def get_rest_count(self):
        """Get rest particle count (direction 6)."""
        return (self.state >> R) & 1
    
    def get_momentum(self):
        """Get momentum field at each site."""
        px = np.zeros((self.Lx, self.Ly), dtype=np.int32)
        py = np.zeros((self.Lx, self.Ly), dtype=np.int32)
        for d in range(7):
            mask = (self.state >> d) & 1
            px += mask * VELOCITY[d, 0]
            py += mask * VELOCITY[d, 1]
        return px, py
    
    def total_particles(self):
        """Total particle count."""
        return self.get_density().sum()
    
    def total_momentum(self):
        """Total momentum vector."""
        px, py = self.get_momentum()
        return px.sum(), py.sum()
    
    def total_moving(self):
        """Total moving particles."""
        return self.get_moving_count().sum()
    
    def total_rest(self):
        """Total rest particles."""
        return self.get_rest_count().sum()
    
    def collision_step(self):
        """Apply collision rules via parity-dependent lookup."""
        even_result = COLLISION_TABLE_EVEN[self.state]
        odd_result = COLLISION_TABLE_ODD[self.state]
        self.state = np.where(self.parity == 0, even_result, odd_result)
    
    def streaming_step(self):
        """Move particles to neighboring sites on hexagonal lattice."""
        new_state = np.zeros_like(self.state)
        
        # Process each direction separately
        for d in range(6):
            particles = (self.state >> d) & 1
            
            # Create shifted arrays for even and odd rows
            shifted = np.zeros_like(particles)
            
            for y in range(self.Ly):
                if y % 2 == 0:
                    dx, dy = NEIGHBORS_EVEN[d]
                else:
                    dx, dy = NEIGHBORS_ODD[d]
                
                for x in range(self.Lx):
                    if particles[x, y]:
                        # Target position
                        nx = (x + dx) % self.Lx
                        ny = (y + dy) % self.Ly
                        
                        if self.boundary == 'periodic':
                            shifted[nx, ny] = 1
                        elif self.boundary == 'walls':
                            # Bounce back at walls
                            if 0 <= x + dx < self.Lx and 0 <= y + dy < self.Ly:
                                shifted[nx, ny] = 1
                            else:
                                # Reflect: particle stays at (x,y) but reverses direction
                                opp_d = (d + 3) % 6  # Opposite direction
                                new_state[x, y] |= (1 << opp_d)
            
            new_state |= (shifted << d)
        
        # Rest particles don't move
        new_state |= (self.state & (1 << R))
        
        self.state = new_state.astype(np.uint8)
    
    def streaming_step_vectorized(self):
        """Vectorized streaming (faster for large lattices)."""
        new_state = np.zeros_like(self.state)
        
        for d in range(6):
            particles = (self.state >> d) & 1
            
            # Process even and odd rows separately
            shifted = np.zeros_like(particles)
            
            # Even rows
            dx_e, dy_e = NEIGHBORS_EVEN[d]
            even_mask = np.zeros((self.Lx, self.Ly), dtype=bool)
            even_mask[:, ::2] = True
            even_particles = particles * even_mask
            
            # Odd rows
            dx_o, dy_o = NEIGHBORS_ODD[d]
            odd_mask = ~even_mask
            odd_particles = particles * odd_mask
            
            # Roll and combine
            if self.boundary == 'periodic':
                rolled_even = np.roll(np.roll(even_particles, dx_e, axis=0), dy_e, axis=1)
                rolled_odd = np.roll(np.roll(odd_particles, dx_o, axis=0), dy_o, axis=1)
                shifted = rolled_even + rolled_odd
            else:
                # For walls, more complex handling needed
                shifted = np.roll(np.roll(even_particles, dx_e, axis=0), dy_e, axis=1)
                shifted += np.roll(np.roll(odd_particles, dx_o, axis=0), dy_o, axis=1)
            
            new_state |= (shifted << d)
        
        # Rest particles stay
        new_state |= (self.state & (1 << R))
        
        self.state = new_state.astype(np.uint8)
    
    def step(self):
        """One full timestep."""
        self.collision_step()
        self.streaming_step()  # Use non-vectorized for correctness
        self.time += 1
    
    def initialize_blob(self, cx, cy, radius, density=0.5, rest_fraction=0.0):
        """Initialize a circular blob of particles."""
        for x in range(self.Lx):
            for y in range(self.Ly):
                # Hexagonal distance (approximate)
                dx = x - cx
                dy = y - cy
                dist_sq = dx*dx + dy*dy
                
                if dist_sq < radius * radius:
                    state = 0
                    for d in range(6):
                        if np.random.random() < density:
                            state |= (1 << d)
                    if np.random.random() < rest_fraction:
                        state |= (1 << R)
                    self.state[x, y] = state
    
    def initialize_uniform(self, density=0.3, rest_fraction=0.1):
        """Initialize with uniform random particles."""
        for x in range(self.Lx):
            for y in range(self.Ly):
                state = 0
                for d in range(6):
                    if np.random.random() < density:
                        state |= (1 << d)
                if np.random.random() < rest_fraction:
                    state |= (1 << R)
                self.state[x, y] = state


def coarse_grain_entropy(lattice, block_size=8):
    """
    Compute coarse-grained entropy.
    
    Partition into blocks and compute entropy from density distribution.
    """
    density = lattice.get_density()
    Lx, Ly = lattice.Lx, lattice.Ly
    
    n_blocks_x = Lx // block_size
    n_blocks_y = Ly // block_size
    
    total_entropy = 0.0
    
    for bx in range(n_blocks_x):
        for by in range(n_blocks_y):
            block = density[bx*block_size:(bx+1)*block_size,
                           by*block_size:(by+1)*block_size]
            n = block.sum()
            sites = block_size * block_size
            max_particles = sites * 7  # 7 particles max per site
            
            if n > 0 and n < max_particles:
                p = n / max_particles
                if 0 < p < 1:
                    total_entropy -= max_particles * (p * np.log(p) + (1-p) * np.log(1-p))
    
    return total_entropy


def run_visualization(Lx=64, Ly=64, steps=500, block_size=8):
    """Run simulation with live visualization."""
    print(f"Creating FHP-III lattice: {Lx}x{Ly}")
    
    lattice = FHPLattice(Lx, Ly, boundary='periodic')
    lattice.initialize_blob(Lx//4, Ly//3, Lx//5, density=0.6, rest_fraction=0.0)
    
    print(f"Initial particles: {lattice.total_particles()}")
    print(f"Initial momentum: {lattice.total_momentum()}")
    
    fig, axes = plt.subplots(2, 2, figsize=(12, 10))
    fig.suptitle(f'FHP-III Lattice Gas ({Lx}x{Ly})', fontsize=14)
    
    # Density plot
    ax_density = axes[0, 0]
    density = lattice.get_density()
    im_density = ax_density.imshow(density.T, origin='lower', cmap='hot',
                                    vmin=0, vmax=7, interpolation='nearest')
    ax_density.set_title('Particle Density')
    plt.colorbar(im_density, ax=ax_density)
    
    # Rest particles
    ax_rest = axes[0, 1]
    rest = lattice.get_rest_count()
    im_rest = ax_rest.imshow(rest.T, origin='lower', cmap='Greens',
                              vmin=0, vmax=1, interpolation='nearest')
    ax_rest.set_title('Rest Particles')
    plt.colorbar(im_rest, ax=ax_rest)
    
    # Entropy history
    ax_entropy = axes[1, 0]
    entropy_history = []
    
    # Energy partition
    ax_stats = axes[1, 1]
    moving_history = []
    rest_history = []
    
    def update(frame):
        for _ in range(5):
            lattice.step()
        
        density = lattice.get_density()
        im_density.set_array(density.T)
        ax_density.set_title(f'Particle Density (t={lattice.time})')
        
        rest = lattice.get_rest_count()
        im_rest.set_array(rest.T)
        
        S = coarse_grain_entropy(lattice, block_size)
        entropy_history.append(S)
        
        n_moving = lattice.total_moving()
        n_rest = lattice.total_rest()
        moving_history.append(n_moving)
        rest_history.append(n_rest)
        
        ax_entropy.clear()
        ax_entropy.plot(entropy_history, 'b-')
        ax_entropy.set_xlabel('Frame')
        ax_entropy.set_ylabel('Entropy')
        ax_entropy.set_title('Coarse-grained Entropy')
        ax_entropy.grid(True, alpha=0.3)
        
        ax_stats.clear()
        ax_stats.plot(moving_history, 'r-', label=f'Moving: {n_moving}')
        ax_stats.plot(rest_history, 'b-', label=f'Rest: {n_rest}')
        ax_stats.legend()
        ax_stats.set_xlabel('Frame')
        ax_stats.set_title(f'Energy Partition (total={n_moving + n_rest})')
        ax_stats.grid(True, alpha=0.3)
        
        return [im_density, im_rest]
    
    ani = animation.FuncAnimation(fig, update, frames=steps//5,
                                   interval=100, blit=False)
    plt.tight_layout()
    plt.show()


def save_frames(Lx=64, Ly=64, n_frames=50, steps_per_frame=10, output_dir='frames_fhp'):
    """Save visualization frames as PNG files."""
    import os
    os.makedirs(output_dir, exist_ok=True)
    
    print(f"FHP-III: Saving {n_frames} frames to {output_dir}/")
    
    lattice = FHPLattice(Lx, Ly, boundary='periodic')
    lattice.initialize_blob(Lx//4, Ly//3, Lx//5, density=0.6, rest_fraction=0.0)
    
    entropy_history = []
    moving_history = []
    rest_history = []
    
    for frame in range(n_frames):
        S = coarse_grain_entropy(lattice, block_size=8)
        n_moving = lattice.total_moving()
        n_rest = lattice.total_rest()
        
        entropy_history.append(S)
        moving_history.append(n_moving)
        rest_history.append(n_rest)
        
        fig, axes = plt.subplots(2, 2, figsize=(12, 10))
        fig.suptitle(f'FHP-III ({Lx}x{Ly}, t={lattice.time})', fontsize=14)
        
        density = lattice.get_density()
        im = axes[0, 0].imshow(density.T, origin='lower', cmap='hot',
                               vmin=0, vmax=7, interpolation='nearest')
        axes[0, 0].set_title('Particle Density')
        plt.colorbar(im, ax=axes[0, 0])
        
        rest = lattice.get_rest_count()
        im2 = axes[0, 1].imshow(rest.T, origin='lower', cmap='Greens',
                                vmin=0, vmax=1, interpolation='nearest')
        axes[0, 1].set_title('Rest Particles')
        plt.colorbar(im2, ax=axes[0, 1])
        
        axes[1, 0].plot(entropy_history, 'b-', linewidth=2)
        axes[1, 0].set_xlabel('Frame')
        axes[1, 0].set_ylabel('Entropy')
        axes[1, 0].set_title('Coarse-grained Entropy')
        axes[1, 0].grid(True, alpha=0.3)
        
        axes[1, 1].plot(moving_history, 'r-', label='Moving', linewidth=2)
        axes[1, 1].plot(rest_history, 'b-', label='Rest', linewidth=2)
        axes[1, 1].legend()
        axes[1, 1].set_xlabel('Frame')
        axes[1, 1].set_title('Energy Partition')
        axes[1, 1].grid(True, alpha=0.3)
        
        plt.tight_layout()
        plt.savefig(f'{output_dir}/frame_{frame:04d}.png', dpi=100)
        plt.close()
        
        for _ in range(steps_per_frame):
            lattice.step()
        
        if frame % 10 == 0:
            print(f"  Frame {frame}/{n_frames}: t={lattice.time}, S={S:.1f}")
    
    print(f"Done! Frames saved to {output_dir}/")


def run_test():
    """Quick test to verify physics."""
    print("FHP-III Lattice Gas Test")
    print("=" * 50)
    
    lattice = FHPLattice(32, 32, boundary='periodic')
    lattice.initialize_blob(16, 16, 8, density=0.5, rest_fraction=0.0)
    
    n0 = lattice.total_particles()
    p0 = lattice.total_momentum()
    
    print(f"Initial: particles={n0}, momentum={p0}")
    
    # Run for a while
    for t in range(100):
        lattice.step()
    
    n1 = lattice.total_particles()
    p1 = lattice.total_momentum()
    
    print(f"After 100 steps: particles={n1}, momentum={p1}")
    
    # Check conservation
    particles_ok = (n0 == n1)
    momentum_ok = (p0 == p1)
    
    print(f"\nParticle conservation: {particles_ok}")
    print(f"Momentum conservation: {momentum_ok}")
    
    # Check entropy increase
    lattice2 = FHPLattice(32, 32, boundary='periodic')
    lattice2.initialize_blob(8, 8, 5, density=0.7, rest_fraction=0.0)
    
    S0 = coarse_grain_entropy(lattice2)
    for _ in range(200):
        lattice2.step()
    S1 = coarse_grain_entropy(lattice2)
    
    print(f"\nEntropy test:")
    print(f"  Initial: {S0:.2f}")
    print(f"  Final:   {S1:.2f}")
    print(f"  Increased: {S1 > S0}")
    
    if particles_ok and momentum_ok:
        print("\n✓ All tests passed!")
    else:
        print("\n✗ Some tests failed!")


if __name__ == "__main__":
    import sys
    
    Lx = Ly = 64
    args = sys.argv[1:]
    
    if '--test' in args:
        run_test()
        sys.exit(0)
    
    if '--save' in args:
        args.remove('--save')
        if args:
            try:
                Lx = Ly = int(args[0])
            except ValueError:
                pass
        save_frames(Lx=Lx, Ly=Ly, n_frames=50, steps_per_frame=10)
        sys.exit(0)
    
    if args:
        try:
            Lx = Ly = int(args[0])
        except ValueError:
            print(f"Usage: {sys.argv[0]} [size] [--test] [--save]")
            sys.exit(1)
    
    run_visualization(Lx=Lx, Ly=Ly, steps=500)
