"""Watch each site's bill folder (OneDrive-synced SharePoint) and add new bills to its master workbook.

    bill-reader watch sites.csv
    bill-reader watch sites.csv --once

sites.csv has one row per site: Site, Folder, Master, Enabled. Subfolders are
watched too. Each site has its own scanner thread; the bills are read one at a
time by one reader, so the models are loaded once. What was read is kept in a
local database. A master gets the new rows once no new bill has come for it
for --quiet seconds, or, while bills keep coming, every --write-every seconds;
one that cannot be written, as when it is open, is tried again after --retry
seconds. Rows already in a master are never changed (master.py).
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import logging
import os
import queue
import sqlite3
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, NamedTuple

from .master import ID, MASTER_COLUMNS, PAGE, READ_AT, SITE, SOURCE, MasterLocked, MasterRefused, write_master
from .reader import SUPPORTED_SUFFIXES, PageDone, FileFinished, PageReader, has_bill_values, iter_pages
from .record import with_zeros

log = logging.getLogger("bill_reader.watch")

_ON = {"", "y", "yes", "true", "1", "是"}
_OFF = {"n", "no", "false", "0", "否"}
_STATUS = {"done": "完成", "failed": "失敗", "deleted": "來源已刪除"}


@dataclass(frozen=True)
class Site:
    name: str
    folder: Path
    master: Path


def _read_text(path: Path) -> str:
    """Saved from Excel as plain CSV, a file is in the code page, not UTF-8."""
    data = path.read_bytes()
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("cp950")


def load_sites(path: Path) -> list[Site]:
    rows = list(csv.DictReader(_read_text(path).splitlines()))
    sites: list[Site] = []
    problems: list[str] = []
    for line, raw in enumerate(rows, start=2):
        row = {(key or "").strip().lower(): (value or "").strip() for key, value in raw.items()}
        if not any(row.values()):
            continue
        enabled = row.get("enabled", "").lower()
        if enabled in _OFF:
            continue
        if enabled not in _ON:
            problems.append(f"line {line}: Enabled should be Y or N, not {enabled!r}")
            continue
        name, folder, master = row.get("site", ""), row.get("folder", ""), row.get("master", "")
        if not (name and folder and master):
            problems.append(f"line {line}: Site, Folder and Master are all needed")
        elif not master.lower().endswith(".xlsx"):
            problems.append(f"line {line}: Master should be an .xlsx file")
        elif any(site.name == name for site in sites):
            problems.append(f"line {line}: site {name} is listed twice")
        else:
            sites.append(Site(name, Path(folder), Path(master)))
    if problems:
        raise SystemExit(f"{path}:\n  " + "\n  ".join(problems))
    if not sites:
        raise SystemExit(f"{path}: no site is enabled (columns: Site, Folder, Master, Enabled)")
    return sites


class Listing(NamedTuple):
    """The bill files in a site's folder, or None when the folder cannot be seen."""

    site: str
    files: list[tuple[str, int, float]] | None  # relative path, size, mtime


def list_site(site: Site) -> Listing:
    if not site.folder.is_dir():
        return Listing(site.name, None)
    files = []
    for path in site.folder.rglob("*"):
        if path.suffix.lower() not in SUPPORTED_SUFFIXES or path.name.startswith(("~$", ".")):
            continue
        try:
            stat = path.stat()
        except OSError:
            continue  # being replaced by the sync client
        if path.is_file():
            files.append((path.relative_to(site.folder).as_posix(), stat.st_size, stat.st_mtime))
    return Listing(site.name, sorted(files))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


_SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    site TEXT, path TEXT, size INTEGER, mtime REAL, sha256 TEXT, status TEXT,
    bills INTEGER, review INTEGER, read_at TEXT, attempts INTEGER, error TEXT,
    PRIMARY KEY (site, path));
