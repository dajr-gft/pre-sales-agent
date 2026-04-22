"""Unit tests for ``app.tools.sow._icon_downloader``.

All network access is mocked. The real GCS client is never instantiated.
"""
from __future__ import annotations

import sys
import zipfile
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.tools.sow import _icon_downloader as icd


@pytest.fixture
def patched_cache_dir(tmp_path, monkeypatch):
    """Point the module's cache dir at a temp location for the test."""
    cache = tmp_path / 'gcp-icons'
    monkeypatch.setattr(icd, '_ICON_CACHE_DIR', cache)
    return cache


def _fake_zip_bytes(svg_names: list[str]) -> bytes:
    buf = BytesIO()
    with zipfile.ZipFile(buf, 'w') as zf:
        for name in svg_names:
            zf.writestr(name, '<svg/>')
    return buf.getvalue()


class TestCacheHit:
    def test_existing_cache_returned_without_download(
        self, patched_cache_dir, monkeypatch
    ):
        patched_cache_dir.mkdir(parents=True)
        (patched_cache_dir / 'Cloud_Run.svg').write_text('<svg/>')
        monkeypatch.setattr(icd, '_ICON_BUCKET', 'my-bucket')

        with patch.object(icd, 'tempfile') as tempfile_mock:
            result = icd.ensure_icons_available()

        assert result == patched_cache_dir
        tempfile_mock.NamedTemporaryFile.assert_not_called()

    def test_empty_cache_triggers_download(
        self, patched_cache_dir, monkeypatch
    ):
        patched_cache_dir.mkdir(parents=True)
        # no .svg files in cache → should attempt download
        monkeypatch.setattr(icd, '_ICON_BUCKET', 'my-bucket')
        with patch.object(icd, 'tempfile') as tempfile_mock:
            tempfile_mock.NamedTemporaryFile.side_effect = RuntimeError('no')
            icd.ensure_icons_available()
        # If NamedTemporaryFile was reached, we tried to download.
        tempfile_mock.NamedTemporaryFile.assert_called_once()


class TestMissingConfig:
    def test_no_bucket_env_returns_none(
        self, patched_cache_dir, monkeypatch
    ):
        monkeypatch.setattr(icd, '_ICON_BUCKET', None)
        assert icd.ensure_icons_available() is None

    def test_empty_bucket_env_returns_none(
        self, patched_cache_dir, monkeypatch
    ):
        monkeypatch.setattr(icd, '_ICON_BUCKET', '')
        assert icd.ensure_icons_available() is None

    def test_google_cloud_storage_unavailable_returns_none(
        self, patched_cache_dir, monkeypatch
    ):
        monkeypatch.setattr(icd, '_ICON_BUCKET', 'my-bucket')
        # Make `from google.cloud import storage` raise ImportError
        original_import = __builtins__['__import__'] if isinstance(
            __builtins__, dict
        ) else __import__

        def fake_import(name, *args, **kwargs):
            if name == 'google.cloud' and 'storage' in (args[2] or ()):
                raise ImportError('no storage')
            if name.startswith('google.cloud') and name.endswith('storage'):
                raise ImportError('no storage')
            return original_import(name, *args, **kwargs)

        # Simpler: stub the import itself at sys.modules level
        monkeypatch.setitem(sys.modules, 'google.cloud.storage', None)
        # With None, `from google.cloud import storage` returns None which
        # isn't an ImportError — so we use a different strategy: patch the
        # from-import inline.
        with patch.dict(
            sys.modules, {'google.cloud.storage': None}
        ), patch(
            'builtins.__import__',
            side_effect=lambda n, *a, **k: (_ for _ in ()).throw(
                ImportError('no storage')
            ) if n == 'google.cloud' and a and 'storage' in (a[2] or ()) else original_import(n, *a, **k),
        ):
            result = icd.ensure_icons_available()
        assert result is None


class TestSuccessfulDownload:
    def test_downloads_and_extracts_svgs(
        self, patched_cache_dir, tmp_path, monkeypatch
    ):
        monkeypatch.setattr(icd, '_ICON_BUCKET', 'my-bucket')

        fake_zip = _fake_zip_bytes([
            'Cloud_Run.svg',
            'BigQuery.svg',
            'not-an-icon.txt',
        ])

        mock_blob = MagicMock()

        def fake_download_to_filename(path: str) -> None:
            Path(path).write_bytes(fake_zip)

        mock_blob.download_to_filename.side_effect = fake_download_to_filename
        mock_bucket = MagicMock()
        mock_bucket.blob.return_value = mock_blob
        mock_client = MagicMock()
        mock_client.bucket.return_value = mock_bucket

        fake_storage = MagicMock()
        fake_storage.Client.return_value = mock_client

        fake_google_cloud = MagicMock()
        fake_google_cloud.storage = fake_storage

        with patch.dict(
            sys.modules,
            {
                'google.cloud': fake_google_cloud,
                'google.cloud.storage': fake_storage,
            },
        ):
            result = icd.ensure_icons_available()

        assert result == patched_cache_dir
        svgs = sorted(p.name for p in patched_cache_dir.glob('*.svg'))
        assert svgs == ['BigQuery.svg', 'Cloud_Run.svg']
        # Non-svg file must NOT be extracted
        assert not (patched_cache_dir / 'not-an-icon.txt').exists()

    def test_empty_zip_returns_none(
        self, patched_cache_dir, monkeypatch
    ):
        monkeypatch.setattr(icd, '_ICON_BUCKET', 'my-bucket')
        fake_zip = _fake_zip_bytes([])

        mock_blob = MagicMock()
        mock_blob.download_to_filename.side_effect = (
            lambda p: Path(p).write_bytes(fake_zip)
        )
        mock_bucket = MagicMock()
        mock_bucket.blob.return_value = mock_blob
        mock_client = MagicMock()
        mock_client.bucket.return_value = mock_bucket

        fake_storage = MagicMock()
        fake_storage.Client.return_value = mock_client
        fake_google_cloud = MagicMock()
        fake_google_cloud.storage = fake_storage

        with patch.dict(
            sys.modules,
            {
                'google.cloud': fake_google_cloud,
                'google.cloud.storage': fake_storage,
            },
        ):
            result = icd.ensure_icons_available()

        assert result is None


class TestFailurePaths:
    def test_download_error_returns_none(
        self, patched_cache_dir, monkeypatch
    ):
        monkeypatch.setattr(icd, '_ICON_BUCKET', 'my-bucket')

        mock_blob = MagicMock()
        mock_blob.download_to_filename.side_effect = RuntimeError(
            'network down'
        )
        mock_bucket = MagicMock()
        mock_bucket.blob.return_value = mock_blob
        mock_client = MagicMock()
        mock_client.bucket.return_value = mock_bucket

        fake_storage = MagicMock()
        fake_storage.Client.return_value = mock_client
        fake_google_cloud = MagicMock()
        fake_google_cloud.storage = fake_storage

        with patch.dict(
            sys.modules,
            {
                'google.cloud': fake_google_cloud,
                'google.cloud.storage': fake_storage,
            },
        ):
            result = icd.ensure_icons_available()

        assert result is None
