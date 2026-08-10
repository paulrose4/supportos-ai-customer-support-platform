from base64 import urlsafe_b64decode, urlsafe_b64encode
from hashlib import scrypt
from hmac import compare_digest
from secrets import token_bytes


class ScryptPasswordHasher:
    _n = 2**14
    _r = 8
    _p = 1
    _key_length = 32

    def hash_password(self, password: str) -> str:
        salt = token_bytes(16)
        digest = scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=self._n,
            r=self._r,
            p=self._p,
            dklen=self._key_length,
        )
        return "$".join(
            (
                "scrypt",
                str(self._n),
                str(self._r),
                str(self._p),
                _encode(salt),
                _encode(digest),
            )
        )

    def verify_password(self, password: str, encoded_hash: str) -> bool:
        try:
            algorithm, n, r, p, salt, expected = encoded_hash.split("$", 5)
            if algorithm != "scrypt":
                return False
            actual = scrypt(
                password.encode("utf-8"),
                salt=_decode(salt),
                n=int(n),
                r=int(r),
                p=int(p),
                dklen=len(_decode(expected)),
            )
            return compare_digest(actual, _decode(expected))
        except (ValueError, TypeError):
            return False


def _encode(value: bytes) -> str:
    return urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _decode(value: str) -> bytes:
    return urlsafe_b64decode(value + "=" * (-len(value) % 4))
