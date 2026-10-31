"""state_machine.py — The 5-state assistant brain for the Orb Visualizer.

Each state is a *vector of continuous parameters* rather than a boolean flag.
Transitions are implemented as exponential interpolation of every parameter toward
its target, so switching states never causes a visual "jump" — states *morph* into
each other. This is the same lerp philosophy as the orb-style cross-fade.

Params consumed by the renderers:
  scale   core orb radius multiplier
  halo    outer aura intensity (breathing/edges)
  rot     internal rotation / vortex speed
  pulse   fast audio pulse multiplier
  bright  overall brightness
  hue     hue offset (ERROR -> negative = reddish; THINKING -> violet chase)
  glitch  glitch/shake intensity (ERROR state)
  edge    outer-edge expansion driven by speech frequencies
  mix     how strongly the active audio channel drives the shape
"""

# str param-name -> default state values
STATES = {
    "IDLE": dict(scale=0.85, halo=0.30, rot=0.10, pulse=0.5, bright=0.75,
                 hue=0.00, glitch=0.00, edge=0.00, mix=0.30),
    "USER_LISTENING": dict(scale=1.05, halo=0.90, rot=0.40, pulse=0.9, bright=1.00,
                           hue=0.06, glitch=0.00, edge=1.00, mix=0.85),
    "THINKING": dict(scale=0.95, halo=0.50, rot=1.50, pulse=1.8, bright=0.95,
                     hue=0.28, glitch=0.05, edge=0.20, mix=0.50),
    "ASSISTANT_SPEAKING": dict(scale=1.20, halo=1.00, rot=0.90, pulse=1.4, bright=1.15,
                               hue=0.12, glitch=0.00, edge=0.60, mix=0.95),
    "ERROR": dict(scale=0.70, halo=0.15, rot=0.50, pulse=2.2, bright=0.85,
                  hue=-0.10, glitch=0.95, edge=0.50, mix=0.20),
}

STATES_ORDER = list(STATES)


class StateMachine:
    """Holds the *current* live parameter vector and eases toward the target state."""

    def __init__(self, state="IDLE", blend=0.06):
        self.blend = blend                 # transition speed per frame (lerp factor)
        self.name = state
        self.cur = dict(STATES[state])     # live smoothed params
        self.target = STATES[state]

    def set(self, name):
        """Request a state change. Respects INSTANT stays; else eases over time."""
        if name not in STATES:
            raise ValueError(f"unknown state: {name} (use {STATES_ORDER})")
        self.name = name
        self.target = STATES[name]

    def advance(self):
        """Step one frame: move every param toward its target vector."""
        t = self.target
        c = self.cur
        b = self.blend
        for k in t:
            c[k] += (t[k] - c[k]) * b

    def __getitem__(self, key):
        return self.cur[key]

    def update(self):
        """Synonym used by the visualizer to keep it in sync every frame."""
        self.advance()
        return self.cur