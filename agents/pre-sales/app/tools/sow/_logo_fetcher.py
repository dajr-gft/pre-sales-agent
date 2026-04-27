"""Fetch customer logos from logo.dev API for embedding in SOW documents.

Caller should treat None as "no logo available" and render a placeholder.
"""

from urllib.parse import quote

import requests
import structlog

from ...config import config

logger = structlog.get_logger()

_LOGO_DEV_TOKEN = config.LOGO_DEV_PUBLISHABLE_KEY
_LOGO_DEV_BASE_URL = 'https://img.logo.dev'
_REQUEST_TIMEOUT_SECONDS = 5
_MIN_IMAGE_BYTES = 1024


def fetch_customer_logo(
    customer_name: str,
    inferred_domain: str | None = None,
) -> bytes | None:
    """Fetch PNG bytes for a customer logo, or None if not found.

    Tries logo.dev in a cascade:
      1. Domain lookup with inferred_domain (provided by the model)
      2. Name lookup with customer_name (logo.dev resolves brand → domain)
      3. None (caller renders a placeholder)

    Domain lookup is preferred because it's deterministic and faster.
    Name lookup is the fallback for cases where the model could not
    infer a domain (uncommon brands, ambiguous names, etc).

    Returns None on any failure (missing token, network error, 404,
    invalid content-type, suspiciously small payload).
    """
    if not _LOGO_DEV_TOKEN:
        logger.warning(
            'LOGO_DEV_PUBLISHABLE_KEY env var not set — cannot fetch logos'
        )
        return None

    if inferred_domain:
        sanitized = _sanitize_domain(inferred_domain)
        if sanitized:
            logo_bytes = _try_fetch_by_domain(sanitized)
            if logo_bytes is not None:
                logger.info(
                    f'Fetched logo for {customer_name!r} via domain '
                    f'{sanitized!r}'
                )
                return logo_bytes

    if customer_name:
        logo_bytes = _try_fetch_by_name(customer_name)
        if logo_bytes is not None:
            logger.info(
                f'Fetched logo for {customer_name!r} via name lookup'
            )
            return logo_bytes

    logger.warning(f'No logo found for {customer_name!r}')
    return None


def _try_fetch_by_domain(domain: str) -> bytes | None:
    """Domain lookup: img.logo.dev/{domain}.

    Assumes the caller has already sanitized the domain.
    """
    url = f'{_LOGO_DEV_BASE_URL}/{quote(domain)}'
    return _execute_request(url, identifier=domain, lookup_kind='domain')


def _try_fetch_by_name(customer_name: str) -> bytes | None:
    """Name lookup: img.logo.dev/name/{customer_name}.

    URL-encodes the name so spaces and special characters are handled
    safely (logo.dev requires URL-encoded names).
    """
    encoded_name = quote(customer_name)
    url = f'{_LOGO_DEV_BASE_URL}/name/{encoded_name}'
    return _execute_request(
        url, identifier=customer_name, lookup_kind='name'
    )


def _execute_request(
    url: str,
    identifier: str,
    lookup_kind: str,
) -> bytes | None:
    """Execute a logo.dev request and return image bytes on success.

    The two lookup endpoints (domain and name) share identical request
    semantics — only the URL path differs. ``identifier`` and
    ``lookup_kind`` are used purely for log context.
    """
    params = {
        'token': _LOGO_DEV_TOKEN,
        'format': 'png',
        'retina': 'true',
        'size': '256',
        'fallback': '404',
    }

    try:
        response = requests.get(
            url, params=params, timeout=_REQUEST_TIMEOUT_SECONDS
        )
    except requests.RequestException as e:
        logger.warning(
            f'logo.dev {lookup_kind} request for {identifier!r} failed: '
            f'{type(e).__name__}: {e}'
        )
        return None

    if response.status_code == 404:
        return None

    if response.status_code != 200:
        logger.warning(
            f'logo.dev {lookup_kind} returned status '
            f'{response.status_code} for {identifier!r}'
        )
        return None

    content_type = response.headers.get('content-type', '')
    if not content_type.startswith('image/'):
        logger.warning(
            f'logo.dev {lookup_kind} returned non-image content-type '
            f'{content_type!r} for {identifier!r}'
        )
        return None

    if len(response.content) < _MIN_IMAGE_BYTES:
        logger.warning(
            f'logo.dev {lookup_kind} returned suspiciously small payload '
            f'({len(response.content)} bytes) for {identifier!r}'
        )
        return None

    return response.content


def _sanitize_domain(raw: str) -> str | None:
    """Strip protocol, www, paths, query strings, and whitespace from a domain.

    The model sometimes returns 'https://www.itau.com.br/' when the API expects
    just 'itau.com.br'. This normalizes any reasonable input to the bare domain.

    Returns None if the input doesn't look like a valid domain.
    """
    if not raw:
        return None

    cleaned = raw.strip().lower()

    for prefix in ('https://', 'http://'):
        if cleaned.startswith(prefix):
            cleaned = cleaned[len(prefix):]
            break

    if cleaned.startswith('www.'):
        cleaned = cleaned[4:]

    for separator in ('/', '?', '#'):
        if separator in cleaned:
            cleaned = cleaned.split(separator, 1)[0]

    cleaned = cleaned.strip()

    if '.' not in cleaned or ' ' in cleaned:
        logger.warning(f'Domain {raw!r} did not look valid after sanitization')
        return None

    return cleaned
