"""Read-only duplicate detection. Python 3.10+, standard library only."""
from __future__ import annotations

import csv
import hashlib
import os
import stat
import threading
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

CHUNK = 1024 * 1024
PREFIX = 64 * 1024
SKIP_NAMES = frozenset(name.casefold() for name in (
    "$Recycle.Bin", "System Volume Information", "Windows", "Program Files",
    "Program Files (x86)", "ProgramData", "AppData", ".git", "node_modules",
    ".venv", "venv", "__pycache__", ".Trash", ".Trashes", ".Spotlight-V100",
    ".fseventsd", ".DocumentRevisions-V100",
))
# Windows OFFLINE / RECALL_ON_OPEN / RECALL_ON_DATA_ACCESS.
CLOUD_ONLY = 0x1000 | 0x40000 | 0x400000
REPARSE = 0x400


def directory_link(s: os.stat_result) -> bool:
    # Cloud folders also have reparse points; do not mistake them for junctions.
    return stat.S_ISLNK(s.st_mode) or getattr(s, "st_reparse_tag", 0) in (0xA0000003, 0xA000000C)


class Cancelled(Exception):
    pass


def stamp(s: os.stat_result) -> tuple:
    # Windows stat/fstat can disagree on deprecated ctime (CPython #157671).
    # Birth time is consistent; on older Python, identity + size + mtime remain.
    extra_time = getattr(s, "st_birthtime_ns", 0) if os.name == "nt" else s.st_ctime_ns
    return s.st_size, s.st_mtime_ns, extra_time, s.st_dev, s.st_ino


@dataclass(frozen=True)
class FileRecord:
    path: str
    size: int
    signature: tuple


@dataclass(frozen=True)
class DuplicateGroup:
    digest: str
    size: int
    files: tuple[FileRecord, ...]

    @property
    def extra_bytes(self) -> int:
        return self.size * (len(self.files) - 1)


@dataclass
class ScanResult:
    roots: list[str]
    groups: list[DuplicateGroup] = field(default_factory=list)
    issues: list[tuple[str, str]] = field(default_factory=list)
    files_seen: int = 0
    eligible_files: int = 0
    eligible_bytes: int = 0
    aliases_skipped: int = 0
    folders_skipped: int = 0
    cloud_skipped: int = 0
    small_skipped: int = 0
    cancelled: bool = False

    @property
    def extra_files(self) -> int:
        return sum(len(g.files) - 1 for g in self.groups)

    @property
    def extra_bytes(self) -> int:
        return sum(g.extra_bytes for g in self.groups)


def normalized(path: str) -> str:
    return os.path.normcase(os.path.abspath(path))


def clean_roots(paths: list[str]) -> list[str]:
    """Keep explicitly selected roots, including children with different exclusions."""
    roots = {}
    for path in paths:
        absolute = os.path.abspath(os.path.expanduser(path))
        roots.setdefault(normalized(absolute), absolute)
    return list(roots.values())


