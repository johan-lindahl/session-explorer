"""session-explorer package."""

import os
import sys

__version__ = "1.16.4"

_VENDOR = os.path.join(os.path.dirname(__file__), "_vendor")
if os.path.isdir(_VENDOR) and _VENDOR not in sys.path:
    # Append, don't prepend — never shadow a user's site-packages.
    sys.path.append(_VENDOR)