CREATE TABLE IF NOT EXISTS bills (id TEXT PRIMARY KEY, site TEXT, sha256 TEXT, row TEXT, replaces TEXT);
CREATE TABLE IF NOT EXISTS placed (id TEXT, master TEXT, kind TEXT, target TEXT, PRIMARY KEY (id, master));
"""


class Watcher:
    def __init__(
        self,
        sites: list[Site],
        state: Path,
        *,
        reader: Any = None,
        settle: float = 30,
        write_every: float = 1800,
        quiet: float = 120,
        retry: float = 300,
        attempts: int = 3,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.sites = {site.name: site for site in sites}
        self.state = state
        self.reader = reader
        self.settle = settle
        self.write_every = write_every
        self.quiet = quiet
        self.retry = retry
        self.attempts = attempts
        self.clock = clock
        state.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(state / "watch.db")
        self.db.executescript(_SCHEMA)
        self.dirty: set[str] = set()
        self.written: dict[str, float] = {}
        self.changed: dict[str, float] = {}  # when a master last got something new to write
        self.retry_at: dict[str, float] = {}  # when a master that could not be written is tried again
        self.unseen: set[str] = set()
        for site in sites:  # bills read last time but not yet in a master, as when it could not be written
            key = self._master_key(site)
            if self.db.execute("SELECT 1 FROM bills WHERE site = ? AND id NOT IN (SELECT id FROM placed WHERE master = ?)",
                               (site.name, key)).fetchone():
                self.dirty.add(key)

    @staticmethod
    def _master_key(site: Site) -> str:
        return str(site.master.resolve()).lower()

    # ------------------------------------------------------------------ reading
    def take(self, listing: Listing) -> None:
        """Reads what in a site's listing is new or changed and has stopped changing."""
        site = self.sites[listing.site]
        if listing.files is None:
            if site.name not in self.unseen:
                log.warning("%s: folder not found: %s", site.name, site.folder)
                self.unseen.add(site.name)
            return  # its files are not taken for deleted
        self.unseen.discard(site.name)
        here = {path for path, _, _ in listing.files}
        known = dict(self.db.execute("SELECT path, status FROM files WHERE site = ?", (site.name,)).fetchall())
        gone = [path for path, status in known.items() if path not in here and status != "deleted"]
        for path in gone:
            log.info("%s: %s was removed", site.name, path)
            self.db.execute("UPDATE files SET status = 'deleted' WHERE site = ? AND path = ?", (site.name, path))
        if gone:
            self.db.commit()
            self._changed(site)
        for path, size, mtime in listing.files:
            if self.clock() - mtime >= self.settle:
                self._take_file(site, path, size, mtime)

    def _take_file(self, site: Site, relative: str, size: int, mtime: float) -> None:
        entry = self.db.execute(
            "SELECT size, mtime, sha256, status, attempts FROM files WHERE site = ? AND path = ?", (site.name, relative)
        ).fetchone()
        if entry and (entry[0], entry[1]) == (size, mtime) and (entry[3] == "done" or entry[4] >= self.attempts):
            return
        path = site.folder / relative
        try:
            digest = _sha256(path)
        except OSError as error:
            log.info("%s: %s not readable yet (%s)", site.name, relative, error)
            return
        earlier = self.db.execute(
            "SELECT bills, review, read_at FROM files WHERE sha256 = ? AND status IN ('done', 'deleted') AND bills IS NOT NULL",
            (digest,),
        ).fetchone()
        if earlier:  # the same file, moved, renamed or synced again: nothing to read
            self._save_file(site, relative, size, mtime, digest, "done", *earlier, attempts=0, error=None)
            return
        tries = entry[4] + 1 if entry and entry[2] == digest else 1
        self._read(site, path, relative, size, mtime, digest, tries, entry[2] if entry and entry[2] != digest else None)

    def _read(self, site: Site, path: Path, relative: str, size: int, mtime: float, digest: str, tries: int,
              before: str | None = None) -> None:
        """``before`` is the hash of what was at this path until its content changed."""
        if self.reader is None:
            log.info("Loading the models...")
            self.reader = PageReader()
        started = time.time()
        rows, errors = [], []
        read_at = time.strftime("%Y-%m-%d %H:%M:%S")
        for event in iter_pages([path], self.reader):
            if isinstance(event, PageDone):
                if event.record is None or (event.bill is not None and not has_bill_values(event.bill)):
                    continue
                row = with_zeros(event.record.row)
                row.update({ID: f"{digest[:16]}-p{event.page_number or 1}", SITE: site.name, SOURCE: relative,
                            PAGE: event.page_number, READ_AT: read_at})
                rows.append({column: row.get(column) for column in MASTER_COLUMNS})
            elif isinstance(event, FileFinished) and event.error:
                errors.append(event.error)
        if errors:
            log.warning("%s: %s failed (try %d of %d): %s", site.name, relative, tries, self.attempts, "; ".join(errors))
            self._save_file(site, relative, size, mtime, digest, "failed", None, None, read_at, attempts=tries, error="; ".join(errors))
            return
        review = sum(1 for row in rows if row.get("需人工檢查"))
        earlier = self._versions_of(site, relative, before) if before else {}
        for row in rows:
            self.db.execute("INSERT OR IGNORE INTO bills VALUES (?, ?, ?, ?, ?)",
                            (row[ID], site.name, digest, json.dumps(row, ensure_ascii=False), earlier.get(row[PAGE])))
        self._save_file(site, relative, size, mtime, digest, "done", len(rows), review, read_at, attempts=tries, error=None)
        log.info("%s: %s, %d bill(s), %d to review, %.0f s", site.name, relative, len(rows), review, time.time() - started)

    def _versions_of(self, site: Site, relative: str, before: str) -> dict[int | None, str]:
        """By page, the row a new version of a file stands for: the first reading that went into a master.

        A page of the old content that reached no master is forgotten instead,
        so only the newest content is added.
        """
        roots: dict[int | None, str] = {}
        still_used = self.db.execute("SELECT 1 FROM files WHERE sha256 = ? AND status = 'done' AND NOT (site = ? AND path = ?)",
            (before, site.name, relative),).fetchone()
        for row_id, row, replaces in self.db.execute(
            "SELECT id, row, replaces FROM bills WHERE site = ? AND sha256 = ?", (site.name, before)
        ).fetchall():
            root = replaces or row_id
            if self.db.execute("SELECT 1 FROM placed WHERE id = ?", (root,)).fetchone():
                roots[json.loads(row)[PAGE]] = root
            elif not still_used and root == row_id:
                self.db.execute("DELETE FROM bills WHERE id = ?", (row_id,))
        return roots

    def _save_file(self, site: Site, relative: str, size: int, mtime: float, digest: str, status: str,
                   bills: int | None, review: int | None, read_at: str | None, *, attempts: int, error: str | None) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO files VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (site.name, relative, size, mtime, digest, status, bills, review, read_at, attempts, error),
        )
        self.db.commit()
        self._changed(site)

    def _changed(self, site: Site) -> None:
        key = self._master_key(site)
        self.dirty.add(key)
        self.changed[key] = self.clock()

    # ------------------------------------------------------------------ writing
    def write_masters(self, *, now: bool = False) -> None:
        """Each master with something new, once reading for it has gone quiet.

        While bills keep coming, a master is still written every --write-every
        seconds; one that could not be written waits --retry seconds.
        """
        for key in sorted(self.dirty):
            clock = self.clock()
            if not now:
                if clock < self.retry_at.get(key, float("-inf")):
                    continue
                quiet = clock - self.changed.get(key, float("-inf")) >= self.quiet
                overdue = clock - self.written.get(key, float("-inf")) >= self.write_every
                if not (quiet or overdue):
                    continue
            sites = [site for site in self.sites.values() if self._master_key(site) == key]
            if sites and self._write(sites[0].master, key, [site.name for site in sites]):
                self.dirty.discard(key)
                self.written[key] = clock
                self.retry_at.pop(key, None)
            else:
                self.retry_at[key] = clock + self.retry

    def _write(self, master: Path, key: str, names: list[str]) -> bool:
        marks = ",".join("?" * len(names))
        bills: dict[str, dict[str, Any]] = {}
        replaces: dict[str, str | None] = {}
        for row_id, row, root in self.db.execute(f"SELECT id, row, replaces FROM bills WHERE site IN ({marks}) ORDER BY rowid", names):
            bills[row_id], replaces[row_id] = json.loads(row), root
        placed = {row_id: (kind, target) for row_id, kind, target in self.db.execute("SELECT id, kind, target FROM placed WHERE master = ?", (key,))}
        waiting, settled = [], []
        for row_id, row in bills.items():
            if row_id in placed:
                continue
            root = placed.get(replaces[row_id] or "")
            if root is None:
                waiting.append(row)
            elif root[0] == "duplicate":  # the file was already there as another file
                settled.append((row_id, "duplicate", root[1]))
            else:
                settled.append((row_id, "version", replaces[row_id]))
        placed.update({row_id: (kind, target) for row_id, kind, target in settled})
        duplicates = [(bills[row_id], target) for row_id, (kind, target) in placed.items() if kind == "duplicate" and row_id in bills]
        newest = {target: row_id for row_id, (kind, target) in placed.items() if kind == "version" and row_id in bills}
        versions = [(bills[row_id], target) for target, row_id in newest.items()]
        files = [
            [site, path, "沒有帳單頁" if status == "done" and count == 0 else _STATUS.get(status, status),
             count, review, read_at, attempts, error]
            for site, path, status, count, review, read_at, attempts, error in self.db.execute(
                f"SELECT site, path, status, bills, review, read_at, attempts, error FROM files "
                f"WHERE site IN ({marks}) ORDER BY site, path", names)
        ]
        try:
            result = write_master(master, waiting, bills, duplicates, versions, files, self.state / "backups")
        except MasterLocked as error:
            log.info("%s: %s; trying again next time", master.name, error)
            return False
        except MasterRefused as error:
            log.error("%s not written: %s", master, error)
            return False
        except OSError as error:
            log.warning("%s not written: %s", master, error)
            return False
        self.db.executemany(
            "INSERT OR REPLACE INTO placed VALUES (?, ?, ?, ?)",
            [(placement.id, key, "duplicate" if placement.duplicate_of else "row", placement.duplicate_of) for placement in result.placements]
            + [(row_id, key, kind, target) for row_id, kind, target in settled],
        )
        self.db.commit()
        doubled = sum(1 for placement in result.placements if placement.duplicate_of)
        log.info("%s: %d bill(s) added%s", master.name, result.appended, f", {doubled} already there as another file" if doubled else "")
        return True

    def run_once(self) -> None:
        for site in self.sites.values():
            self.take(list_site(site))
        self.write_masters(now=True)

    def close(self) -> None:
        self.db.close()


