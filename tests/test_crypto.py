"""M4 state encryption: Fernet round trip, fresh nonce, wrong key fails loudly (plan M4)."""

import pytest
from cryptography.fernet import Fernet

from housing_watch.crypto import CryptoError, decrypt, encrypt

KEY = Fernet.generate_key().decode()


def test_round_trip():
    assert decrypt(encrypt(b"sqlite bytes", KEY), KEY) == b"sqlite bytes"


def test_fresh_nonce_every_time():
    # Identical pushes every run must not reveal whether state changed (plan M4).
    assert encrypt(b"same", KEY) != encrypt(b"same", KEY)


def test_wrong_key_raises():
    with pytest.raises(CryptoError):
        decrypt(encrypt(b"x", KEY), Fernet.generate_key().decode())


def test_tampered_raises():
    blob = bytearray(encrypt(b"x", KEY))
    blob[-5] ^= 1
    with pytest.raises(CryptoError):
        decrypt(bytes(blob), KEY)


@pytest.mark.parametrize("bad", ["", "not-a-key", None])
def test_bad_key_raises(bad):
    with pytest.raises(CryptoError):
        encrypt(b"x", bad)


def test_length_is_padded_to_buckets():
    from housing_watch.crypto import BUCKET

    assert len(encrypt(b"", KEY)) == len(encrypt(b"x" * (BUCKET - 100), KEY))
    assert len(encrypt(b"x" * (BUCKET + 1), KEY)) > len(encrypt(b"", KEY))
    assert decrypt(encrypt(b"x" * (BUCKET + 1), KEY), KEY) == b"x" * (BUCKET + 1)
