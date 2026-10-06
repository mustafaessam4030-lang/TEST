"""
Kept for the name used before: the ETA write proof now lives in the worker's
verification command, which also reports to the control plane.

    python -m worker.verify eta --reference MEDUAHP69377
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from worker.verify import main  # noqa: E402

if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(["eta", "--reference", sys.argv[1]]))