class Scanner:
    def __init__(self, roots: list[str], minimum: int = 1, skip_common: bool = True,
                 cancel: threading.Event | None = None,
                 progress: Callable[[dict], None] | None = None):
        self.result = ScanResult(clean_roots(roots))
        self.minimum = minimum
        self.skip_common = skip_common
        self.cancel = cancel if cancel is not None else threading.Event()
        self.progress = progress or (lambda _: None)
        self._paths: set[str] = set()
        self._identities: set[tuple] = set()

    def check_cancel(self):
        if self.cancel.is_set():
            raise Cancelled()

    def note(self, path: str, reason: str):
        self.result.issues.append((path, reason))

    def emit(self, phase: str, current: int = 0, total: int = 0, path: str = ""):
        self.progress(dict(phase=phase, current=current, total=total, path=path,
                           files=self.result.files_seen,
                           bytes=self.result.eligible_bytes))

    def enumerate_files(self) -> dict[int, list[FileRecord]]:
        sizes: dict[int, list[FileRecord]] = defaultdict(list)
        stack = list(reversed(self.result.roots))
        seen_dirs = set()
        while stack:
            self.check_cancel()
            folder = stack.pop()
            if normalized(folder) in seen_dirs:
                continue
            seen_dirs.add(normalized(folder))
            self.emit("Finding files", path=folder)
            try:
                fs = os.lstat(folder)
                if not stat.S_ISDIR(fs.st_mode) or directory_link(fs):
                    self.note(folder, "Selected folder is a link, junction, or unavailable directory; skipped")
                    continue
                with os.scandir(folder) as entries:
                    for entry in entries:
                        self.check_cancel()
                        try:
                            s = entry.stat(follow_symlinks=False)
                            attrs = getattr(s, "st_file_attributes", 0)
                            if stat.S_ISLNK(s.st_mode):
                                self.result.aliases_skipped += 1
                                continue
                            if stat.S_ISDIR(s.st_mode):
                                if directory_link(s):
                                    self.result.folders_skipped += 1
                                elif self.skip_common and entry.name.casefold() in SKIP_NAMES:
                                    self.result.folders_skipped += 1
                                else:
                                    stack.append(entry.path)
                                continue
                            if not stat.S_ISREG(s.st_mode):
                                continue
                            # Windows DirEntry.stat omits device/inode. lstat supplies
                            # the actual identity used for hard links and race checks.
                            s = os.lstat(entry.path)
                            if not stat.S_ISREG(s.st_mode):
                                continue
                            if getattr(s, "st_flags", 0) & 0x40000000:  # macOS SF_DATALESS
                                self.result.cloud_skipped += 1
                                continue
                            attrs = getattr(s, "st_file_attributes", 0)
                            path = normalized(entry.path)
                            if path in self._paths:
                                continue
                            self._paths.add(path)
                            self.result.files_seen += 1
                            if attrs & CLOUD_ONLY:
                                self.result.cloud_skipped += 1
                                continue
                            if s.st_size < self.minimum:
                                self.result.small_skipped += 1
                                continue
                            identity = (s.st_dev, s.st_ino)
                            if s.st_ino and identity in self._identities:
                                self.result.aliases_skipped += 1
                                continue
                            if s.st_ino:
                                self._identities.add(identity)
                            record = FileRecord(entry.path, s.st_size, stamp(s))
                            sizes[s.st_size].append(record)
                            self.result.eligible_files += 1
                            self.result.eligible_bytes += s.st_size
                        except OSError as exc:
                            self.note(entry.path, str(exc))
            except OSError as exc:
                self.note(folder, str(exc))
        return sizes

    def unchanged(self, record: FileRecord) -> bool:
        try:
            s = os.lstat(record.path)
            return (stat.S_ISREG(s.st_mode) and stamp(s) == record.signature
                    and not getattr(s, "st_file_attributes", 0) & CLOUD_ONLY
                    and not getattr(s, "st_flags", 0) & 0x40000000)
        except OSError:
            return False

    def hash_file(self, record: FileRecord, prefix: bool = False) -> str | None:
        try:
            self.check_cancel()
            if not self.unchanged(record):
                raise OSError("File changed or became unavailable during scan")
            digest = hashlib.sha256()
            with open(record.path, "rb") as stream:
                if stamp(os.fstat(stream.fileno())) != record.signature:
                    raise OSError("File changed before reading")
                remaining = min(PREFIX, record.size) if prefix else record.size
                while remaining:
                    self.check_cancel()
                    data = stream.read(min(CHUNK, remaining))
                    if not data:
                        raise OSError("File changed while reading")
                    digest.update(data)
                    remaining -= len(data)
                    if prefix:
                        break
                if stamp(os.fstat(stream.fileno())) != record.signature or not self.unchanged(record):
                    raise OSError("File changed while reading")
            return digest.hexdigest()
        except OSError as exc:
            self.note(record.path, str(exc))
            return None

    def same_bytes(self, left: FileRecord, right: FileRecord) -> bool:
        if not self.unchanged(left) or not self.unchanged(right):
            self.note(right.path, "Comparison skipped because a file changed")
            return False
        try:
            with open(left.path, "rb") as a, open(right.path, "rb") as b:
                if stamp(os.fstat(a.fileno())) != left.signature or stamp(os.fstat(b.fileno())) != right.signature:
                    return False
                remaining = left.size
                while remaining:
                    self.check_cancel()
                    block_a = a.read(min(CHUNK, remaining))
                    block_b = b.read(min(CHUNK, remaining))
                    if not block_a or block_a != block_b:
                        return False
                    remaining -= len(block_a)
                return (stamp(os.fstat(a.fileno())) == left.signature
                        and stamp(os.fstat(b.fileno())) == right.signature
                        and self.unchanged(left) and self.unchanged(right))
        except OSError as exc:
            self.note(right.path, str(exc))
            return False

    def run(self) -> ScanResult:
        try:
            by_size = self.enumerate_files()
            candidates = [records for records in by_size.values() if len(records) > 1]
            total = sum(map(len, candidates))
            current = 0
            full_candidates = []
            for records in candidates:
                by_prefix: dict[str, list[FileRecord]] = defaultdict(list)
                for record in records:
                    self.check_cancel()
                    self.emit("Checking file samples", current, total, record.path)
                    digest = self.hash_file(record, prefix=True)
                    if digest is not None:
                        by_prefix[digest].append(record)
                    current += 1
                full_candidates.extend(group for group in by_prefix.values() if len(group) > 1)
            total = sum(map(len, full_candidates))
            current = 0
            exact_candidates = []
            for records in full_candidates:
                by_hash: dict[str, list[FileRecord]] = defaultdict(list)
                for record in records:
                    self.check_cancel()
                    self.emit("Hashing complete files", current, total, record.path)
                    digest = self.hash_file(record)
                    if digest is not None:
                        by_hash[digest].append(record)
                    current += 1
                exact_candidates.extend((digest, group) for digest, group in by_hash.items() if len(group) > 1)
            total = sum(len(group) for _, group in exact_candidates)
            current = 0
            for digest, records in exact_candidates:
                # Partition even a hypothetical SHA-256 collision by actual bytes.
                partitions: list[list[FileRecord]] = []
                for record in records:
                    self.check_cancel()
                    self.emit("Verifying matching bytes", current, total, record.path)
                    for partition in partitions:
                        if self.same_bytes(partition[0], record):
                            partition.append(record)
                            break
                    else:
                        partitions.append([record])
                    current += 1
                for partition in partitions:
                    stable = [r for r in partition if self.unchanged(r)]
                    if len(stable) > 1:
                        self.result.groups.append(DuplicateGroup(digest, stable[0].size,
                            tuple(sorted(stable, key=lambda r: r.path.casefold()))))
            # Results are a snapshot. A final metadata check excludes late changes.
            final = []
            for group in self.result.groups:
                stable = tuple(r for r in group.files if self.unchanged(r))
                for record in group.files:
                    if record not in stable:
                        self.note(record.path, "File changed before scan completed")
                if len(stable) > 1:
                    final.append(DuplicateGroup(group.digest, group.size, stable))
            self.result.groups = sorted(final, key=lambda g: (-g.extra_bytes, -g.size, g.files[0].path))
        except Cancelled:
            self.result.cancelled = True
            self.result.groups.clear()  # Never present an incomplete scan as final results.
        return self.result


def safe_cell(value: str) -> str:
    # CSV quoting alone does not prevent spreadsheet formulas.
    return "'" + value if value.startswith(("=", "+", "-", "@", "\t", "\r", "\n")) else value


def export_csv(result: ScanResult, destination: str):
    with open(destination, "w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["Group", "Size per file (bytes)", "Copies", "Extra logical bytes", "SHA-256", "Path"])
        for number, group in enumerate(result.groups, 1):
            for record in group.files:
                writer.writerow([number, group.size, len(group.files), group.extra_bytes,
                                 group.digest, safe_cell(record.path)])


def export_issues(result: ScanResult, destination: str):
    with open(destination, "w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["Path", "Reason"])
        writer.writerows((safe_cell(path), safe_cell(reason)) for path, reason in result.issues)