def _scan(site: Site, listings: queue.Queue, every: float, stop: threading.Event) -> None:
    while not stop.is_set():
        try:
            listings.put(list_site(site))
        except OSError as error:
            log.warning("%s: cannot list %s: %s", site.name, site.folder, error)
        stop.wait(every)


def watch(watcher: Watcher, scan_every: float, stop: threading.Event) -> None:
    listings: queue.Queue = queue.Queue()
    for site in watcher.sites.values():
        threading.Thread(target=_scan, args=(site, listings, scan_every, stop), name=f"scan {site.name}", daemon=True).start()
    while not stop.is_set():
        try:
            watcher.take(listings.get(timeout=5))
        except queue.Empty:
            pass
        watcher.write_masters()


class SingleInstance:
    """Two watchers on one state folder would read every bill twice."""

    def __init__(self, folder: Path) -> None:
        self.path = folder / "watch.lock"
        self.handle: Any = None

    def __enter__(self) -> "SingleInstance":
        import msvcrt

        self.handle = open(self.path, "a+")  # noqa: SIM115
        self.handle.seek(0)
        try:
            msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            self.handle.close()
            raise SystemExit(f"Another watcher is already running on {self.path.parent}") from None
        return self

    def __exit__(self, *exc: object) -> None:
        import msvcrt

        self.handle.seek(0)
        msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
        self.handle.close()


