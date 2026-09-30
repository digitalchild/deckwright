"""Render decks to PDF and PNG with LibreOffice, using the template's brand fonts."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path

PROFILE_DIR = Path(os.environ.get("DECKWRIGHT_CACHE", Path.home() / ".cache" / "deckwright")) / "lo-profile"


class RenderError(RuntimeError):
    pass


_thread_lock = threading.Lock()


@contextmanager
def _render_lock():
    """Serialise LibreOffice runs: they share one user profile, which soffice locks.

    The file lock covers several processes (API workers, MCP servers); the thread
    lock covers the API's thread pool on platforms without fcntl.
    """
    PROFILE_DIR.parent.mkdir(parents=True, exist_ok=True)
    with _thread_lock:
        try:
            import fcntl
        except ImportError:  # Windows
            yield
            return
        with open(PROFILE_DIR.parent / ".render.lock", "w") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(fh, fcntl.LOCK_UN)


def soffice_path() -> str:
    candidates = [
        os.environ.get("DECKWRIGHT_SOFFICE"),
        shutil.which("soffice"),
        shutil.which("libreoffice"),
        "/Applications/LibreOffice.app/Contents/MacOS/soffice",
    ]
    for c in candidates:
        if c and Path(c).exists():
            return c
    raise RenderError("LibreOffice not found. Install it or set DECKWRIGHT_SOFFICE.")


def _profile(fonts: Path | None) -> Path:
    """A private LibreOffice profile whose user font folder holds the brand fonts."""
    font_dir = PROFILE_DIR / "user" / "fonts"
    font_dir.mkdir(parents=True, exist_ok=True)
    if fonts is not None and fonts.is_dir():
        for f in fonts.glob("*.ttf"):
            dst = font_dir / f.name
            if not dst.exists() or dst.stat().st_mtime < f.stat().st_mtime:
                shutil.copy2(f, dst)
    return PROFILE_DIR


def to_pdf(pptx: Path, out_dir: Path | None = None, timeout: int = 240, fonts: Path | None = None) -> Path:
    out_dir = out_dir or pptx.parent
    binary = soffice_path()
    with _render_lock():
        cmd = [
            binary,
            f"-env:UserInstallation=file://{_profile(fonts)}",
            "--headless", "--convert-to", "pdf", "--outdir", str(out_dir), str(pptx),
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    pdf = out_dir / (pptx.stem + ".pdf")
    if proc.returncode != 0 or not pdf.exists():
        raise RenderError(f"LibreOffice failed: {proc.stderr.strip() or proc.stdout.strip()}")
    return pdf


def to_pngs(pptx: Path, out_dir: Path, dpi: int = 60, first: int | None = None, last: int | None = None,
            fonts: Path | None = None) -> list[Path]:
    if not shutil.which("pdftoppm"):
        raise RenderError("pdftoppm (poppler) not found.")
    out_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        pdf = to_pdf(pptx, Path(tmp), fonts=fonts)
        cmd = ["pdftoppm", "-png", "-r", str(dpi)]
        if first:
            cmd += ["-f", str(first)]
        if last:
            cmd += ["-l", str(last)]
        prefix = out_dir / pptx.stem
        for old in out_dir.glob(f"{pptx.stem}-*.png"):
            old.unlink()
        subprocess.run([*cmd, str(pdf), str(prefix)], check=True, capture_output=True)
    return sorted(out_dir.glob(f"{pptx.stem}-*.png"))
