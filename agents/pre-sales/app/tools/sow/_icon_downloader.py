import logging
import os
import tempfile
import zipfile
from pathlib import Path

logger = logging.getLogger(__name__)

_ICON_BUCKET = os.environ.get('LOGS_BUCKET_NAME')
_ICON_ZIP_OBJECT = 'gcp-icons-flat.zip'
_ICON_CACHE_DIR = Path('/tmp/gcp-icons')


def ensure_icons_available() -> Path | None:
    """Ensure GCP icons are cached locally. Downloads from GCS on first call.

    Returns the path to the icon directory, or None if download failed
    or the GCP_ICONS_BUCKET env var is not set.
    Idempotent: subsequent calls reuse the cached directory.
    """
    if _ICON_CACHE_DIR.exists() and any(_ICON_CACHE_DIR.glob('*.svg')):
        return _ICON_CACHE_DIR

    if not _ICON_BUCKET:
        logger.warning(
            'GCP_ICONS_BUCKET env var not set — cannot download icons from GCS'
        )
        return None

    try:
        from google.cloud import storage
    except ImportError:
        logger.warning(
            'google-cloud-storage not available — cannot download icons from GCS'
        )
        return None

    try:
        _ICON_CACHE_DIR.mkdir(parents=True, exist_ok=True)

        logger.info(
            f'Downloading GCP icons from gs://{_ICON_BUCKET}/{_ICON_ZIP_OBJECT}...'
        )
        client = storage.Client()
        bucket = client.bucket(_ICON_BUCKET)
        blob = bucket.blob(_ICON_ZIP_OBJECT)

        with tempfile.NamedTemporaryFile(suffix='.zip', delete=False) as tmp:
            tmp_path = Path(tmp.name)

        try:
            blob.download_to_filename(str(tmp_path))

            with zipfile.ZipFile(tmp_path) as zf:
                svg_members = [m for m in zf.namelist() if m.endswith('.svg')]
                for member in svg_members:
                    target = _ICON_CACHE_DIR / Path(member).name
                    with zf.open(member) as source, open(target, 'wb') as dest:
                        dest.write(source.read())
        finally:
            tmp_path.unlink(missing_ok=True)

        count = len(list(_ICON_CACHE_DIR.glob('*.svg')))
        if count == 0:
            logger.warning(
                f'Downloaded zip from GCS but no SVG files were found inside'
            )
            return None

        logger.info(f'Cached {count} GCP icons at {_ICON_CACHE_DIR}')
        return _ICON_CACHE_DIR

    except Exception as e:
        logger.warning(
            f'Failed to download GCP icons from GCS: {type(e).__name__}: {e}'
        )
        return None
