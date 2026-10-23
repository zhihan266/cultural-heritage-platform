"""Delivery guarantees: secrets, history, traversal, deterministic archives and schema."""

import hashlib
import importlib.util
import json
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
spec = importlib.util.spec_from_file_location('release_builder', ROOT / 'scripts/build_release.py')
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


@pytest.fixture
def source(tmp_path, monkeypatch):
    backend, frontend = tmp_path / 'backend', tmp_path / 'frontend'
    backend.mkdir()
    frontend.mkdir()
    (tmp_path / 'README.md').write_text('Public source', encoding='utf-8')
    monkeypatch.setattr(release, 'REQUIRED', {'README.md'})
    return tmp_path, backend, frontend


def make_release(source, **kwargs):
    root, backend, frontend = source
    return release.build(root, backend, frontend, root / 'out.zip', include_history=False,
                         repository_url='https://example.org/contest/source', **kwargs)


def test_archive_is_deterministic_and_manifest_checked(source):
    first = make_release(source)
    second = make_release(source)
    assert first['sha256'] == second['sha256']
    assert first['status'] == 'passed'
    with zipfile.ZipFile(source[0] / 'out.zip') as archive:
        manifest = json.loads(archive.read(release.ARCHIVE_ROOT + '/RELEASE_MANIFEST.json'))
        assert manifest['files']['README.md']['sha256'] == hashlib.sha256(b'Public source').hexdigest()


def test_runtime_and_environment_are_excluded_but_python_models_kept(source):
    root, backend, _ = source
    for name in ['app/models/entity.py', 'app/__pycache__/bad.pyc', 'app/model_weights/model.safetensors',
                 'app/.env', 'app/uploads/private.txt']:
        path = backend / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('fixture', encoding='utf-8')
    make_release(source)
    with zipfile.ZipFile(root / 'out.zip') as archive:
        names = archive.namelist()
        assert any(name.endswith('app/models/entity.py') for name in names)
        assert not any(name.endswith(('bad.pyc', 'model.safetensors', '.env', 'private.txt')) for name in names)


def test_empty_key_does_not_consume_next_env_line():
    assert not release.scan_bytes(b'EMBEDDING_API_KEY=\nEMBEDDING_MODEL=qwen3-embedding\n', '.env.example')


@pytest.mark.parametrize('data', [b'sk-' + b'Z' * 24, b'-----BEGIN ' + b'PRIVATE KEY-----',
                                   b'DATABASE_PASSWORD=' + b'nonplaceholderprivatevalue'])
def test_secret_patterns_are_rejected(data):
    assert release.scan_bytes(data, 'app/config.py')


def test_actual_private_value_is_rejected_even_without_recognizable_shape():
    assert release.scan_bytes(b'prefix incidentalphrase suffix', 'readme.txt', {b'incidentalphrase'})


def test_tampered_archive_is_rejected(source):
    make_release(source)
    path = source[0] / 'out.zip'
    with zipfile.ZipFile(path) as archive:
        files = {name: archive.read(name) for name in archive.namelist()}
    files[release.ARCHIVE_ROOT + '/README.md'] = b'Tampered'
    with zipfile.ZipFile(path, 'w') as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    with pytest.raises(release.ReleaseError, match='Manifest mismatch'):
        release.verify_archive(path)


@pytest.mark.parametrize('name', ['../escape.txt', 'culture-ai-competition/../../escape.txt',
                                'culture-ai-competition/node_modules/private.txt',
                                'culture-ai-competition/.env'])
def test_unsafe_archive_paths_are_rejected(tmp_path, name):
    path = tmp_path / 'unsafe.zip'
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr(name, b'x')
    with pytest.raises(release.ReleaseError):
        release.verify_archive(path)


