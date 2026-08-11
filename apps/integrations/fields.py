"""Encryption at rest for sensitive fields (Phase 4D).

django-cryptography 1.1 (the library the spec names) is unmaintained and
incompatible with Django 6.0 (it imports the removed ``django.utils.baseconv``),
so this module reimplements exactly what it wraps: Fernet encryption from the
``cryptography`` package, with the key derived from ``SECRET_KEY``.

Stored values carry a ``trazeiq-enc:`` prefix so ciphertext is visibly not
plaintext (and is never re-encrypted on update), and decrypt transparently
on read. A leaked database dump yields an unusable token.
"""

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.db import models

_PREFIX = "trazeiq-enc:"


def _fernet() -> Fernet:
    key = hashlib.sha256(settings.SECRET_KEY.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(key))


class EncryptedCharField(models.CharField):
    """A CharField whose stored value is Fernet ciphertext."""

    def get_prep_value(self, value):
        value = super().get_prep_value(value)
        if value is None or value.startswith(_PREFIX):
            return value
        return _PREFIX + _fernet().encrypt(value.encode("utf-8")).decode("ascii")

    def from_db_value(self, value, expression, connection):
        if value is None or not value.startswith(_PREFIX):
            return value
        try:
            return _fernet().decrypt(value[len(_PREFIX):].encode("ascii")).decode("utf-8")
        except (InvalidToken, ValueError):
            return value