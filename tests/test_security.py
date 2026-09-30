from minioj.security import create_api_token, hash_password, hash_token, verify_password


def test_password_hash_is_not_plaintext():
    digest = hash_password("a-strong-password")
    assert digest != "a-strong-password"
    assert verify_password("a-strong-password", digest)
    assert not verify_password("wrong-password", digest)


def test_api_token_has_prefix_and_only_digest_is_stable():
    token_id, raw, digest, expires = create_api_token()
    assert token_id.startswith("token_")
    assert raw.startswith("oj_")
    assert digest == hash_token(raw)
    assert raw not in digest
    assert expires is not None
