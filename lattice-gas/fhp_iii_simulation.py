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
    python fhp_iii_simulation.py [size] [--test] [--save] [--gradient] [--vortex] [--batch]

Modes:
    (default)   Interactive visualization with blob initialization
    --test      Quick physics verification
    --save      Save frames to disk
    --gradient  Gradient mode: inject particles left, absorb right
    --vortex    Vortex experiment: obstacle in flow, measure EPR and vorticity
    --batch     Headless batch mode: fast, saves snapshots (e.g., --batch 256 10000)
"""

import numpy as np
import matplotlib.pyplot as plt
import matplotlib.animation as animation

# Try to import numba for performance
try:
    from numba import njit, prange
    NUMBA_AVAILABLE = True
    print("Numba available - using parallel acceleration")
except ImportError:
    NUMBA_AVAILABLE = False
    print("Numba not available - using pure Python (slower)")
    # Provide dummy decorators
    def njit(*args, **kwargs):
        def decorator(func):
            return func
        return decorator
    def prange(x):
        return range(x)

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


# Numba-accelerated streaming kernel
@njit(parallel=True, cache=True)
def _streaming_kernel_gradient(state, obstacles, Lx, Ly, neighbors_even, neighbors_odd):
    """
    JIT-compiled streaming step for gradient boundary mode.
    Parallel over y (rows) for best cache utilization.
    """
    new_state = np.zeros((Lx, Ly), dtype=np.uint8)

    # Process each direction
    for d in range(6):
        opp_d = (d + 3) % 6
        bit_mask = np.uint8(1 << d)
        opp_mask = np.uint8(1 << opp_d)

        # Parallel over y
        for y in prange(Ly):
            if y % 2 == 0:
                dx, dy = neighbors_even[d, 0], neighbors_even[d, 1]
            else:
                dx, dy = neighbors_odd[d, 0], neighbors_odd[d, 1]

            for x in range(Lx):
                if state[x, y] & bit_mask:
                    # Target position
                    nx = x + dx
                    ny = (y + dy) % Ly  # Periodic in y

                    if 0 <= nx < Lx:
                        if obstacles[nx, ny]:
                            # Bounce back from obstacle
                            new_state[x, y] |= opp_mask
                        else:
                            new_state[nx, ny] |= bit_mask
                    else:
                        # Bounce back from x walls
                        new_state[x, y] |= opp_mask

    # Rest particles don't move
    for y in prange(Ly):
        for x in range(Lx):
            if state[x, y] & np.uint8(64):  # bit 6 = rest particle
                new_state[x, y] |= np.uint8(64)

    return new_state


@njit(parallel=True, cache=True)
def _streaming_kernel_periodic(state, obstacles, Lx, Ly, neighbors_even, neighbors_odd):
    """JIT-compiled streaming step for periodic boundary mode."""
    new_state = np.zeros((Lx, Ly), dtype=np.uint8)

    for d in range(6):
        opp_d = (d + 3) % 6
        bit_mask = np.uint8(1 << d)
        opp_mask = np.uint8(1 << opp_d)

        for y in prange(Ly):
            if y % 2 == 0:
                dx, dy = neighbors_even[d, 0], neighbors_even[d, 1]
            else:
                dx, dy = neighbors_odd[d, 0], neighbors_odd[d, 1]

            for x in range(Lx):
                if state[x, y] & bit_mask:
                    nx = (x + dx) % Lx
                    ny = (y + dy) % Ly

                    if obstacles[nx, ny]:
                        new_state[x, y] |= opp_mask
                    else:
                        new_state[nx, ny] |= bit_mask

    # Rest particles
    for y in prange(Ly):
        for x in range(Lx):
            if state[x, y] & np.uint8(64):
                new_state[x, y] |= np.uint8(64)

    return new_state


class FHPLattice:
    """FHP-III Lattice Gas Simulation."""
    
    def __init__(self, Lx, Ly, boundary='periodic', p_inject=0.0, p_absorb=0.0):
        """
        Initialize lattice.

        Args:
            Lx: Width (number of sites in x)
            Ly: Height (number of sites in y)
            boundary: 'periodic', 'walls', or 'gradient'
            p_inject: Probability to inject eastward particle at left boundary (gradient mode)
            p_absorb: Probability to absorb particles at right boundary (gradient mode)
        """
        self.Lx = Lx
        self.Ly = Ly
        self.boundary = boundary
        self.p_inject = p_inject
        self.p_absorb = p_absorb

        # State array: 7 bits per site packed into uint8
        self.state = np.zeros((Lx, Ly), dtype=np.uint8)

        # Pre-compute parity mask
        x_idx, y_idx = np.meshgrid(np.arange(Lx), np.arange(Ly), indexing='ij')
        self.parity = (x_idx + y_idx) % 2

        # Pre-compute row parity for streaming
        self.row_parity = np.arange(Ly) % 2

        # Track particles injected/absorbed for statistics
        self.particles_injected = 0
        self.particles_absorbed = 0

        # Obstacle mask (True = obstacle site, particles bounce back)
        self.obstacles = np.zeros((Lx, Ly), dtype=bool)

        # For EPR calculation: track per-step statistics
        self.last_injected = 0
        self.last_absorbed = 0

        self.time = 0

    def add_obstacle_circle(self, cx, cy, radius):
        """Add a circular obstacle centered at (cx, cy)."""
        for x in range(self.Lx):
            for y in range(self.Ly):
                if (x - cx)**2 + (y - cy)**2 <= radius**2:
                    self.obstacles[x, y] = True
                    self.state[x, y] = 0  # Clear any particles

    def add_obstacle_rect(self, x0, y0, width, height):
        """Add a rectangular obstacle."""
        x1 = min(x0 + width, self.Lx)
        y1 = min(y0 + height, self.Ly)
        self.obstacles[x0:x1, y0:y1] = True
        self.state[x0:x1, y0:y1] = 0
    
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

    def get_density_profile(self):
        """Get average density at each x position (useful for gradient visualization)."""
        density = self.get_density()
        return density.mean(axis=1)  # Average over y

    def get_momentum(self):
        """Get momentum field at each site."""
        px = np.zeros((self.Lx, self.Ly), dtype=np.int32)
        py = np.zeros((self.Lx, self.Ly), dtype=np.int32)
        for d in range(7):
            mask = (self.state >> d) & 1
            px += mask * VELOCITY[d, 0]
            py += mask * VELOCITY[d, 1]
        return px, py

    def get_vorticity(self):
        """
        Calculate vorticity (curl of velocity field).

        ω = ∂v_y/∂x - ∂v_x/∂y

        Positive = counterclockwise rotation
        Negative = clockwise rotation
        """
        px, py = self.get_momentum()
        # Convert to float for derivatives
        vx = px.astype(float)
        vy = py.astype(float)

        # Finite differences (central difference where possible)
        dvx_dy = np.zeros_like(vx)
        dvy_dx = np.zeros_like(vy)

        # ∂v_y/∂x
        dvy_dx[1:-1, :] = (vy[2:, :] - vy[:-2, :]) / 2.0
        dvy_dx[0, :] = vy[1, :] - vy[0, :]
        dvy_dx[-1, :] = vy[-1, :] - vy[-2, :]

        # ∂v_x/∂y
        dvx_dy[:, 1:-1] = (vx[:, 2:] - vx[:, :-2]) / 2.0
        dvx_dy[:, 0] = vx[:, 1] - vx[:, 0]
        dvx_dy[:, -1] = vx[:, -1] - vx[:, -2]

        vorticity = dvy_dx - dvx_dy
        return vorticity

    def get_epr(self):
        """
        Calculate entropy production rate (EPR).

        EPR ≈ throughput_rate × density_gradient

        In steady-state non-equilibrium thermodynamics:
        - Throughput = particles flowing through the system per timestep
        - Gradient = density difference (left - right)

        Returns (epr, throughput_rate, gradient)
        """
        # Throughput: rate of particles absorbed (= injected in steady state)
        throughput_rate = self.particles_absorbed - self.last_absorbed

        # Density gradient: left side vs right side
        profile = self.get_density_profile()
        n_edge = max(1, self.Lx // 8)
        density_left = profile[:n_edge].mean()
        density_right = profile[-n_edge:].mean()
        gradient = density_left - density_right

        # EPR ≈ flux × gradient (thermodynamic force)
        epr = throughput_rate * gradient

        return epr, throughput_rate, gradient

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

    def boundary_driving_step(self):
        """
        Apply boundary driving for gradient mode.

        - Left boundary (x=0): Inject eastward particles with probability p_inject
        - Right boundary (x=Lx-1): Absorb particles with probability p_absorb

        This creates a steady-state density gradient that can drive "work".
        """
        if self.boundary != 'gradient':
            return

        # Left boundary: inject eastward (E) particles
        for y in range(self.Ly):
            # Only inject if there's no E particle already
            if not (self.state[0, y] & (1 << E)):
                if np.random.random() < self.p_inject:
                    self.state[0, y] |= (1 << E)
                    self.particles_injected += 1

        # Right boundary: absorb particles
        for y in range(self.Ly):
            site_state = int(self.state[self.Lx - 1, y])
            if site_state != 0:
                # Absorb each particle with probability p_absorb
                new_state = site_state
                for d in range(7):
                    if site_state & (1 << d):
                        if np.random.random() < self.p_absorb:
                            new_state ^= (1 << d)  # XOR to clear bit
                            self.particles_absorbed += 1
                self.state[self.Lx - 1, y] = np.uint8(new_state)

    def collision_step(self):
        """Apply collision rules via parity-dependent lookup."""
        even_result = COLLISION_TABLE_EVEN[self.state]
        odd_result = COLLISION_TABLE_ODD[self.state]
        self.state = np.where(self.parity == 0, even_result, odd_result)
    
    def streaming_step(self):
        """Move particles to neighboring sites on hexagonal lattice."""
        # Use numba-accelerated kernel if available
        if NUMBA_AVAILABLE:
            if self.boundary == 'gradient':
                self.state = _streaming_kernel_gradient(
                    self.state, self.obstacles, self.Lx, self.Ly,
                    NEIGHBORS_EVEN, NEIGHBORS_ODD
                )
            elif self.boundary == 'periodic':
                self.state = _streaming_kernel_periodic(
                    self.state, self.obstacles, self.Lx, self.Ly,
                    NEIGHBORS_EVEN, NEIGHBORS_ODD
                )
            else:
                # Fallback for 'walls' mode (less common)
                self._streaming_step_python()
        else:
            self._streaming_step_python()

    def _streaming_step_python(self):
        """Pure Python streaming step (fallback when numba unavailable)."""
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
                        nx = x + dx
                        ny = y + dy

                        if self.boundary == 'periodic':
                            nx = nx % self.Lx
                            ny = ny % self.Ly
                            # Check for obstacle at target
                            if self.obstacles[nx, ny]:
                                opp_d = (d + 3) % 6
                                new_state[x, y] |= (1 << opp_d)
                            else:
                                shifted[nx, ny] = 1
                        elif self.boundary == 'walls':
                            # Bounce back at all walls
                            if 0 <= nx < self.Lx and 0 <= ny < self.Ly:
                                if self.obstacles[nx, ny]:
                                    opp_d = (d + 3) % 6
                                    new_state[x, y] |= (1 << opp_d)
                                else:
                                    shifted[nx, ny] = 1
                            else:
                                # Reflect: particle stays at (x,y) but reverses direction
                                opp_d = (d + 3) % 6  # Opposite direction
                                new_state[x, y] |= (1 << opp_d)
                        elif self.boundary == 'gradient':
                            # Gradient mode: periodic in y, walls in x
                            ny = ny % self.Ly  # Periodic in y
                            if 0 <= nx < self.Lx:
                                if self.obstacles[nx, ny]:
                                    opp_d = (d + 3) % 6
                                    new_state[x, y] |= (1 << opp_d)
                                else:
                                    shifted[nx, ny] = 1
                            else:
                                # Bounce back at left/right walls
                                opp_d = (d + 3) % 6
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
        # Track for EPR calculation
        self.last_injected = self.particles_injected
        self.last_absorbed = self.particles_absorbed

        self.collision_step()
        self.streaming_step()  # Use non-vectorized for correctness
        self.boundary_driving_step()  # Apply injection/absorption for gradient mode
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


def run_gradient(Lx=64, Ly=64, steps=500, p_inject=0.3, p_absorb=0.5):
    """
    Run simulation with gradient boundary driving.

    Particles are injected on the left (moving East) and absorbed on the right,
    creating a steady-state density gradient.
    """
    print(f"Creating FHP-III lattice with gradient: {Lx}x{Ly}")
    print(f"  p_inject={p_inject}, p_absorb={p_absorb}")

    lattice = FHPLattice(Lx, Ly, boundary='gradient',
                         p_inject=p_inject, p_absorb=p_absorb)
    # Start with low uniform density
    lattice.initialize_uniform(density=0.1, rest_fraction=0.05)

    print(f"Initial particles: {lattice.total_particles()}")

    fig, axes = plt.subplots(2, 2, figsize=(12, 10))
    fig.suptitle(f'FHP-III Gradient Mode ({Lx}x{Ly})', fontsize=14)

    # Density plot
    ax_density = axes[0, 0]
    density = lattice.get_density()
    im_density = ax_density.imshow(density.T, origin='lower', cmap='hot',
                                    vmin=0, vmax=7, interpolation='nearest')
    ax_density.set_title('Particle Density')
    ax_density.set_xlabel('x (left=inject, right=absorb)')
    plt.colorbar(im_density, ax=ax_density)

    # Density profile (x-averaged)
    ax_profile = axes[0, 1]
    profile = lattice.get_density_profile()
    line_profile, = ax_profile.plot(profile, 'b-', linewidth=2)
    ax_profile.set_xlim(0, Lx)
    ax_profile.set_ylim(0, 4)
    ax_profile.set_xlabel('x position')
    ax_profile.set_ylabel('Average density')
    ax_profile.set_title('Density Profile (should show gradient)')
    ax_profile.grid(True, alpha=0.3)

    # Particle count history
    ax_count = axes[1, 0]
    total_history = []
    inject_history = []
    absorb_history = []

    # Net flux
    ax_flux = axes[1, 1]
    flux_history = []

    def update(frame):
        for _ in range(5):
            lattice.step()

        density = lattice.get_density()
        im_density.set_array(density.T)
        ax_density.set_title(f'Particle Density (t={lattice.time})')

        profile = lattice.get_density_profile()
        line_profile.set_ydata(profile)

        total_history.append(lattice.total_particles())
        inject_history.append(lattice.particles_injected)
        absorb_history.append(lattice.particles_absorbed)

        ax_count.clear()
        ax_count.plot(total_history, 'k-', label=f'Total: {total_history[-1]}')
        ax_count.plot(inject_history, 'g-', label=f'Injected: {inject_history[-1]}')
        ax_count.plot(absorb_history, 'r-', label=f'Absorbed: {absorb_history[-1]}')
        ax_count.legend(loc='upper left')
        ax_count.set_xlabel('Frame')
        ax_count.set_ylabel('Count')
        ax_count.set_title('Particle Statistics')
        ax_count.grid(True, alpha=0.3)

        # Net flux = injected - absorbed
        net_flux = lattice.particles_injected - lattice.particles_absorbed
        flux_history.append(net_flux)

        ax_flux.clear()
        ax_flux.plot(flux_history, 'b-', linewidth=2)
        ax_flux.axhline(y=0, color='k', linestyle='--', alpha=0.5)
        ax_flux.set_xlabel('Frame')
        ax_flux.set_ylabel('Net flux (injected - absorbed)')
        ax_flux.set_title('Net Particle Flux')
        ax_flux.grid(True, alpha=0.3)

        return [im_density, line_profile]

    ani = animation.FuncAnimation(fig, update, frames=steps//5,
                                   interval=100, blit=False)
    plt.tight_layout()
    plt.show()


def run_vortex(Lx=128, Ly=64, steps=1000, p_inject=0.8, p_absorb=0.15):
    """
    Run simulation with obstacle to generate vortices (von Kármán street).

    Tests morphodynamic hypothesis: do flow structures increase EPR?
    """
    print(f"Creating FHP-III vortex experiment: {Lx}x{Ly}")
    print(f"  p_inject={p_inject}, p_absorb={p_absorb}")

    lattice = FHPLattice(Lx, Ly, boundary='gradient',
                         p_inject=p_inject, p_absorb=p_absorb)

    # Add flat plate obstacle (perpendicular to flow) - creates sharper separation
    obstacle_x = Lx // 4
    plate_height = Ly // 3
    plate_y0 = (Ly - plate_height) // 2
    lattice.add_obstacle_rect(obstacle_x, plate_y0, 3, plate_height)  # thin plate
    print(f"  Flat plate at x={obstacle_x}, y={plate_y0} to {plate_y0 + plate_height}")

    # Start with higher density for lower effective viscosity
    lattice.initialize_uniform(density=0.35, rest_fraction=0.05)

    print(f"Initial particles: {lattice.total_particles()}")

    fig, axes = plt.subplots(2, 3, figsize=(16, 10))
    fig.suptitle(f'FHP-III Vortex Experiment ({Lx}x{Ly})', fontsize=14)

    # Density plot
    ax_density = axes[0, 0]
    density = lattice.get_density()
    # Mask obstacles for display
    density_display = np.ma.masked_where(lattice.obstacles, density)
    im_density = ax_density.imshow(density_display.T, origin='lower', cmap='hot',
                                    vmin=0, vmax=5, interpolation='nearest')
    ax_density.set_title('Particle Density')
    plt.colorbar(im_density, ax=ax_density)

    # Vorticity plot
    ax_vorticity = axes[0, 1]
    vorticity = lattice.get_vorticity()
    vort_display = np.ma.masked_where(lattice.obstacles, vorticity)
    im_vorticity = ax_vorticity.imshow(vort_display.T, origin='lower', cmap='RdBu_r',
                                        vmin=-2, vmax=2, interpolation='nearest')
    ax_vorticity.set_title('Vorticity (red=CW, blue=CCW)')
    plt.colorbar(im_vorticity, ax=ax_vorticity)

    # Density profile
    ax_profile = axes[0, 2]
    profile = lattice.get_density_profile()
    line_profile, = ax_profile.plot(profile, 'b-', linewidth=2)
    ax_profile.set_xlim(0, Lx)
    ax_profile.set_ylim(0, 5)
    ax_profile.axvline(x=obstacle_x, color='gray', linestyle='--', alpha=0.5, label='plate')
    ax_profile.set_xlabel('x position')
    ax_profile.set_ylabel('Average density')
    ax_profile.set_title('Density Profile')
    ax_profile.grid(True, alpha=0.3)

    # EPR history
    ax_epr = axes[1, 0]
    epr_history = []

    # Throughput history
    ax_throughput = axes[1, 1]
    throughput_history = []

    # Total vorticity (absolute)
    ax_vort_total = axes[1, 2]
    vort_total_history = []

    def update(frame):
        for _ in range(5):
            lattice.step()

        # Update density
        density = lattice.get_density()
        density_display = np.ma.masked_where(lattice.obstacles, density)
        im_density.set_array(density_display.T)
        ax_density.set_title(f'Particle Density (t={lattice.time})')

        # Update vorticity
        vorticity = lattice.get_vorticity()
        vort_display = np.ma.masked_where(lattice.obstacles, vorticity)
        im_vorticity.set_array(vort_display.T)

        # Update profile
        profile = lattice.get_density_profile()
        line_profile.set_ydata(profile)

        # Calculate EPR
        epr, throughput, gradient = lattice.get_epr()
        epr_history.append(epr)
        throughput_history.append(throughput)

        # Total absolute vorticity (measure of rotation in system)
        vort_total = np.abs(vorticity[~lattice.obstacles]).sum()
        vort_total_history.append(vort_total)

        # Plot EPR
        ax_epr.clear()
        ax_epr.plot(epr_history, 'g-', linewidth=2)
        ax_epr.set_xlabel('Frame')
        ax_epr.set_ylabel('EPR (throughput × gradient)')
        ax_epr.set_title(f'Entropy Production Rate: {epr:.1f}')
        ax_epr.grid(True, alpha=0.3)

        # Plot throughput
        ax_throughput.clear()
        ax_throughput.plot(throughput_history, 'r-', linewidth=2)
        ax_throughput.set_xlabel('Frame')
        ax_throughput.set_ylabel('Throughput (particles/step)')
        ax_throughput.set_title(f'Throughput: {throughput:.0f}')
        ax_throughput.grid(True, alpha=0.3)

        # Plot total vorticity
        ax_vort_total.clear()
        ax_vort_total.plot(vort_total_history, 'purple', linewidth=2)
        ax_vort_total.set_xlabel('Frame')
        ax_vort_total.set_ylabel('Total |vorticity|')
        ax_vort_total.set_title(f'Total Rotation: {vort_total:.0f}')
        ax_vort_total.grid(True, alpha=0.3)

        return [im_density, im_vorticity, line_profile]

    ani = animation.FuncAnimation(fig, update, frames=steps//5,
                                   interval=100, blit=False)
    plt.tight_layout()
    plt.show()


def run_vortex_batch(Lx=512, Ly=256, total_steps=10000, snapshot_interval=500,
                     p_inject=0.8, p_absorb=0.15, output_dir='vortex_batch'):
    """
    Run vortex experiment in batch mode (no live visualization).

    Saves snapshots every snapshot_interval steps for later analysis.
    Much faster than live visualization.
    """
    import os
    os.makedirs(output_dir, exist_ok=True)

    print(f"FHP-III Vortex Batch Experiment")
    print(f"  Lattice: {Lx}x{Ly}")
    print(f"  Steps: {total_steps}, snapshots every {snapshot_interval}")
    print(f"  p_inject={p_inject}, p_absorb={p_absorb}")
    print(f"  Output: {output_dir}/")

    lattice = FHPLattice(Lx, Ly, boundary='gradient',
                         p_inject=p_inject, p_absorb=p_absorb)

    # Add flat plate obstacle
    obstacle_x = Lx // 4
    plate_height = Ly // 3
    plate_y0 = (Ly - plate_height) // 2
    lattice.add_obstacle_rect(obstacle_x, plate_y0, 3, plate_height)
    print(f"  Obstacle: plate at x={obstacle_x}")

    # Higher density for lower viscosity
    lattice.initialize_uniform(density=0.4, rest_fraction=0.05)

    print(f"  Initial particles: {lattice.total_particles()}")
    print()

    # Track history for final plots
    epr_history = []
    throughput_history = []
    vort_history = []
    time_history = []

    import time
    start_time = time.time()

    snapshot_num = 0
    for step in range(total_steps):
        lattice.step()

        # Record stats every step for smooth curves
        if step % 10 == 0:
            epr, throughput, gradient = lattice.get_epr()
            vorticity = lattice.get_vorticity()
            vort_total = np.abs(vorticity[~lattice.obstacles]).sum()
            epr_history.append(epr)
            throughput_history.append(throughput)
            vort_history.append(vort_total)
            time_history.append(lattice.time)

        # Save snapshot
        if step % snapshot_interval == 0:
            elapsed = time.time() - start_time
            steps_per_sec = (step + 1) / elapsed if elapsed > 0 else 0
            print(f"  Step {step}/{total_steps} ({steps_per_sec:.0f} steps/s) - saving snapshot...")

            fig, axes = plt.subplots(2, 3, figsize=(16, 10))
            fig.suptitle(f'FHP-III Vortex ({Lx}x{Ly}, t={lattice.time})', fontsize=14)

            # Density
            density = lattice.get_density()
            density_display = np.ma.masked_where(lattice.obstacles, density)
            im = axes[0, 0].imshow(density_display.T, origin='lower', cmap='hot',
                                   vmin=0, vmax=6, interpolation='nearest')
            axes[0, 0].set_title('Particle Density')
            plt.colorbar(im, ax=axes[0, 0])

            # Vorticity
            vorticity = lattice.get_vorticity()
            vort_display = np.ma.masked_where(lattice.obstacles, vorticity)
            im2 = axes[0, 1].imshow(vort_display.T, origin='lower', cmap='RdBu_r',
                                    vmin=-3, vmax=3, interpolation='nearest')
            axes[0, 1].set_title('Vorticity')
            plt.colorbar(im2, ax=axes[0, 1])

            # Density profile
            profile = lattice.get_density_profile()
            axes[0, 2].plot(profile, 'b-', linewidth=2)
            axes[0, 2].axvline(x=obstacle_x, color='gray', linestyle='--', alpha=0.5)
            axes[0, 2].set_xlim(0, Lx)
            axes[0, 2].set_ylim(0, 5)
            axes[0, 2].set_xlabel('x')
            axes[0, 2].set_ylabel('Density')
            axes[0, 2].set_title('Density Profile')
            axes[0, 2].grid(True, alpha=0.3)

            # EPR history
            axes[1, 0].plot(time_history, epr_history, 'g-', linewidth=1)
            axes[1, 0].set_xlabel('Time')
            axes[1, 0].set_ylabel('EPR')
            axes[1, 0].set_title('Entropy Production Rate')
            axes[1, 0].grid(True, alpha=0.3)

            # Throughput history
            axes[1, 1].plot(time_history, throughput_history, 'r-', linewidth=1)
            axes[1, 1].set_xlabel('Time')
            axes[1, 1].set_ylabel('Throughput')
            axes[1, 1].set_title('Throughput')
            axes[1, 1].grid(True, alpha=0.3)

            # Vorticity history
            axes[1, 2].plot(time_history, vort_history, 'purple', linewidth=1)
            axes[1, 2].set_xlabel('Time')
            axes[1, 2].set_ylabel('Total |vorticity|')
            axes[1, 2].set_title('Total Rotation')
            axes[1, 2].grid(True, alpha=0.3)

            plt.tight_layout()
            plt.savefig(f'{output_dir}/snapshot_{snapshot_num:04d}.png', dpi=120)
            plt.close()
            snapshot_num += 1

    elapsed = time.time() - start_time
    print(f"\nDone! {total_steps} steps in {elapsed:.1f}s ({total_steps/elapsed:.0f} steps/s)")
    print(f"Snapshots saved to {output_dir}/")


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

    if '--gradient' in args:
        args.remove('--gradient')
        if args:
            try:
                Lx = Ly = int(args[0])
            except ValueError:
                pass
        run_gradient(Lx=Lx, Ly=Ly, steps=500)
        sys.exit(0)

    if '--vortex' in args:
        args.remove('--vortex')
        Lx, Ly = 128, 64  # Default wider aspect for vortex street
        if args:
            try:
                size = int(args[0])
                Lx, Ly = size * 2, size  # Keep 2:1 aspect ratio
            except ValueError:
                pass
        run_vortex(Lx=Lx, Ly=Ly, steps=1000)
        sys.exit(0)

    if '--batch' in args:
        args.remove('--batch')
        Lx, Ly = 512, 256  # Default large size for batch
        total_steps = 10000
        if args:
            try:
                size = int(args[0])
                Lx, Ly = size * 2, size
                args = args[1:]
            except (ValueError, IndexError):
                pass
        if args:
            try:
                total_steps = int(args[0])
            except ValueError:
                pass
        run_vortex_batch(Lx=Lx, Ly=Ly, total_steps=total_steps)
        sys.exit(0)

    if args:
        try:
            Lx = Ly = int(args[0])
        except ValueError:
            print(f"Usage: {sys.argv[0]} [size] [--test] [--save] [--gradient] [--vortex] [--batch]")
            sys.exit(1)

    run_visualization(Lx=Lx, Ly=Ly, steps=500)
