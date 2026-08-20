#!/usr/bin/env python3
"""Read-only multi-root AINE Portfolio Intelligence Registry.

This module remains the public compatibility facade.  Implementation
responsibilities live in focused modules while the historical import path and
console-script entry point remain stable.
"""

from __future__ import annotations

try:
    from .version import VERSION
    from .constants import *
    from .common import *
    from .discovery import *
    from .analysis import *
    from .evidence import *
    from .view import *
    from .cli import *
except ImportError:  # direct execution: python3 registry/aine_registry.py
    from version import VERSION
    from constants import *
    from common import *
    from discovery import *
    from analysis import *
    from evidence import *
    from view import *
    from cli import *


if __name__ == "__main__":
    raise SystemExit(main())
