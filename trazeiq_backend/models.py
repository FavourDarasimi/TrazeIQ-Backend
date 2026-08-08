"""Project-wide abstract models — infrastructure shared by every app."""

import uuid

from django.db import models


class UUIDModel(models.Model):
    """Abstract base for every concrete TrazeIQ model.

    A random 128-bit UUID primary key instead of a sequential auto-increment:
    keys are never guessable or enumerable, so an exposed id leaks nothing
    about volume or ordering, and every FK to a TrazeIQ model automatically
    shares the same (UUID) column type.

    Because the class is abstract it produces no migrations of its own; the
    ``id`` field materializes on each inheriting model's initial migration.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    class Meta:
        abstract = True