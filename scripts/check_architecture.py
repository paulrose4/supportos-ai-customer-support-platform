import subprocess
import sys
from pathlib import Path

if __name__ == "__main__":
    root = Path(__file__).parents[1]
    raise SystemExit(
        subprocess.call(
            [sys.executable, "-m", "pytest", "tests/contract/test_architecture.py", "-q"],
            cwd=root,
        )
    )
