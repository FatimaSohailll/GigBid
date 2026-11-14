"""Password hashing, session tokens and the encrypted OAuth token store."""

import hashlib
import hmac
import json
import secrets
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from apps.api.db import Repository

_SCRYPT = {"n": 2**14, "r": 8, "p": 1}


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, **_SCRYPT)
    return f"{salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    salt, _, expected = stored.partition("$")
    digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), **_SCRYPT)
    return hmac.compare_digest(digest.hex(), expected)


def new_session_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    """Sessions are stored hashed, so a database leak does not expose live sessions."""
    return hashlib.sha256(token.encode()).hexdigest()


class TokenVault:
    """OAuth tokens encrypted per user. Plaintext never reaches the database or a log."""

    def __init__(self, key: str, repository: Repository) -> None:
        self._fernet = Fernet(key)
        self._repository = repository

    async def store(self, user_id: str, provider: str, tokens: dict[str, Any]) -> None:
        ciphertext = self._fernet.encrypt(json.dumps(tokens).encode())
        await self._repository.store_oauth(user_id, provider, ciphertext)

    async def load(self, user_id: str, provider: str) -> dict[str, Any] | None:
        """None when nothing is stored, or when it was stored under another key (the user then
        connects again; a changed key must not crash every request)."""
        ciphertext = await self._repository.oauth_ciphertext(user_id, provider)
        if ciphertext is None:
            return None
        try:
            value: dict[str, Any] = json.loads(self._fernet.decrypt(ciphertext))
        except InvalidToken:
            return None
        return value
