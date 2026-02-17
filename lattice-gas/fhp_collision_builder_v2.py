# Authored or modified by Claude
# Version: 2024-12-31 v2.0 - FHP-III Collision Table Builder (Fixed)

"""
FHP-III Collision Table Builder - Corrected Version

The key insight: to get a bijection, we need to pair up states that map to each other.
We can't just arbitrarily change particle counts.

Correct FHP-III collision rules:
1. Symmetric triplets: 0+2+4 <-> 1+3+5 (always swap)
2. Head-on pairs: rotate by parity (0+3 -> 1+4 or 2+5)
3. With rest spectator: same rotation rules apply
4. Four-particle states: various swaps
5. Everything else: identity

For REST PARTICLE involvement in FHP-III:
- Rest can be involved in 4-body collisions
- Key: 2 head-on + 2 rest (if we had 2 rest bits) - but we only have 1 rest bit
- With 1 rest bit: Rest acts as catalyst/spectator in most collisions

Actually, the FHP-III rest particle enables different collision outcomes,
but doesn't change particle count. Let me implement this correctly.
"""

import numpy as np
from collections import Counter

# Direction indices
E, NE, NW, W, SW, SE, R = 0, 1, 2, 3, 4, 5, 6
DIR_NAMES = ['E', 'NE', 'NW', 'W', 'SW', 'SE', 'R']

# Integer velocity vectors 
VELOCITY = [
    (2, 0),    # 0: E
    (1, 1),    # 1: NE  
    (-1, 1),   # 2: NW
    (-2, 0),   # 3: W
    (-1, -1),  # 4: SW
    (1, -1),   # 5: SE
    (0, 0),    # 6: R (rest)
]


def bits_to_list(state):
    return [d for d in range(7) if state & (1 << d)]

def list_to_bits(dirs):
    state = 0
    for d in dirs:
        state |= (1 << d)
    return state

def state_to_str(state):
    dirs = bits_to_list(state)
    if not dirs:
        return '∅'
    return '+'.join(DIR_NAMES[d] for d in dirs)

def count_particles(state):
    return bin(state).count('1')

def momentum(state):
    px, py = 0, 0
    for d in range(7):
        if state & (1 << d):
            px += VELOCITY[d][0]
            py += VELOCITY[d][1]
    return (px, py)


def find_momentum_conserving_partners(state):
    """
    Find all states with same particle count and momentum as input.
    These are candidates for collision outputs.
    """
    n = count_particles(state)
    p = momentum(state)
    partners = []
    for s in range(128):
        if count_particles(s) == n and momentum(s) == p:
            partners.append(s)
    return partners


def build_collision_table_systematic(parity):
    """
    Build collision table by finding valid swaps.
    
    Strategy:
    1. Group states by (particle_count, momentum)
    2. Within each group, define swaps that form a bijection
    3. Use parity to choose between options when multiple exist
    """
    table = list(range(128))  # Start with identity
    used = [False] * 128  # Track which states have been assigned
    
    # === Rule 1: Symmetric triplets (no rest) ===
    # 0+2+4 (21) <-> 1+3+5 (42)
    triplet_a = list_to_bits([0, 2, 4])  # 1 + 4 + 16 = 21
    triplet_b = list_to_bits([1, 3, 5])  # 2 + 8 + 32 = 42
    table[triplet_a] = triplet_b
    table[triplet_b] = triplet_a
    used[triplet_a] = used[triplet_b] = True
    
    # With rest: 0+2+4+R (85) <-> 1+3+5+R (106)
    triplet_a_r = triplet_a | (1 << R)  # 21 + 64 = 85
    triplet_b_r = triplet_b | (1 << R)  # 42 + 64 = 106
    table[triplet_a_r] = triplet_b_r
    table[triplet_b_r] = triplet_a_r
    used[triplet_a_r] = used[triplet_b_r] = True
    
    # === Rule 2: Head-on pairs (2-body) ===
    # These form 3-cycles: 0+3 -> 1+4 -> 2+5 -> 0+3
    # At even parity: go clockwise (0+3 -> 1+4 -> 2+5 -> 0+3)
    # At odd parity: go counter-clockwise (0+3 -> 2+5 -> 1+4 -> 0+3)
    
    pair_03 = list_to_bits([0, 3])  # 1 + 8 = 9 (E+W)
    pair_14 = list_to_bits([1, 4])  # 2 + 16 = 18 (NE+SW)
    pair_25 = list_to_bits([2, 5])  # 4 + 32 = 36 (NW+SE)
    
    if parity == 0:  # Clockwise
        table[pair_03] = pair_14
        table[pair_14] = pair_25
        table[pair_25] = pair_03
    else:  # Counter-clockwise
        table[pair_03] = pair_25
        table[pair_25] = pair_14
        table[pair_14] = pair_03
    
    used[pair_03] = used[pair_14] = used[pair_25] = True
    
    # Same with rest spectator
    pair_03_r = pair_03 | (1 << R)  # 9 + 64 = 73
    pair_14_r = pair_14 | (1 << R)  # 18 + 64 = 82
    pair_25_r = pair_25 | (1 << R)  # 36 + 64 = 100
    
    if parity == 0:
        table[pair_03_r] = pair_14_r
        table[pair_14_r] = pair_25_r
        table[pair_25_r] = pair_03_r
    else:
        table[pair_03_r] = pair_25_r
        table[pair_25_r] = pair_14_r
        table[pair_14_r] = pair_03_r
    
    used[pair_03_r] = used[pair_14_r] = used[pair_25_r] = True
    
    # === Rule 3: Head-on pair + non-opposite single (3-body, not symmetric) ===
    # Example: E+W+NE: momentum = (1,1)
    # Other 3-particle states with momentum (1,1): NE alone? No, that's 1 particle.
    # What about E+W+NE <-> ? 
    # 
    # Actually, for 3-body non-symmetric states, there may not be another state
    # to swap with! Let's check...
    #
    # E+W+NE = 0+3+1 = 1+8+2 = 11, momentum = (1,1)
    # What other 3-particle states have momentum (1,1)?
    
    # Let me enumerate all 3-particle states with momentum (1,1):
    # - 0+1+3 = E+NE+W: (2,0)+(1,1)+(-2,0) = (1,1) ✓
    # - 0+1+4 = E+NE+SW: (2,0)+(1,1)+(-1,-1) = (2,0) ✗
    # - 0+2+5 = E+NW+SE: (2,0)+(-1,1)+(1,-1) = (2,0) ✗
    # - 1+2+5 = NE+NW+SE: (1,1)+(-1,1)+(1,-1) = (1,1) ✓
    # - 1+4+5 = NE+SW+SE: (1,1)+(-1,-1)+(1,-1) = (1,-1) ✗
    # - others...
    #
    # So 0+1+3 (11) and 1+2+5 (38) both have momentum (1,1)
    # These could swap! Let me verify:
    # 0+1+3 = 1+2+8 = 11
    # 1+2+5 = 2+4+32 = 38
    
    # Let me systematically find all such pairs...
    
    # Group all states by (n_particles, momentum)
    groups = {}
    for s in range(128):
        key = (count_particles(s), momentum(s))
        if key not in groups:
            groups[key] = []
        groups[key].append(s)
    
    # For each group, pair up unused states
    for key, states in groups.items():
        unused_in_group = [s for s in states if not used[s]]
        
        # For groups with exactly 2 unused states, swap them
        if len(unused_in_group) == 2:
            a, b = unused_in_group
            table[a] = b
            table[b] = a
            used[a] = used[b] = True
        
        # For groups with 3 unused states, make a 3-cycle (depends on parity)
        elif len(unused_in_group) == 3:
            a, b, c = sorted(unused_in_group)
            if parity == 0:
                table[a] = b
                table[b] = c
                table[c] = a
            else:
                table[a] = c
                table[c] = b
                table[b] = a
            used[a] = used[b] = used[c] = True
        
        # For larger groups, we'd need more complex logic
        # For now, leave as identity (they stay unused)
        elif len(unused_in_group) > 3:
            # Try to pair them up in twos
            for i in range(0, len(unused_in_group) - 1, 2):
                a, b = unused_in_group[i], unused_in_group[i+1]
                table[a] = b
                table[b] = a
                used[a] = used[b] = True
    
    return np.array(table, dtype=np.uint8)


