"""Did a change alter anything? Every column and flag of every page, against a reference.

    python evaluation/compare.py record  ref.json files.txt     # before changing anything
    python evaluation/compare.py check   ref.json files.txt     # after: exit 1 if anything differs

``files.txt`` lists one bill file per line. The models' answers are cached in
tmp/model_cache, so only the first run is slow, and a change to the parser is
checked in a few minutes. Keep the lists and references out of Git: they hold bills.
"""

from __future__ import annotations

import hashlib
import json
import pickle
import sys
from pathlib import Path

import numpy as np

from bill_reader.engine import OnnxEngine, model_dir
from bill_reader.reader import FileFinished, PageDone, PageReader, iter_pages

CACHE = Path(__file__).resolve().parents[1] / "tmp" / "model_cache"


class CachedEngine:
    """The engine's answers from disk, asking the models only about what is new."""

    def __init__(self) -> None:
        CACHE.mkdir(parents=True, exist_ok=True)
        self.real: OnnxEngine | None = None
        self.last_read = None

    def _answer(self, kind: str, source, ask):
        data = source.tobytes() + repr(source.shape).encode() if isinstance(source, np.ndarray) else Path(source).read_bytes()
        path = CACHE / f"{kind}-{hashlib.sha256(data).hexdigest()[:32]}.pkl"
        if path.is_file():
            return pickle.loads(path.read_bytes())
        self.real = self.real or OnnxEngine(model_dir())
        answer = ask(self.real)
        path.write_bytes(pickle.dumps(answer))
        return answer

    def page_angle(self, source):
        return self._answer("angle", source, lambda real: real.page_angle(source))

    def predict(self, source):
        answer, self.last_read = self._answer("read", source, lambda real: (real.predict(source), getattr(real, "last_read", None)))
        return answer


def main() -> int:
    mode, reference, listing = sys.argv[1:4]
    files = [Path(line) for line in Path(listing).read_text(encoding="utf-8").splitlines() if line.strip()]
    reader = PageReader()
    engine = CachedEngine()
    reader._engine = lambda: engine
    pages = {}
    for event in iter_pages(files, reader):
        if isinstance(event, PageDone):
            pages[f"{event.path}#{event.page_number}"] = dict(event.record.row) if event.record else None
        elif isinstance(event, FileFinished) and event.error:
            pages[f"{event.path}#error"] = event.error
    if mode == "record":
        Path(reference).write_text(json.dumps(pages, ensure_ascii=False, indent=0), encoding="utf-8")
        print(f"{len(pages)} 頁已存成基準：{reference}")
        return 0
    before = json.loads(Path(reference).read_text(encoding="utf-8"))
    different = [key for key in sorted(set(before) | set(pages)) if before.get(key) != pages.get(key)]
    for key in different:
        old, new = before.get(key) or {}, pages.get(key) or {}
        print(key)
        for column in sorted(set(old) | set(new)):
            if old.get(column) != new.get(column):
                print(f"  {column}: {old.get(column)} -> {new.get(column)}")
    print(f"{len(pages)} 頁，{len(different)} 頁不同")
    return 1 if different else 0


if __name__ == "__main__":
    sys.exit(main())
