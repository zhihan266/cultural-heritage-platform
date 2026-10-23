"""Redacted Git publication scan. Use --history before publishing existing Git history."""
from __future__ import annotations

import argparse
import ipaddress
import io
import re
import subprocess
from pathlib import Path
from zipfile import BadZipFile, ZipFile

ROOT = Path(__file__).resolve().parents[1]
IP_PATTERN = re.compile(r"(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w.])")
KEY_PATTERN = re.compile(r"\b(?:sk-[A-Za-z0-9_-]{20,}|tvly-[A-Za-z0-9_-]{20,}|ghp_[A-Za-z0-9]{30,}|AKIA[A-Z0-9]{16})\b")
PERSONAL_PATH_PATTERN = re.compile(r"(?:[A-Z]:[\\/]Users[\\/][^\s\\/]+|/" + r"home/[^\s/]+/)", re.I)
RUNTIME_PARTS = {".venv", "venv", ".idea", ".vscode", "__pycache__", "logs", "runtime", "uploads", "temporary_rag"}


def git(*args: str) -> bytes:
    return subprocess.check_output(["git", *args], cwd=ROOT)


def findings(content: str) -> set[str]:
    result = set()
    if KEY_PATTERN.search(content):
        result.add("credential-shaped value")
    if PERSONAL_PATH_PATTERN.search(content):
        result.add("personal absolute path")
    for match in IP_PATTERN.finditer(content):
        try:
            address = ipaddress.ip_address(match.group())
        except ValueError:
            continue
        # Localhost and binding addresses are safe examples; include private and CGNAT addresses.
        if not address.is_loopback and not address.is_unspecified and (
            address.is_private or (address.version == 4 and int(address) >> 22 == 401)
        ):
            result.add("private server address")
    return result


def decode_content(raw: bytes, suffix: str) -> str:
    if suffix == ".docx":
        try:
            with ZipFile(io.BytesIO(raw)) as archive:
                return "\n".join(archive.read(name).decode("utf-8", errors="replace")
                                 for name in archive.namelist() if name.endswith(".xml"))
        except BadZipFile:
            return ""
    if b"\x00" in raw and not raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return ""
    encoding = "utf-16" if raw.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig"
    return raw.decode(encoding, errors="replace")


def scan_current() -> int:
    # Include untracked publishable source, but respect ignore rules and deleted worktree files.
    names = set(git("ls-files", "-z", "--cached", "--others", "--exclude-standard").decode().split("\0"))
    count = 0
    failures = 0
    for name in sorted(names - {""}):
        path = ROOT / name
        if not path.is_file():
            continue
        count += 1
        issues = findings(decode_content(path.read_bytes(), path.suffix))
        if path.name.startswith(".env") and path.name != ".env.example":
            issues.add("tracked environment file")
        if RUNTIME_PARTS.intersection(path.parts) or path.suffix in {".log", ".pyc"}:
            issues.add("tracked runtime/editor artifact")
        for issue in sorted(issues):
            print(f"{name}: {issue} [value redacted]")
            failures += 1
    print(f"Current snapshot: {count} files checked, {failures} findings")
    return failures


def scan_index() -> int:
    failures = 0
    count = 0
    for name in git("ls-files", "-z").decode().split("\0"):
        if not name:
            continue
        count += 1
        content = decode_content(git("show", ":" + name), Path(name).suffix)
        issues = findings(content)
        if Path(name).name.startswith(".env") and Path(name).name != ".env.example":
            issues.add("tracked environment file")
        if Path(name).suffix == ".docx":
            # Authors in document fixtures also need review, even when no credential shape matches.
            authors = re.findall(r"<(?:\w+:)?(?:creator|lastModifiedBy)[^>]*>([^<]*)<", content)
            if any(author.strip() not in {"", "Project fixture"} for author in authors):
                issues.add("document author metadata needs review")
        for issue in sorted(issues):
            print(f"Index {name}: {issue} [value redacted]")
            failures += 1
    print(f"Index: {count} files checked, {failures} findings")
    return failures


def scan_history() -> int:
    failures = 0
    checked = 0
    for line in git("rev-list", "--objects", "--all").decode().splitlines():
        oid, _, name = line.partition(" ")
        if git("cat-file", "-t", oid).strip() != b"blob":
            continue
        raw = git("cat-file", "-p", oid)
        encoding = "utf-16" if raw.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8"
        if b"\0" in raw and encoding == "utf-8":
            continue
        checked += 1
        for issue in sorted(findings(raw.decode(encoding, errors="replace"))):
            print(f"History {oid[:10]} {name}: {issue} [value redacted]")
            failures += 1
    print(f"History: {checked} text blobs checked, {failures} findings")
    return failures


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--history", action="store_true")
    parser.add_argument("--index", action="store_true")
    args = parser.parse_args()
    failures = scan_current()
    if args.index:
        failures += scan_index()
    if args.history:
        failures += scan_history()
    raise SystemExit(1 if failures else 0)


if __name__ == "__main__":
    main()
