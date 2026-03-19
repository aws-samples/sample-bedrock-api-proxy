"""
Application-layer encryption for Provider API keys using Fernet.
"""
import base64
import hashlib
import logging

from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger(__name__)


class KeyEncryption:
    """Fernet (AES-128-CBC + HMAC-SHA256) encryption for provider API keys."""

    def __init__(self, secret: str):
        key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest())
        self._fernet = Fernet(key)

    def encrypt(self, plaintext: str) -> str:
        return self._fernet.encrypt(plaintext.encode()).decode()

    def decrypt(self, ciphertext: str) -> str:
        return self._fernet.decrypt(ciphertext.encode()).decode()

    @staticmethod
    def mask(key: str) -> str:
        """Redact key for logging: first4****last4, or **** if short."""
        if len(key) <= 8:
            return "****"
        return f"{key[:4]}****{key[-4:]}"
