# Theory Notes

## Local vs Global Reversibility (Micro/Macro Information Loss)

The fb2d metabolism is microscopically reversible: every `step()` has a
unique predecessor recoverable via `step_back()`. But macroscopic
observables can lose information in a way exactly analogous to
thermodynamic irreversibility.

### The compression example

A run of 15 identical `A` fuel cells gets XOR-compressed to:
- 1 `A` (the reference cell, kept)
- 14 zeros (the compressed-out duplicates)
- A `:` accumulator value encoding how many iterations ran
- An EX position trail recording where the walk-back stopped

Each individual XOR step is self-inverse and reversible. But after
compression, a coarse-grained snapshot showing "1 A and some zeros"
does not reveal whether the original run was 15 A's, 8 A's (with a
mismatch at cell 9), or any other configuration. The count is encoded
in the microdynamics — the accumulator value, the EX trail, the
walk-back distance — but only if you track the full trajectory.

### Analogy to thermodynamics

This is the same structure as a gas expanding into a vacuum:

- **Micro level**: every molecular collision is time-reversible. The
  exact microstate at time T suffices to reconstruct time T-1.
- **Macro level**: "which half of the box is the gas in?" goes from
  informative (left half) to uninformative (uniform). Information is
  not destroyed — it is *dispersed* into molecular correlations that
  no macroscopic measurement can access.

The metabolism does the same: macro information ("how long was the fuel
run?") disperses into micro correlations (accumulator, EX positions,
walk-back trail) that are invisible to any observation that only looks
at cell values.

### Landauer's principle

The compression *appears* to erase information (15 A's → 1 A + zeros),
but because the dynamics are reversible, information is conserved in
the total state. The zeros are not "blank" in the physical sense — they
are zeros-with-a-history, distinguishable from primordial zeros only
through their micro context.

Landauer's principle says irreversible erasure of 1 bit must dissipate
at least kT ln 2 of energy. Our system sidesteps this: no bit is truly
erased. The apparent erasure (many A's → fewer A's + zeros) is a
reversible reorganization, not a deletion. The "waste heat" equivalent
is the micro trail left behind — it carries exactly the information
needed to reconstruct the original fuel run.

### Torus periodicity and Poincare recurrence

On a finite reversible system (toroidal grid, finite cell values), the
state space is finite and the dynamics are a permutation. Every
trajectory must eventually recur (Poincare recurrence theorem). The
"lost" macro information will reconstitute when the trajectory revisits
the region of phase space where the A's are grouped together. The
recurrence time is astronomically long (bounded by the size of the
state space, which is 65536^(rows×cols) × IP_states), but it is finite.

This is another exact parallel with statistical mechanics: the Second
Law holds in practice because recurrence times exceed the age of the
universe, even though they are finite in principle.

### Open questions

- Can we define an entropy-like quantity for the waste trail that
  measures how much macro information has been dispersed? The number
  of distinct micro histories compatible with a given macro snapshot
  would be the analog of thermodynamic entropy.

- The immunity gadget *decreases* entropy locally (corrects errors,
  restoring order). The metabolism *increases* entropy (disperses fuel
  structure into the waste trail). Is there a meaningful sense in which
  the agent maintains itself at a steady-state entropy, analogous to a
  dissipative structure (Prigogine)?

- The free-food cheat breaks reversibility by injecting new fuel from
  outside the system. This is analogous to coupling the system to a
  heat bath. Can we characterize the "temperature" of the noise source
  and the "free energy" provided by the food cheat?
