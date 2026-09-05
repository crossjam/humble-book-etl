from hashlib import sha256

from api.security import hash_password, is_sha256_hash, verify_password


def test_is_sha256_hash():
    digest = sha256(b"example").hexdigest()
    assert is_sha256_hash(digest)
    assert not is_sha256_hash("not-a-hash")


def test_hash_password_accepts_sha256_input():
    digest = sha256(b"shared-secret").hexdigest()
    hashed = hash_password(digest)
    assert verify_password(digest, hashed)


def test_hash_password_accepts_plain_input():
    plain = "PlainPassword123"
    hashed = hash_password(plain)
    assert verify_password(plain, hashed)

