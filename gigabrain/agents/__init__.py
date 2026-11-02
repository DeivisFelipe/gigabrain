from .base import Context
from .expert import Expert
from .leader import Leader
from .twin import DigitalTwin

__all__ = ["Context", "Critic", "DigitalTwin", "Expert", "Leader", "ValidationSettings"]


def __getattr__(name: str):
    # The Critic loads the validation layer, so it only comes when asked for:
    # an error in validation/ must not stop a run without validation.
    if name in ("Critic", "ValidationSettings"):
        from . import critic
        return getattr(critic, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