def verify_table(table, name):
    """Verify collision table properties."""
    print(f"\n{'='*60}")
    print(f"Verifying {name}")
    print('='*60)
    
    # Check bijection
    outputs = list(table)
    is_bijection = len(set(outputs)) == 128
    print(f"Bijection: {is_bijection}")
    
    if not is_bijection:
        counts = Counter(outputs)
        for out, cnt in counts.most_common(5):
            if cnt > 1:
                inputs = [s for s in range(128) if table[s] == out]
                print(f"  {state_to_str(out)} <- {[state_to_str(i) for i in inputs]}")
    
    # Check particle conservation
    particle_violations = []
    for state in range(128):
        if count_particles(state) != count_particles(table[state]):
            particle_violations.append((state, table[state]))
    print(f"Particle conservation: {len(particle_violations) == 0}")
    for s_in, s_out in particle_violations[:3]:
        print(f"  {state_to_str(s_in)} ({count_particles(s_in)}) -> {state_to_str(s_out)} ({count_particles(s_out)})")
    
    # Check momentum conservation
    momentum_violations = []
    for state in range(128):
        if momentum(state) != momentum(table[state]):
            momentum_violations.append((state, table[state]))
    print(f"Momentum conservation: {len(momentum_violations) == 0}")
    for s_in, s_out in momentum_violations[:3]:
        print(f"  {state_to_str(s_in)} {momentum(s_in)} -> {state_to_str(s_out)} {momentum(s_out)}")
    
    # Count non-trivial rules
    nontrivial = sum(1 for s in range(128) if table[s] != s)
    print(f"Non-trivial rules: {nontrivial}")
    
    return is_bijection and len(particle_violations) == 0 and len(momentum_violations) == 0


def print_nontrivial(table, name, max_print=30):
    """Print non-identity rules."""
    print(f"\n{name} non-trivial rules:")
    count = 0
    for state in range(128):
        if table[state] != state:
            print(f"  {state_to_str(state):20} -> {state_to_str(table[state])}")
            count += 1
            if count >= max_print:
                print(f"  ... and more")
                break
    print(f"Total: {sum(1 for s in range(128) if table[s] != s)} non-trivial rules")


def main():
    print("Building FHP-III collision tables (systematic approach)...")
    
    table_even = build_collision_table_systematic(parity=0)
    table_odd = build_collision_table_systematic(parity=1)
    
    valid_even = verify_table(table_even, "EVEN sites")
    valid_odd = verify_table(table_odd, "ODD sites")
    
    if valid_even and valid_odd:
        print("\n" + "="*60)
        print("SUCCESS: Both tables are valid!")
        print("="*60)
        
        print_nontrivial(table_even, "EVEN")
        print_nontrivial(table_odd, "ODD")
        
        print("\n# Collision tables:")
        print(f"COLLISION_TABLE_EVEN = np.array({list(table_even)}, dtype=np.uint8)")
        print(f"\nCOLLISION_TABLE_ODD = np.array({list(table_odd)}, dtype=np.uint8)")
    else:
        print("\nERROR: Tables have issues!")


if __name__ == "__main__":
    main()
