import hashlib
import hmac
import secrets


def create_api_key():
    return secrets.token_urlsafe(32)


def hash_api_key(api_key):
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()


def matches_api_key(api_key, expected_hash):
    return hmac.compare_digest(hash_api_key(api_key), expected_hash)
