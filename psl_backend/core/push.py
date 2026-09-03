"""Firebase Cloud Messaging delivery.

Initialisation is lazy and cached: firebase_admin raises if the same app is
initialised twice, and importing this module must not require credentials to
exist (tests, migrations, and the web process all import it).

When no credentials are configured, sends are skipped and reported rather than
raised. A missing Firebase key should not take down shift publishing.
"""

import logging
import threading

from django.conf import settings

logger = logging.getLogger(__name__)

_app = None
_app_lock = threading.Lock()
_init_failed = False


def is_configured():
    return bool(settings.FIREBASE_CREDENTIALS_FILE)


def get_app():
    """Return the initialised firebase app, or None if unavailable."""
    global _app, _init_failed
    if _app is not None or _init_failed:
        return _app
    if not is_configured():
        return None

    with _app_lock:
        if _app is not None or _init_failed:
            return _app
        try:
            import firebase_admin
            from firebase_admin import credentials

            cred = credentials.Certificate(settings.FIREBASE_CREDENTIALS_FILE)
            try:
                _app = firebase_admin.get_app()
            except ValueError:
                _app = firebase_admin.initialize_app(cred)
        except Exception:
            # Cached so a broken config does not retry on every single send.
            _init_failed = True
            logger.exception("Firebase initialisation failed; push disabled.")
    return _app


def send_to_tokens(tokens, title, body, data=None):
    """Send one notification to many device tokens.

    Returns (sent, failed, invalid_tokens). `invalid_tokens` are tokens FCM
    rejected as unregistered — the caller should delete them, because a stale
    token never becomes valid again and would otherwise be retried forever.
    """
    tokens = [t for t in tokens if t]
    if not tokens:
        return 0, 0, []

    app = get_app()
    if app is None:
        logger.warning(
            "Push skipped for %s device(s): Firebase is not configured.", len(tokens)
        )
        return 0, len(tokens), []

    from firebase_admin import messaging

    sent = 0
    failed = 0
    invalid = []
    for token in tokens:
        message = messaging.Message(
            notification=messaging.Notification(title=title, body=body),
            data={k: str(v) for k, v in (data or {}).items()},
            token=token,
        )
        try:
            messaging.send(message, app=app)
            sent += 1
        except Exception as exc:
            failed += 1
            if _is_dead_token(exc):
                invalid.append(token)
            else:
                logger.warning("Push to a device failed: %s", exc)
    return sent, failed, invalid


def _is_dead_token(exc):
    """Whether this token should be deleted rather than retried forever.

    UnregisteredError and SenderIdMismatchError always mean the token itself
    is finished. InvalidArgumentError is broader — FCM also raises it for a
    malformed payload — so it only counts when the message names the
    registration token. Treating every InvalidArgumentError as a dead token
    would let one payload bug delete every device in the database.
    """
    name = type(exc).__name__
    if name in ("UnregisteredError", "SenderIdMismatchError"):
        return True

    text = str(exc).lower()
    if name == "InvalidArgumentError":
        return "registration token" in text
    return "unregistered" in text or "not found" in text
