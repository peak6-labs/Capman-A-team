"""Kalshi API-key authentication: RSA-PSS request signing.

Per Kalshi's scheme, every authenticated request is signed:
    message   = f"{timestamp_ms}{METHOD}{path}"
    signature = base64( RSA-PSS-sign(message, SHA-256, MGF1-SHA256, salt=32) )

Headers sent:
    KALSHI-ACCESS-KEY        -> the API key id
    KALSHI-ACCESS-TIMESTAMP  -> the same Unix milliseconds used in the message
    KALSHI-ACCESS-SIGNATURE  -> base64 signature

`path` includes the `/trade-api/v2/...` prefix but EXCLUDES the query string.
"""

from __future__ import annotations

import base64
import time
from pathlib import Path
from typing import Dict

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa


class KalshiSigner:
    """Loads an RSA private key and produces signed auth headers."""

    def __init__(self, api_key_id: str, private_key: rsa.RSAPrivateKey) -> None:
        self.api_key_id = api_key_id
        self._private_key = private_key

    @classmethod
    def from_file(cls, api_key_id: str, private_key_path: str) -> "KalshiSigner":
        key_bytes = Path(private_key_path).expanduser().read_bytes()
        private_key = serialization.load_pem_private_key(key_bytes, password=None)
        if not isinstance(private_key, rsa.RSAPrivateKey):
            raise TypeError("Kalshi private key must be an RSA key in PEM format.")
        return cls(api_key_id, private_key)

    def _sign(self, message: str) -> str:
        signature = self._private_key.sign(
            message.encode("utf-8"),
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=hashes.SHA256().digest_size,  # 32
            ),
            hashes.SHA256(),
        )
        return base64.b64encode(signature).decode("ascii")

    def headers(self, method: str, path: str, timestamp_ms: int | None = None) -> Dict[str, str]:
        """Build signed auth headers for `method` + `path`.

        `path` MUST start with /trade-api/v2 and exclude the query string.
        """
        ts = str(timestamp_ms if timestamp_ms is not None else int(time.time() * 1000))
        message = f"{ts}{method.upper()}{path}"
        return {
            "KALSHI-ACCESS-KEY": self.api_key_id,
            "KALSHI-ACCESS-TIMESTAMP": ts,
            "KALSHI-ACCESS-SIGNATURE": self._sign(message),
        }
