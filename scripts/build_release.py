"""Build and verify a deterministic, secret-scanned competition source ZIP.

Standard library only. Does not install dependencies, call models, modify Git,
read database rows, or copy configured environment files into the archive.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
ARCHIVE_ROOT = "culture-ai-competition"
SKIP_PARTS = {
    ".git", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache", ".pytest_tmp",
    ".mypy_cache", ".ruff_cache", ".idea", ".vscode", "dist", "build", "release", "releases",
    "logs", "artifacts", "volumes", "model_weights", "uploads", "temporary_rag",
    "runtime", ".cache", ".run", ".verification",
}
SKIP_SUFFIXES = {".pyc", ".pyo", ".log", ".db", ".sqlite", ".sqlite3", ".safetensors", ".gguf", ".pt", ".pth", ".onnx", ".pem", ".key", ".p12", ".zip", ".bundle"}
REQUIRED = {
    "README.md", "package.json", "scripts/dev.mjs", "scripts/build_release.py",
    "Cultural heritage ai/app/main.py", "Cultural heritage ai/requirements.txt",
    "Cultural heritage ai/.env.example", "culture-front/Cultural heritage/package.json",
    "culture-front/Cultural heritage/package-lock.json", "culture-front/Cultural heritage/src/main.ts",
    "deployment/compose.yml", "deployment/sql/mysql_schema.sql", "deployment/database.py",
    "deployment/init_milvus.py", "deployment/health_check.py", "deployment/smoke_test.py",
    "deployment/models/model_sources.json", "deployment/models/qwen3_reranker.jinja",
    "docs/PROJECT_DESCRIPTION.md", "docs/SYSTEM_DESIGN.md", "docs/DEPLOYMENT.md",
    "docs/ENVIRONMENT.md", "docs/JUDGE_GUIDE.md", "docs/DELIVERY_CHECKLIST.md",
    "docs/AUDIT_REPORT.md",
}
KEY_SHAPES = re.compile(r"\b(?:sk-[A-Za-z0-9_-]{20,}|tvly-[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}|AKIA[A-Z0-9]{16})\b")
JWT_SHAPE = re.compile(r"\beyJ[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]{15,}\b")
SECRET_ASSIGNMENT = re.compile(
    r'''(?im)^[ \t]*["']?([A-Z][A-Z0-9_]*(?:PASSWORD|SECRET_KEY|API_KEY|ACCESS_TOKEN))["']?[ \t]*[:=][ \t]*["']?([^\s"'#,}]+)'''
)


class ReleaseError(ValueError):
    pass


def placeholder(value: str) -> bool:
    value = value.lower()
    return not value or value in {"none", "null", "change-me", "dev-secret-key", "unused", "root"} or any(
        value.startswith(prefix) for prefix in ("replace-", "replace_", "your-", "your_", "\u4f60\u7684", "<", "${", "offline-", "test-", "sharedpass", "legacytest")
    )


def configured_private_values(backend: Path) -> set[bytes]:
    """Only exact-match fingerprints are retained in memory; never log the values."""
    values = set()
    for name in (".env", ".env.development", ".env.production"):
        path = backend / name
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            match = re.match(r"\s*([A-Z][A-Z0-9_]*)\s*=\s*(.*?)\s*$", line)
            if match and re.search(r"PASSWORD|SECRET|API_KEY|TOKEN|_HOST$", match[1]):
                value = match[2].strip("\"'")
                if len(value) >= 8 and not placeholder(value) and value not in {"127.0.0.1", "localhost"}:
                    values.add(value.encode())
    return values


def scan_bytes(data: bytes, name: str, private_values: set[bytes] | None = None) -> list[str]:
    issues = []
    if any(value in data for value in (private_values or set())):
        issues.append("configured private value")
    if b"\x00" in data:
        return issues
    source = data.decode("utf-8-sig", errors="replace")
    for match in KEY_SHAPES.finditer(source):
        if not match[0].startswith(("sk-test-", "sk-offline-")):
            issues.append("credential-shaped literal")
    if JWT_SHAPE.search(source):
        issues.append("JWT-shaped literal")
    if re.search(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----", source):
        issues.append("private key")
    if re.search(r"\b(?:mysql|postgres(?:ql)?|redis)://[^\s/:]+:([^@\s]+)@", source):
        for match in re.finditer(r"\b(?:mysql|postgres(?:ql)?|redis)://[^\s/:]+:([^@\s]+)@", source):
            if not placeholder(match[1]) and not match[1].startswith(("{", "$")):
                issues.append("credential in connection URL")
    if name.endswith(".sql") and re.search(r"\b(?:INSERT|REPLACE)\s+INTO\s+`?(?:accounts|ai_agents|tasks|answers|rag_tasks)\b", source, re.IGNORECASE):
        issues.append("database user/runtime data")
    for match in SECRET_ASSIGNMENT.finditer(source):
        value = match[2]
        # Explicit unit-test identities are not configured deployment credentials.
        if (len(value) >= 12 and not placeholder(value) and not any(char in value for char in "({[\\")
                and "/tests/" not in "/" + name.replace("\\", "/")):
            issues.append("non-placeholder secret assignment")
    return sorted(set(issues))


def included(path: Path, relative: Path) -> bool:
    if set(relative.parts) & SKIP_PARTS or path.suffix.lower() in SKIP_SUFFIXES:
        return False
    if path.name.startswith(".env") and not path.name.endswith(".example"):
        return False
    return not (path.name.startswith(".") and path.name not in {".env.example", ".gitignore", ".gitattributes", ".editorconfig", ".prettierrc.json", ".oxlintrc.json"})


def collect_tree(source: Path, destination: str, files: dict[str, bytes]) -> None:
    source = source.resolve()
    if not source.is_dir():
        return
    for directory, dirs, names in os.walk(source):
        dirs[:] = sorted(name for name in dirs if name not in SKIP_PARTS
                         and not (Path(directory) / name).is_symlink()
                         and (Path(directory) / name).resolve().is_relative_to(source))
        for name in sorted(names):
            path = Path(directory) / name
            relative = path.relative_to(source)
            if not included(path, relative):
                continue
            if path.is_symlink() or not path.resolve().is_relative_to(source):
                raise ReleaseError("Link outside the selected source: " + relative.as_posix())
            if path.stat().st_size > 10 * 1024 * 1024:
                raise ReleaseError("Unexpected large release asset: " + relative.as_posix())
            files[(PurePosixPath(destination) / relative.as_posix()).as_posix()] = path.read_bytes()


def git(repo: Path, *args: str) -> bytes:
    return subprocess.check_output(["git", "-c", f"safe.directory={repo.as_posix()}", *args], cwd=repo, stderr=subprocess.DEVNULL)


def scan_history_objects(repo: Path, private_values: set[bytes]) -> int:
    objects = [line.partition(" ") for line in git(repo, "rev-list", "--objects", "--all").decode().splitlines()]
    requests = ("\n".join(item[0] for item in objects) + "\n").encode()
    command = ["git", "-c", f"safe.directory={repo.as_posix()}", "cat-file"]
    sizes = subprocess.check_output([*command, "--batch-check"], cwd=repo, input=requests, stderr=subprocess.DEVNULL)
    if sum(int(line.split()[2]) for line in sizes.splitlines()) > 200 * 1024 * 1024:
        raise ReleaseError("Git history exceeds source delivery size limit")
    raw = subprocess.check_output([*command, "--batch"], cwd=repo, input=requests, stderr=subprocess.DEVNULL)
    checked, offset = 0, 0
    for oid, _, name in objects:
        boundary = raw.index(b"\n", offset)
        actual_oid, kind, size = raw[offset:boundary].decode().split()
        if actual_oid != oid:
            raise ReleaseError("Git batch object mismatch")
        offset = boundary + 1
        data = raw[offset:offset + int(size)]
        offset += int(size) + 1
        if kind not in {"blob", "commit", "tag"}:
            continue
        issues = scan_bytes(data, name or f"git-{kind}", private_values)
        if kind == "blob" and Path(name).name.startswith(".env") and not Path(name).name.endswith(".example"):
            issues.append("historical configured environment file")
        if issues:
            raise ReleaseError(f"Unsafe Git history {oid[:12]} ({name or kind}): {', '.join(issues)} [values redacted]")
        checked += kind == "blob"
    return checked


def history_bundle(repo: Path, private_values: set[bytes]) -> tuple[bytes | None, dict]:
    try:
        revision = git(repo, "rev-parse", "HEAD").decode().strip()
        if not re.fullmatch(r"[0-9a-f]{40,64}", revision):
            raise subprocess.CalledProcessError(1, "git rev-parse HEAD")
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None, {"status": "no_committed_history"}
    checked = scan_history_objects(repo, private_values)
    with tempfile.TemporaryDirectory(prefix="heritage-git-") as directory:
        target = Path(directory) / "history.bundle"
        git(repo, "bundle", "create", str(target), "--all")
        bundle = target.read_bytes()
    return bundle, {"status": "included_scanned_bundle", "head": revision, "blobs_scanned": checked}


def verify_bundled_history(data: bytes, private_values: set[bytes]) -> set[str]:
    with tempfile.TemporaryDirectory(prefix="heritage-bundle-check-") as directory:
        root = Path(directory)
        bundle = root / "source.bundle"
        bundle.write_bytes(data)
        repository = root / "repository"
        subprocess.run(["git", "init", "--bare", "-q", str(repository)], check=True, capture_output=True)
        git(repository, "fetch", "--quiet", str(bundle), "refs/*:refs/*")
        scan_history_objects(repository, private_values)
        return set(git(repository, "for-each-ref", "--format=%(objectname)").decode().splitlines())


def sanitized_history_bundle(repo: Path, private_values: set[bytes]) -> tuple[bytes | None, dict]:
    """Export original HEAD ancestry into a temporary repository; never rewrite source Git.

    Commit identities/dates/messages/topology are retained with an explicit export note.
    Configured environments, runtime artifacts and unsafe blobs are excluded. Rewritten
    hashes and exclusions are disclosed; this is not presented as the original bundle.
    """
    try:
        commits = git(repo, "rev-list", "--topo-order", "--reverse", "HEAD").decode().splitlines()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None, {"status": "no_committed_history"}
    allowed_roots = {"app", "tests", "scripts", "evaluation", "migrations", "sql"}
    allowed_files = {"README.md", "requirements.txt", "requirements-dev.txt", "requirements-lock-windows-py311.txt", "pytest.ini", "ruff.toml", "mypy.ini", ".env.example", ".gitignore"}
    excluded, cached, rewritten = set(), {}, {}
    with tempfile.TemporaryDirectory(prefix="heritage-clean-history-") as directory:
        target = Path(directory)
        subprocess.run(["git", "init", "--bare", "-q", str(target)], check=True, capture_output=True)

        def object_write(kind: str, data: bytes) -> str:
            return subprocess.check_output(["git", "--git-dir", str(target), "hash-object", "-w", "-t", kind, "--stdin"], input=data).decode().strip()

        def make_tree(tree: dict) -> str:
            entries = []
            for name, item in sorted(tree.items()):
                mode, kind, oid = ("040000", "tree", make_tree(item)) if isinstance(item, dict) else item
                entries.append(f"{mode} {kind} {oid}\t{name}".encode() + b"\x00")
            return subprocess.check_output(["git", "--git-dir", str(target), "mktree", "-z"], input=b"".join(entries)).decode().strip()

        for commit in commits:
            header, _, message = git(repo, "cat-file", "-p", commit).partition(b"\n\n")
            headers = header.decode().splitlines()
            parents = [line.split()[1] for line in headers if line.startswith("parent ")]
            tree = {}
            for entry in git(repo, "ls-tree", "-rz", "--full-tree", commit).split(b"\x00"):
                if not entry:
                    continue
                info, raw_name = entry.split(b"\t", 1)
                mode, kind, oid = info.decode().split()
                name = raw_name.decode()
                path = Path(name)
                allowed = ((path.parts[0] in allowed_roots and "/evaluation/reports/" not in "/" + name) or name in allowed_files or name.startswith("data/rag_eval/")
                           or name == "data/test_upload/temple_of_heaven.txt")
                if not allowed or kind != "blob" or mode not in {"100644", "100755"} or not included(path, path):
                    excluded.add(name)
                    continue
                if oid not in cached:
                    raw = git(repo, "cat-file", "-p", oid)
                    cached[oid] = None if scan_bytes(raw, name, private_values) else object_write("blob", raw)
                if cached[oid] is None:
                    excluded.add(name)
                    continue
                current = tree
                for part in path.parts[:-1]:
                    current = current.setdefault(part, {})
                current[path.name] = (mode, "blob", cached[oid])
            new_header = ["tree " + make_tree(tree)] + ["parent " + rewritten[parent] for parent in parents]
            new_header += [line for line in headers if line.startswith(("author ", "committer ", "encoding "))]
            note = f"\n\nDelivery sanitized export; original commit {commit}. Private/runtime files excluded.\n"
            rewritten[commit] = object_write("commit", ("\n".join(new_header) + "\n\n").encode() + message.rstrip() + note.encode())
        git(target, "update-ref", "refs/heads/review-source", rewritten[commits[-1]])
        git(target, "symbolic-ref", "HEAD", "refs/heads/review-source")
        bundle, evidence = history_bundle(target, private_values)
    evidence.update(status="included_sanitized_bundle", original_head=commits[-1], commits=len(commits), excluded_paths=sorted(excluded))
    return bundle, evidence


def verify_archive(path: Path, private_values: set[bytes] | None = None) -> dict:
    with zipfile.ZipFile(path) as archive:
        entries = archive.infolist()
        if len(entries) > 10000 or sum(entry.file_size for entry in entries) > 200 * 1024 * 1024:
            raise ReleaseError("Archive exceeds source delivery size limits")
        names = [entry.filename for entry in entries]
        if len(names) != len(set(names)):
            raise ReleaseError("Duplicate ZIP path")
        for entry in entries:
            parts = PurePosixPath(entry.filename).parts
            if not parts or parts[0] != ARCHIVE_ROOT or ".." in parts or "\\" in entry.filename or ":" in entry.filename:
                raise ReleaseError("Unsafe ZIP path")
            if (entry.external_attr >> 16) & 0o170000 == 0o120000:
                raise ReleaseError("Symlink in ZIP")
            relative = "/".join(parts[1:])
            if set(parts) & SKIP_PARTS or (Path(relative).suffix.lower() in SKIP_SUFFIXES and relative not in {"source-history/backend.bundle", "source-history/frontend.bundle"}):
                raise ReleaseError("Excluded runtime asset in ZIP")
            if Path(relative).name.startswith(".env") and not Path(relative).name.endswith(".example"):
                raise ReleaseError("Configured environment file in ZIP")
            if relative in {"source-history/backend.bundle", "source-history/frontend.bundle"}:
                verify_bundled_history(archive.read(entry), private_values or set())
            else:
                issues = scan_bytes(archive.read(entry), relative, private_values)
                if issues:
                    raise ReleaseError(f"Archive scan: {relative}: {', '.join(issues)} [values redacted]")
        if archive.testzip() is not None:
            raise ReleaseError("ZIP CRC failure")
        manifest = json.loads(archive.read(f"{ARCHIVE_ROOT}/RELEASE_MANIFEST.json"))
        for name, digest in manifest["files"].items():
            data = archive.read(f"{ARCHIVE_ROOT}/{name}")
            if hashlib.sha256(data).hexdigest() != digest["sha256"] or len(data) != digest["bytes"]:
                raise ReleaseError("Manifest mismatch: " + name)
        actual = {name.removeprefix(ARCHIVE_ROOT + "/") for name in names}
        if set(manifest["files"]) | {"RELEASE_MANIFEST.json"} != actual:
            raise ReleaseError("Archive contains unmanifested files")
        if not REQUIRED <= actual:
            raise ReleaseError("Missing required release structure: " + ", ".join(sorted(REQUIRED - actual)))
        # Extraction validation uses a newly created temporary directory, never user data.
        with tempfile.TemporaryDirectory(prefix="heritage-extract-") as directory:
            archive.extractall(directory)
            root = Path(directory) / ARCHIVE_ROOT
            if not all((root / name).is_file() for name in REQUIRED):
                raise ReleaseError("Extracted layout verification failed")
    return {"status": "passed", "files": len(names), "uncompressed_bytes": sum(entry.file_size for entry in entries)}


def build(root: Path, backend: Path, frontend: Path, output: Path, *, include_history: bool = True, repository_url: str | None = None, sanitize_history: bool = False) -> dict:
    files: dict[str, bytes] = {}
    for name in ["app", "tests", "scripts", "evaluation", "migrations"]:
        collect_tree(backend / name, f"Cultural heritage ai/{name}", files)
    # SQL is explicitly selected: never ship newly exported account/task INSERT scripts.
    for name in ["createsql.sql", "createtable.sql"]:
        source = backend / "sql" / name
        if source.is_file():
            files[f"Cultural heritage ai/sql/{name}"] = source.read_bytes()
    # Preserve source evaluation code and fixtures, omit historical/private runtime reports.
    files = {name: data for name, data in files.items() if "/evaluation/reports/" not in name or name.endswith(".py")}
    collect_tree(backend / "data/rag_eval", "Cultural heritage ai/data/rag_eval", files)
    demo = backend / "data/test_upload/temple_of_heaven.txt"
    if demo.is_file():
        files["Cultural heritage ai/data/test_upload/temple_of_heaven.txt"] = demo.read_bytes()
    for name in ["requirements.txt", "requirements-dev.txt", "requirements-lock-windows-py311.txt", "pytest.ini", "ruff.toml", "mypy.ini", "README.md", ".env.example", ".gitignore"]:
        if (backend / name).is_file():
            files[f"Cultural heritage ai/{name}"] = (backend / name).read_bytes()
    for name in ["src", "public", "tests"]:
        collect_tree(frontend / name, f"culture-front/Cultural heritage/{name}", files)
    for path in sorted(frontend.iterdir()):
        if path.is_file() and included(path, Path(path.name)) and path.suffix in {".json", ".ts", ".html"}:
            files[f"culture-front/Cultural heritage/{path.name}"] = path.read_bytes()
    for name in ["README.md", ".env.example", ".gitignore", ".gitattributes", ".editorconfig", ".prettierrc.json", ".oxlintrc.json"]:
        if (frontend / name).is_file():
            files[f"culture-front/Cultural heritage/{name}"] = (frontend / name).read_bytes()
    for name in ["deployment", "docs", "tests"]:
        collect_tree(root / name, name, files)
    for name in ["README.md", "package.json", "scripts/dev.mjs", "scripts/build_release.py"]:
        if (root / name).is_file():
            files[name] = (root / name).read_bytes()
    private = configured_private_values(backend)
    for name, data in files.items():
        issues = scan_bytes(data, name, private)
        if issues:
            raise ReleaseError(f"Source scan: {name}: {', '.join(issues)} [values redacted]")
    history = {}
    if include_history:
        for label, repo in [("backend", backend), ("frontend", frontend)]:
            bundle, evidence = (sanitized_history_bundle if sanitize_history else history_bundle)(repo, private)
            # An extracted delivery has no .git; retain its independently reverified
            # history without inventing commits or changing original provenance.
            retained = root / f"source-history/{label}.bundle"
            provenance = root / "SOURCE_HISTORY.json"
            if bundle is None and retained.is_file() and provenance.is_file():
                candidate = retained.read_bytes()
                heads = verify_bundled_history(candidate, private)
                prior = json.loads(provenance.read_text(encoding="utf-8"))["repositories"].get(label, {})
                if prior.get("head") not in heads or not prior.get("status", "").startswith("included_"):
                    raise ReleaseError("Retained bundle does not match disclosed history")
                bundle, evidence = candidate, prior
            history[label] = evidence
            if bundle:
                files[f"source-history/{label}.bundle"] = bundle
        if not any(item["status"].startswith("included_") for item in history.values()):
            raise ReleaseError("No committed Git history found; provide an accessible --repository-url and --no-history")
    else:
        parsed = urlsplit(repository_url or "")
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query:
            raise ReleaseError("Omitting history requires a clean HTTPS repository URL")
        history["reference"] = {"url": repository_url, "note": "Submitter must verify reviewer access and contest rules"}
    files["SOURCE_HISTORY.json"] = (json.dumps({"repositories": history, "note": "ZIP contains current worktree source. History is separate; sanitized exports disclose rewritten hashes and excluded paths. Original repositories remain unchanged."}, sort_keys=True, indent=2) + "\n").encode()
    missing = REQUIRED - set(files)
    if missing:
        raise ReleaseError("Missing required files: " + ", ".join(sorted(missing)))
    manifest = {"format": 1, "files": {name: {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)} for name, data in sorted(files.items())}}
    files["RELEASE_MANIFEST.json"] = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(".zip.tmp")
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for name, data in sorted(files.items()):
                info = zipfile.ZipInfo(f"{ARCHIVE_ROOT}/{name}", date_time=(2026, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.create_system = 3
                info.external_attr = (0o100755 if name.endswith(".sh") else 0o100644) << 16
                archive.writestr(info, data)
        result = verify_archive(temporary, private)
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    output.with_suffix(".sha256").write_text(f"{digest}  {output.name}\n", encoding="utf-8")
    result.update(zip=output.name, compressed_bytes=output.stat().st_size, sha256=digest, history=history)
    output.with_suffix(".verification.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", type=Path, default=ROOT / "Cultural heritage ai")
    parser.add_argument("--frontend", type=Path, default=ROOT / "culture-front/Cultural heritage")
    parser.add_argument("--output", type=Path, default=ROOT / "release/culture-ai-competition.zip")
    parser.add_argument("--no-history", action="store_true")
    parser.add_argument("--sanitize-history", action="store_true", help="Export safe HEAD ancestry in a temporary repository; original Git is unchanged")
    parser.add_argument("--repository-url")
    parser.add_argument("--verify-only", type=Path)
    args = parser.parse_args()
    try:
        result = verify_archive(args.verify_only) if args.verify_only else build(
            ROOT, args.backend.resolve(), args.frontend.resolve(), args.output.resolve(),
            include_history=not args.no_history, repository_url=args.repository_url,
            sanitize_history=args.sanitize_history,
        )
    except (ReleaseError, OSError, subprocess.CalledProcessError, zipfile.BadZipFile) as error:
        raise SystemExit(str(error)) from None
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
