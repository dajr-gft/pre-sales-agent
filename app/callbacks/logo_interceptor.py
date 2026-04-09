import logging
from typing import Optional

from google.adk.agents.callback_context import CallbackContext
from google.adk.models import LlmRequest, LlmResponse
from google.genai import types

logger = logging.getLogger(__name__)

_SUPPORTED_IMAGE_TYPES = frozenset(
    {
        'image/png',
        'image/jpeg',
        'image/svg+xml',
        'image/webp',
        'image/gif',
    }
)

_MIME_TO_EXT = {
    'image/png': 'png',
    'image/jpeg': 'jpg',
    'image/svg+xml': 'svg',
    'image/webp': 'webp',
    'image/gif': 'gif',
}


async def intercept_image_uploads(
    callback_context: CallbackContext,
    llm_request: LlmRequest,
) -> Optional[LlmResponse]:
    """Intercept logo uploads when awaiting — pass all other images through.

    This callback runs before every model call. It only intervenes when
    state['awaiting_logo'] is True. In all other cases, images pass through
    to the model normally (they may be contextual screenshots, diagrams, etc.).

    When state['awaiting_logo'] is True and an inline image is detected:
        1. Saves the first image as an artifact via the configured
           ArtifactService (GcsArtifactService in production,
           InMemoryArtifactService in dev). This persists the bytes across
           instances for the rest of the session — critical because Agent
           Engine routes requests across multiple stateless containers.
        2. Stores the artifact filename in state['customer_logo_artifact'].
        3. Stores the MIME type in state['customer_logo_mime_type'].
        4. Sets state['awaiting_logo'] to False.
        5. Replaces the image part with a text confirmation — the model
           receives only text, no image bytes.

    Args:
        callback_context: ADK callback context with state + artifact access.
        llm_request: The mutable LLM request about to be sent to the model.

    Returns:
        None — always proceeds to the model (with modified request).
    """
    if not llm_request.contents:
        return None

    last_message = llm_request.contents[-1]
    if last_message.role != 'user' or not last_message.parts:
        return None

    image_parts = []
    non_image_parts = []
    for part in last_message.parts:
        if _is_image_part(part):
            image_parts.append(part)
        else:
            non_image_parts.append(part)

    if not image_parts:
        return None

    awaiting_logo = callback_context.state.get('awaiting_logo', False)
    if not awaiting_logo:
        return None

    image_part = image_parts[0]

    if not (
        hasattr(image_part, 'inline_data')
        and image_part.inline_data is not None
        and image_part.inline_data.data
    ):
        logger.warning(
            'intercept_image_uploads: logo provided via file_data (URI) '
            'instead of inline bytes — cannot persist as artifact, letting '
            'image pass through to model unchanged.'
        )
        return None

    mime_type = image_part.inline_data.mime_type
    ext = _MIME_TO_EXT.get(mime_type, 'png')
    filename = f'customer_logo.{ext}'

    artifact = types.Part.from_bytes(
        data=image_part.inline_data.data,
        mime_type=mime_type,
    )

    try:
        version = await callback_context.save_artifact(
            filename=filename,
            artifact=artifact,
        )
        logger.info(
            'intercept_image_uploads: logo saved as artifact | filename=%s | version=%s | mime=%s',
            filename,
            version,
            mime_type,
        )
    except Exception as err:
        logger.error(
            'intercept_image_uploads: failed to save logo artifact | error=%s | type=%s',
            str(err),
            type(err).__name__,
        )
        return None

    callback_context.state['customer_logo_artifact'] = filename
    callback_context.state['customer_logo_mime_type'] = mime_type
    callback_context.state['awaiting_logo'] = False

    confirmation_text = (
        '[Logo do cliente recebida e armazenada com sucesso. '
        'Prossiga com a montagem do documento.]'
    )
    non_image_parts.append(types.Part(text=confirmation_text))

    last_message.parts.clear()
    last_message.parts.extend(non_image_parts)

    return None


def _is_image_part(part: types.Part) -> bool:
    """Check if a Part contains image data (either inline or via File API)."""
    if (
        hasattr(part, 'inline_data')
        and part.inline_data is not None
        and hasattr(part.inline_data, 'mime_type')
        and part.inline_data.mime_type in _SUPPORTED_IMAGE_TYPES
    ):
        return True

    if (
        hasattr(part, 'file_data')
        and part.file_data is not None
        and hasattr(part.file_data, 'mime_type')
        and part.file_data.mime_type in _SUPPORTED_IMAGE_TYPES
    ):
        return True

    return False
