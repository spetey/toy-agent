#!/usr/bin/env python3
"""
Verify the JZ dispatch block logic from the TC proof sketch.
Authored or modified by Claude
Version: 2025-02-01

We simulate the LOGIC of a JZ(A, L_zero=3, L_nonzero=5) block
for instruction k=2, testing both the A=0 and A≠0 cases.

This doesn't use the actual fb_reflect simulator — it traces the
abstract operations to verify correctness of the arm/act/disarm pattern
and the uncomputation.
"""

def trace_jz_block(active_k, A_val, k=2, L_zero=3, L_nonzero=5, n=6):
    """
    Simulate JZ(A, L_zero, L_nonzero) dispatch block for instruction k.
    
    State:
        current[0..n-1]: active flags (current[k-1] may be 1)
        next[0..n-1]: target flags (all start at 0)
        A: register value
        one_const: always 1
        cond_cell: scratch (starts at 0)
        scratch[0..2]: all start at 0
    """
    # Initialize state
    current = [0] * n
    current[k-1] = active_k  # 0 or 1
    
    next_flags = [0] * n
    A = A_val
    one_const = 1
    cond_cell = 0
    scratch = [0, 0, 0]
    
    print(f"=== JZ(A, L_zero={L_zero}, L_nonzero={L_nonzero}), "
          f"instruction k={k} ===")
    print(f"Initial: current[{k-1}]={active_k}, A={A_val}")
    print()
    
    # Save initial state for comparison
    init_scratch = (cond_cell, scratch[:], one_const)
    
    # ---- PHASE 1: Compute condition flags ----
    print("Phase 1: Compute conditions")
    
    # >. cond_cell += current[k-1]
    cond_cell += current[k-1]
    print(f"  cond_cell += current[{k-1}] → cond_cell = {cond_cell}")
    
    # >. scratch[0] += cond_cell (copy cond_active into scratch[0])
    scratch[0] += cond_cell
    print(f"  scratch[0] += cond_cell → scratch[0] = {scratch[0]}")
    
    # >G: CL → A (save CL to addr_A cell — abstractly, we just note CL target)
    # >F: if A ≠ 0: swap(scratch[0], scratch[1])
    print(f"  Fredkin(CL→A={A}, swap scratch[0]↔scratch[1]):")
    if A != 0:
        scratch[0], scratch[1] = scratch[1], scratch[0]
        print(f"    A≠0, swapped → scratch[0]={scratch[0]}, scratch[1]={scratch[1]}")
    else:
        print(f"    A=0, no swap → scratch[0]={scratch[0]}, scratch[1]={scratch[1]}")
    # >G: CL → cond_cell (restore)
    
    cond_zero = scratch[0]
    cond_nonzero = scratch[1]
    print(f"  → cond_zero={cond_zero}, cond_nonzero={cond_nonzero}")
    print()
    
    # ---- PHASE 2: Activate targets ----
    print("Phase 2: Activate targets")
    
    # Set next[L_zero-1] gated on cond_zero (scratch[0])
    # >G: CL → scratch[0]
    # >F(one_const, scratch[2]): if cond_zero ≠ 0: scratch[2] = 1
    if scratch[0] != 0:
        one_const, scratch[2] = scratch[2], one_const
    print(f"  Fredkin(CL→cond_zero={scratch[0]}): "
          f"one_const={one_const}, scratch[2]={scratch[2]}")
    
    # >. next[L_zero-1] += scratch[2]
    next_flags[L_zero-1] += scratch[2]
    print(f"  next[{L_zero-1}] += scratch[2] → next[{L_zero-1}]={next_flags[L_zero-1]}")
    
    # >F: undo
    if scratch[0] != 0:
        one_const, scratch[2] = scratch[2], one_const
    print(f"  Undo Fredkin: one_const={one_const}, scratch[2]={scratch[2]}")
    # >G: CL → cond_cell
    
    # Set next[L_nonzero-1] gated on cond_nonzero (scratch[1])
    # >G: CL → scratch[1]
    if scratch[1] != 0:
        one_const, scratch[2] = scratch[2], one_const
    print(f"  Fredkin(CL→cond_nonzero={scratch[1]}): "
          f"one_const={one_const}, scratch[2]={scratch[2]}")
    
    next_flags[L_nonzero-1] += scratch[2]
    print(f"  next[{L_nonzero-1}] += scratch[2] → "
          f"next[{L_nonzero-1}]={next_flags[L_nonzero-1]}")
    
    if scratch[1] != 0:
        one_const, scratch[2] = scratch[2], one_const
    print(f"  Undo Fredkin: one_const={one_const}, scratch[2]={scratch[2]}")
    # >G: CL → cond_cell
    print()
    
    # ---- PHASE 3: Deactivate self ----
    print("Phase 3: Deactivate self")
    
    # >F(CL→cond_cell, one_const, scratch[2]): arm
    if cond_cell != 0:
        one_const, scratch[2] = scratch[2], one_const
    # >, current[k-1] -= scratch[2]
    current[k-1] -= scratch[2]
    print(f"  current[{k-1}] -= {scratch[2]} → current[{k-1}]={current[k-1]}")
    # >F: disarm
    if cond_cell != 0:
        one_const, scratch[2] = scratch[2], one_const
    print(f"  After disarm: one_const={one_const}, scratch[2]={scratch[2]}")
    print()
    
    # ---- PHASE 4: Uncompute ----
    print("Phase 4: Uncompute")
    
    # Undo the Fredkin from Phase 1
    # >G: CL → A
    print(f"  Undo Fredkin(CL→A={A}, swap scratch[0]↔scratch[1]):")
    if A != 0:
        scratch[0], scratch[1] = scratch[1], scratch[0]
        print(f"    A≠0, swapped → scratch[0]={scratch[0]}, scratch[1]={scratch[1]}")
    else:
        print(f"    A=0, no swap → scratch[0]={scratch[0]}, scratch[1]={scratch[1]}")
    # >G: CL → cond_cell
    
    # >, scratch[0] -= cond_cell
    scratch[0] -= cond_cell
    print(f"  scratch[0] -= cond_cell({cond_cell}) → scratch[0]={scratch[0]}")
    
    # >, cond_cell -= next[L_zero-1]
    cond_cell -= next_flags[L_zero-1]
    print(f"  cond_cell -= next[{L_zero-1}]({next_flags[L_zero-1]}) → "
          f"cond_cell={cond_cell}")
    
    # >, cond_cell -= next[L_nonzero-1]
    cond_cell -= next_flags[L_nonzero-1]
    print(f"  cond_cell -= next[{L_nonzero-1}]({next_flags[L_nonzero-1]}) → "
          f"cond_cell={cond_cell}")
    print()
    
    # ---- VERIFY ----
    print("=== Final state ===")
    print(f"  A = {A} (unchanged from {A_val}? {A == A_val})")
    print(f"  current = {current} (all zero? {all(c == 0 for c in current)})")
    print(f"  next = {next_flags}")
    print(f"  cond_cell = {cond_cell} (clean? {cond_cell == 0})")
    print(f"  scratch = {scratch} (clean? {all(s == 0 for s in scratch)})")
    print(f"  one_const = {one_const} (still 1? {one_const == 1})")
    
    if active_k:
        if A_val == 0:
            expected_target = L_zero - 1
        else:
            expected_target = L_nonzero - 1
        actual_target = next(i for i, v in enumerate(next_flags) if v == 1)
        print(f"  Target: next[{actual_target}] = 1 "
              f"(expected next[{expected_target}]? {actual_target == expected_target})")
    else:
        print(f"  No target activated (correct for inactive block? "
              f"{all(v == 0 for v in next_flags)})")
    
    all_clean = (cond_cell == 0 and all(s == 0 for s in scratch) 
                 and one_const == 1 and A == A_val 
                 and all(c == 0 for c in current))
    print(f"\n  ALL INVARIANTS MAINTAINED: {all_clean}")
    return all_clean


# Test all cases
print("=" * 60)
results = []

# Case 1: Active block, A = 0 (should goto L_zero)
r = trace_jz_block(active_k=1, A_val=0)
results.append(("active, A=0", r))
print("\n" + "=" * 60 + "\n")

# Case 2: Active block, A = 5 (should goto L_nonzero)
r = trace_jz_block(active_k=1, A_val=5)
results.append(("active, A=5", r))
print("\n" + "=" * 60 + "\n")

# Case 3: Inactive block, A = 0
r = trace_jz_block(active_k=0, A_val=0)
results.append(("inactive, A=0", r))
print("\n" + "=" * 60 + "\n")

# Case 4: Inactive block, A = 5
r = trace_jz_block(active_k=0, A_val=5)
results.append(("inactive, A=5", r))

print("\n" + "=" * 60)
print("\nSUMMARY:")
for desc, ok in results:
    print(f"  {desc:20s}: {'PASS' if ok else 'FAIL'}")
print(f"\nAll passed: {all(r for _, r in results)}")
