"""Add ``User.username`` and backfill existing rows.

The field ends up non-null + unique, but existing deployments already have
users — so this runs in three steps: add it nullable, backfill every row
from the email prefix (sanitized, collision-suffixed), then enforce the
real constraints. New code must always supply a username; the NOT NULL
constraint guarantees it.
"""

import re

from django.db import migrations, models

import apps.accounts.validators


def _candidate_base(email: str) -> str:
    local = (email or "").split("@")[0].lower()
    base = re.sub(r"[^a-z0-9._-]", "", local)[:30]
    if len(base) < 3:
        base = (base + "user")[:30]
    return base or "user"


def backfill_usernames(apps, schema_editor):
    User = apps.get_model("accounts", "User")
    seen = set(
        User.objects.exclude(username__isnull=True)
        .exclude(username="")
        .values_list("username", flat=True)
    )
    rows = (
        User.objects.filter(models.Q(username__isnull=True) | models.Q(username=""))
        .order_by("id")
        .only("id", "email", "username")
    )
    for user in rows:
        base = _candidate_base(user.email)
        candidate, suffix = base, 1
        while candidate in seen:
            suffix += 1
            candidate = f"{base[: 30 - len(str(suffix)) - 1]}-{suffix}"
        user.username = candidate
        user.save(update_fields=["username"])
        seen.add(candidate)


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="username",
            field=models.CharField(max_length=30, null=True),
        ),
        migrations.RunPython(backfill_usernames, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="user",
            name="username",
            field=models.CharField(
                max_length=30,
                unique=True,
                validators=[apps.accounts.validators.validate_username],
                help_text="Public handle: 3–30 lowercase chars (a-z, 0-9, dot, underscore, hyphen).",
            ),
        ),
    ]
