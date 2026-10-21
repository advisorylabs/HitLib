"""The Animator: Pattern Studio's custom-animation editor.

The Design tab picks an animation for a strand. This tab builds one, out of
layers over drivers - see pattern_studio.models.CompositionConfig and the
firmware types it mirrors in include/hitlib/led_strand.hpp.
"""

from .panel import AnimatorPanel

__all__ = ["AnimatorPanel"]
