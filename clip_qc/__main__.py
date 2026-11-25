"""Entry point so `python3 -m clip_qc` works."""

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
