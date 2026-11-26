"""state.db encryption (M4): Fernet (AES-128-CBC + HMAC), a fresh nonce every call.

Re-encrypting every run makes every push to `data` look alike, so push timing reveals
nothing about alerts (Simon, 2026-11-26). A wrong key or a tampered file raises.
"""

from cryptography.fernet import Fernet, InvalidToken


class CryptoError(Exception):
    """Bad key, or the file isn't ours (wrong key or tampered)."""


def _fernet(key: str | bytes | None) -> Fernet:
    try:
        return Fernet(key.strip() if isinstance(key, (str, bytes)) else key)
    except ValueError, TypeError:
        raise CryptoError("STATE_KEY is not a valid Fernet key") from None


def encrypt(data: bytes, key: str | bytes | None) -> bytes:
    return _fernet(key).encrypt(data)


def decrypt(blob: bytes, key: str | bytes | None) -> bytes:
    f = _fernet(key)
    try:
        return f.decrypt(blob)
    except InvalidToken:
        raise CryptoError("state.db.enc: wrong STATE_KEY or tampered file") from None
