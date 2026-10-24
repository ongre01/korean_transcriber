"""Stage a job's result files beside their final location and publish them together.

A job writes every result file into a private staging directory inside the
output folder and moves the whole set to the final names only after the last
file is complete.  An interrupted or failed job therefore never leaves a
partial file, or a file without the results it belongs to, under a final
name.  A file the job replaces is kept in the staging directory until the set
is published and is restored if publishing fails part-way.

A killed process cannot clean up (``QProcess::kill()`` is the only way to stop
a console process on Windows), so its staging directory stays behind.  Every
staging directory holds an owner lock for the life of its job, and the next
job that writes to the same output folder removes the staging directories
whose lock is no longer held.  Staging directories of running jobs are never
touched.
"""

from __future__ import annotations

import os
from pathlib import Path
import re
import shutil
import tempfile
import threading
import time
from typing import BinaryIO, Sequence

if os.name == "nt":
    fcntl = None
else:
    import fcntl


STAGING_SUFFIX = ".partial"
STAGING_PREFIXES = ("multimic", "transcribe")
OWNER_LOCK_NAME = ".owner.lock"
# A staging directory gets its owner lock right after it is created.  One
# without a lock is only treated as abandoned once it cannot be the brand-new
# directory of a concurrent job.
UNLOCKED_STAGING_GRACE_SECONDS = 60.0

_BACKUP_PREFIX = ".replaced-"
_STAGING_NAME = re.compile(
    r"^\.(?:%s)_[A-Za-z0-9_]+%s$"
    % ("|".join(STAGING_PREFIXES), re.escape(STAGING_SUFFIX))
)

_active_stagings: set["ResultStaging"] = set()
_active_stagings_lock = threading.Lock()


def _file_name(name: str) -> str:
    if not name or name in (".", "..") or Path(name).name != name:
        raise ValueError(f"result file name must be a bare file name: {name!r}")
    return name


def _acquire_owner_lock(path: Path) -> BinaryIO:
    handle = open(path, "a+b")
    if fcntl is not None:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BaseException:
            handle.close()
            raise
    # On Windows a file opened by Python cannot be deleted by anyone while the
    # handle is open, so the open handle itself is the lock.
    return handle


def _created_long_ago(directory: Path, now: float) -> bool:
    try:
        return now - directory.stat().st_mtime >= UNLOCKED_STAGING_GRACE_SECONDS
    except OSError:
        return False


def _owner_is_gone(directory: Path, now: float) -> bool:
    lock = directory / OWNER_LOCK_NAME
    try:
        if fcntl is None:
            # Fails while the owning job still holds the lock file open.
            os.remove(lock)
            return True
        descriptor = os.open(lock, os.O_RDWR)
    except FileNotFoundError:
        return _created_long_ago(directory, now)
    except OSError:
        return False

    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return False
    finally:
        os.close(descriptor)
    return True


def remove_abandoned_staging(output_directory: str | Path) -> list[Path]:
    """Remove staging directories whose job no longer runs.

    Only directories named like this module's staging directories are
    considered.  This is best effort: an entry that cannot be inspected or
    removed is left alone and never raises.
    """

    removed: list[Path] = []
    now = time.time()
    with _active_stagings_lock:
        own = {staging._directory for staging in _active_stagings}
    try:
        entries = list(os.scandir(output_directory))
    except OSError:
        return removed
    for entry in entries:
        if not _STAGING_NAME.match(entry.name):
            continue
        try:
            if not entry.is_dir(follow_symlinks=False):
                continue
        except OSError:
            continue
        path = Path(entry.path)
        if path in own or not _owner_is_gone(path, now):
            continue
        shutil.rmtree(path, ignore_errors=True)
        if not path.exists():
            removed.append(path)
    return removed


def discard_active_staging() -> None:
    """Discard every staging directory this process still owns.

    The cancellation watchdog uses this when the main thread cannot unwind in
    time.  A staging directory that is being published is discarded once the
    publishing step has finished.
    """

    with _active_stagings_lock:
        stagings = list(_active_stagings)
    for staging in stagings:
        staging.discard()


