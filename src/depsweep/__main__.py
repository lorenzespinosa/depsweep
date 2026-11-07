"""Allow `python -m depsweep`."""

import sys

from .cli import main

sys.exit(main())