def default_state(sites: Path) -> Path:
    """Kept on this computer: a database in a synced folder is a database OneDrive breaks."""
    base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    tag = hashlib.sha1(str(sites.resolve()).lower().encode()).hexdigest()[:8]
    return base / "BillReader" / f"watch-{tag}"


def _logging_to(folder: Path) -> None:
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%Y-%m-%d %H:%M:%S")
    handlers: list[logging.Handler] = [logging.FileHandler(folder / "watch.log", encoding="utf-8")]
    if sys.stderr is not None:  # a file's name the console's code page lacks must not stop the log
        sys.stderr.reconfigure(errors="replace")
        handlers.append(logging.StreamHandler())
    for handler in handlers:
        handler.setFormatter(formatter)
        log.addHandler(handler)
    log.setLevel(logging.INFO)


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="bill-reader watch", description="Watch each site's bill folder and add new bills to its master workbook")
    parser.add_argument("sites", type=Path, help="CSV with the columns Site, Folder, Master, Enabled")
    parser.add_argument("--state", type=Path, help="folder for the database, log and backups (by default in %%LOCALAPPDATA%%)")
    parser.add_argument("--once", action="store_true", help="read what is new, write the masters and stop (for Task Scheduler)")
    parser.add_argument("--scan-every", type=float, default=60, help="seconds between looks at each folder (default 60)")
    parser.add_argument("--settle", type=float, default=30, help="seconds a file must be unchanged before it is read (default 30)")
    parser.add_argument("--quiet", type=float, default=120, help="seconds with no new bill before a master is written (default 120)")
    parser.add_argument("--write-every", type=float, default=1800,
                        help="while bills keep coming, seconds between writes of a master (default 1800)")
    parser.add_argument("--retry", type=float, default=300, help="seconds before a master that could not be written is tried again (default 300)")
    parser.add_argument("--attempts", type=int, default=3, help="tries for a file that fails to read (default 3)")
    args = parser.parse_args(list(argv) if argv is not None else None)

    sites = load_sites(args.sites)
    state = args.state or default_state(args.sites)
    state.mkdir(parents=True, exist_ok=True)
    _logging_to(state)
    with SingleInstance(state):
        watcher = Watcher(sites, state, settle=args.settle, write_every=args.write_every,
                          quiet=args.quiet, retry=args.retry, attempts=args.attempts)
        log.info("Watching %d site(s); state in %s", len(sites), state)
        try:
            if args.once:
                watcher.run_once()
            else:
                stop = threading.Event()
                try:
                    watch(watcher, args.scan_every, stop)
                except KeyboardInterrupt:
                    stop.set()
                    watcher.write_masters(now=True)
                    log.info("Stopped")
        finally:
            watcher.close()
    return 0
