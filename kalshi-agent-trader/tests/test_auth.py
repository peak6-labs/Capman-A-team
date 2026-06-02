"""Tests for RSA-PSS request signing.

We generate a throwaway RSA key, sign a request, and verify the signature with the
matching public key using the exact PSS parameters Kalshi specifies. This locks the
message format (ts+METHOD+path) and header names.
"""

import base64

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from kalshi_agent_trader.auth import KalshiSigner


def _key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def test_headers_present_and_message_format():
    signer = KalshiSigner("key-123", _key())
    headers = signer.headers("GET", "/trade-api/v2/portfolio/balance", timestamp_ms=1700000000000)

    assert headers["KALSHI-ACCESS-KEY"] == "key-123"
    assert headers["KALSHI-ACCESS-TIMESTAMP"] == "1700000000000"
    assert headers["KALSHI-ACCESS-SIGNATURE"]

    # Reconstruct the exact signed message and verify with the public key.
    message = b"1700000000000GET/trade-api/v2/portfolio/balance"
    signature = base64.b64decode(headers["KALSHI-ACCESS-SIGNATURE"])
    signer._private_key.public_key().verify(
        signature,
        message,
        padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=hashes.SHA256().digest_size),
        hashes.SHA256(),
    )  # raises InvalidSignature on mismatch


def test_method_is_uppercased():
    signer = KalshiSigner("k", _key())
    h = signer.headers("post", "/trade-api/v2/portfolio/orders", timestamp_ms=1)
    sig = base64.b64decode(h["KALSHI-ACCESS-SIGNATURE"])
    signer._private_key.public_key().verify(
        sig,
        b"1POST/trade-api/v2/portfolio/orders",
        padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=hashes.SHA256().digest_size),
        hashes.SHA256(),
    )