def _restore(backup: Path, final: Path) -> None:
    try:
        os.replace(backup, final)
    except OSError:
        pass


def _remove(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass


class ResultStaging:
    """A job's private staging directory inside ``output_directory``.

    Nothing is created on disk until :attr:`directory` is first used, so a job
    that fails before it produces results leaves the output folder as it was.
    Leaving the ``with`` block removes the staging directory and everything
    still in it, whether or not it was published.
    """

    def __init__(self, output_directory: str | Path, *, prefix: str) -> None:
        if prefix not in STAGING_PREFIXES:
            choices = ", ".join(STAGING_PREFIXES)
            raise ValueError(f"staging prefix must be one of: {choices}")
        self._output_directory = Path(output_directory).expanduser().resolve()
        self._prefix = prefix
        self._directory: Path | None = None
        self._owner_lock: BinaryIO | None = None
        self._discarded = False
        self._lock = threading.RLock()

    def __enter__(self) -> "ResultStaging":
        return self

    def __exit__(self, *_exception) -> None:
        self.discard()

    @property
    def output_directory(self) -> Path:
        return self._output_directory

    @property
    def directory(self) -> Path:
        """The staging directory, created (with the output folder) on first use."""

        with self._lock:
            if self._discarded:
                raise RuntimeError("result staging has already been discarded")
            if self._directory is None:
                self._create()
            return self._directory

    def path(self, name: str) -> Path:
        """Where the result file ``name`` is written before it is published."""

        file_name = _file_name(name)
        return self.directory / file_name

    def final_path(self, name: str) -> Path:
        return self._output_directory / _file_name(name)

    def publish(self, names: Sequence[str]) -> list[Path]:
        """Move the staged files ``names`` to the output folder as one set.

        Files are moved in the given order, so callers list the file a
        consumer waits for last.  If a move fails, every file already moved is
        taken back and the files it replaced are restored before the error is
        raised.
        """

        file_names = [_file_name(name) for name in names]
        if len({os.path.normcase(name) for name in file_names}) != len(file_names):
            raise ValueError("result file names must be unique")
        with self._lock:
            directory = self.directory
            for name in file_names:
                if not (directory / name).is_file():
                    raise FileNotFoundError(f"staged result file is missing: {name}")

            swapped: list[tuple[Path, Path | None]] = []
            try:
                for index, name in enumerate(file_names):
                    final = self._output_directory / name
                    backup = None
                    if os.path.lexists(final):
                        backup = directory / f"{_BACKUP_PREFIX}{index}-{name}"
                        os.replace(final, backup)
                    try:
                        os.replace(directory / name, final)
                    except BaseException:
                        if backup is not None:
                            _restore(backup, final)
                        raise
                    swapped.append((final, backup))
            except BaseException:
                for final, backup in reversed(swapped):
                    if backup is None:
                        _remove(final)
                    else:
                        _restore(backup, final)
                raise
            return [final for final, _backup in swapped]

    def discard(self) -> None:
        """Remove the staging directory and everything still in it."""

        with self._lock:
            if self._discarded:
                return
            self._discarded = True
            if self._owner_lock is not None:
                self._owner_lock.close()
                self._owner_lock = None
            if self._directory is not None:
                shutil.rmtree(self._directory, ignore_errors=True)
        with _active_stagings_lock:
            _active_stagings.discard(self)

    def _create(self) -> None:
        self._output_directory.mkdir(parents=True, exist_ok=True)
        remove_abandoned_staging(self._output_directory)
        directory = Path(
            tempfile.mkdtemp(
                prefix=f".{self._prefix}_",
                suffix=STAGING_SUFFIX,
                dir=self._output_directory,
            )
        )
        try:
            self._owner_lock = _acquire_owner_lock(directory / OWNER_LOCK_NAME)
        except BaseException:
            shutil.rmtree(directory, ignore_errors=True)
            raise
        self._directory = directory
        with _active_stagings_lock:
            _active_stagings.add(self)