def test_historical_secret_prevents_bundle(tmp_path):
    subprocess.run(['git', 'init', '-q', str(tmp_path)], check=True)
    private = tmp_path / 'config.py'
    private.write_bytes(b'API_KEY=' + b'sk-' + b'Z' * 24)
    release.git(tmp_path, 'add', '.')
    release.git(tmp_path, '-c', 'user.name=Delivery Test', '-c', 'user.email=test@example.invalid',
                'commit', '-qm', 'historical secret fixture')
    private.write_text('clean source', encoding='utf-8')
    release.git(tmp_path, 'add', '.')
    release.git(tmp_path, '-c', 'user.name=Delivery Test', '-c', 'user.email=test@example.invalid',
                'commit', '-qm', 'clean current tree')
    with pytest.raises(release.ReleaseError, match='Unsafe Git history'):
        release.history_bundle(tmp_path, set())


def test_sanitized_history_is_disclosed_and_source_git_unchanged(tmp_path):
    subprocess.run(['git', 'init', '-q', str(tmp_path)], check=True)
    (tmp_path / 'app').mkdir()
    (tmp_path / 'app/main.py').write_text('print(1)', encoding='utf-8')
    (tmp_path / '.env.production').write_bytes(b'API_KEY=' + b'sk-' + b'Z' * 24)
    release.git(tmp_path, 'add', '.')
    release.git(tmp_path, '-c', 'user.name=Delivery Test', '-c', 'user.email=test@example.invalid',
                'commit', '-qm', 'source fixture')
    original = release.git(tmp_path, 'rev-parse', 'HEAD')
    bundle, evidence = release.sanitized_history_bundle(tmp_path, set())
    assert bundle and evidence['status'] == 'included_sanitized_bundle'
    assert evidence['commits'] == 1
    assert '.env.production' in evidence['excluded_paths']
    assert release.git(tmp_path, 'rev-parse', 'HEAD') == original
    bundle_path = tmp_path / 'safe.bundle'
    bundle_path.write_bytes(bundle)
    clone = tmp_path / 'review-clone'
    subprocess.run(['git', 'clone', '-q', '--branch', 'review-source', str(bundle_path), str(clone)], check=True)
    assert (clone / 'app/main.py').read_text() == 'print(1)'
    assert not (clone / '.env.production').exists()
    assert b'Delivery sanitized export' in release.git(clone, 'log', '-1', '--format=%B')


def test_schema_stable_between_python_processes():
    command = 'from deployment.database import render_schema; import hashlib; print(hashlib.sha256(render_schema().encode()).hexdigest())'
    hashes = [subprocess.check_output([sys.executable, '-c', command], cwd=ROOT).strip() for _ in range(2)]
    assert hashes[0] == hashes[1]
    schema = (ROOT / 'deployment/sql/mysql_schema.sql').read_bytes()
    assert hashes[0].decode() == hashlib.sha256(schema).hexdigest()
    assert schema.count(b'CREATE TABLE ') == 19
    assert not any(word in schema.upper() for word in [b'DROP TABLE', b'INSERT INTO', b'DELETE FROM'])


def test_extracted_release_reuses_verified_history_and_rebuilds_identically(source):
    root, backend, frontend = source
    (frontend / 'package.json').write_text('{}', encoding='utf-8')
    subprocess.run(['git', 'init', '-q', str(backend)], check=True)
    (backend / 'app').mkdir()
    (backend / 'app/main.py').write_text('print(1)', encoding='utf-8')
    release.git(backend, 'add', '.')
    release.git(backend, '-c', 'user.name=Delivery Test', '-c', 'user.email=test@example.invalid',
                'commit', '-qm', 'source fixture')
    first = release.build(root, backend, frontend, root / 'first.zip', sanitize_history=True)
    with zipfile.ZipFile(root / 'first.zip') as archive:
        archive.extractall(root / 'extracted')
    extracted = root / 'extracted' / release.ARCHIVE_ROOT
    second = release.build(extracted, extracted / 'Cultural heritage ai',
                           extracted / 'culture-front/Cultural heritage', root / 'second.zip',
                           sanitize_history=True)
    assert first['sha256'] == second['sha256']
