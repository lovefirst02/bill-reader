"""Download the models named in models.lock.json into models/, checking each file's SHA-256.

    python scripts/fetch_models.py
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    lock = json.loads((ROOT / "models.lock.json").read_text(encoding="utf-8"))
    folder = ROOT / "models"
    folder.mkdir(exist_ok=True)
    for name, wanted in lock["files"].items():
        target = folder / name
        if target.is_file() and hashlib.sha256(target.read_bytes()).hexdigest() == wanted:
            print(f"已有 {name}")
            continue
        url = f"https://github.com/{lock['repository']}/releases/download/{lock['version']}/{name}"
        print(f"下載 {url}", flush=True)
        # curl, which comes with Windows, trusts what the system trusts; some Pythons' ssl does not.
        partial = target.with_suffix(target.suffix + ".part")
        subprocess.run(["curl", "-fsSL", "-o", str(partial), url], check=True)
        actual = hashlib.sha256(partial.read_bytes()).hexdigest()
        if actual != wanted:
            partial.unlink()
            print(f"{name} 的 SHA-256 不符：應為 {wanted}，實際 {actual}", file=sys.stderr)
            return 1
        partial.replace(target)
    print(f"模型 {lock['version']} 已就緒：{folder}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
