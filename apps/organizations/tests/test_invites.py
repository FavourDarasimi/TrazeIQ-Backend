"""Phase 4A: team invites — invite/accept flow, token handling, RBAC on invite."""

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts.utils import hash_code
from apps.organizations.models import Invite, Membership, MembershipRole

from apps.events.tests.test_events import create_org, register_and_login

User = get_user_model()

PASSWORD = "fdsK9Qop21z!"


def invite(client: APIClient, org_id: str, email: str, role="viewer") -> dict:
    """Invite via the API; returns the parsed response data."""
    response = client.post(
        f"/api/v1/organizations/{org_id}/invite/",
        {"email": email, "role": role},
        format="json",
    )
    assert response.status_code == 201, response.data
    return response.data["data"]


def accept(client: APIClient, token: str):
    return client.post(f"/api/v1/invites/{token}/accept/", format="json")


class InviteFlowTests(TestCase):
    def setUp(self):
        cache.clear()
        self.owner = APIClient()
        register_and_login(self.owner, "owner@trazeiq.io")
        self.org_id = create_org(self.owner, "Acme")

    def test_invite_accept_produces_membership_with_correct_role(self):
        data = invite(self.owner, self.org_id, "teammate@trazeiq.io", role="developer")
        raw_token = data["invite_token"]
        self.assertEqual(data["invite"]["email"], "teammate@trazeiq.io")
        self.assertEqual(data["invite"]["role"], "developer")

        # Only the hash is stored — the raw token is never persisted.
        stored = Invite.objects.get()
        self.assertEqual(stored.token_hash, hash_code(raw_token))
        self.assertNotEqual(stored.token_hash, raw_token)

        # The invitee registers and claims the invite.
        teammate = APIClient()
        register_and_login(teammate, "teammate@trazeiq.io")
        response = accept(teammate, raw_token)
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["success"], True)
        membership_data = response.data["data"]["membership"]
        self.assertEqual(membership_data["role"], "developer")
        self.assertEqual(membership_data["organization"]["id"], self.org_id)

        membership = Membership.objects.get(user__email="teammate@trazeiq.io")
        self.assertEqual(str(membership.organization_id), self.org_id)
        self.assertEqual(membership.role, MembershipRole.DEVELOPER)

        # Token is single-use — a replay is rejected.
        replayed = accept(teammate, raw_token)
        self.assertEqual(replayed.status_code, 400)
        self.assertEqual(replayed.data["error"]["code"], "INVITE_USED")

    def test_invite_to_existing_member_is_409(self):
        response = self.owner.post(
            f"/api/v1/organizations/{self.org_id}/invite/",
            {"email": "owner@trazeiq.io"},
            format="json",
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["error"]["code"], "ALREADY_MEMBER")

    def test_reinvite_rotates_the_token(self):
        first = invite(self.owner, self.org_id, "rotate@trazeiq.io", role="viewer")
        second = invite(self.owner, self.org_id, "rotate@trazeiq.io", role="admin")

        # Two rows exist: the first invite was voided, the second is live.
        invites = Invite.objects.filter(
            email="rotate@trazeiq.io", organization_id=self.org_id
        ).order_by("created_at")
        self.assertEqual(invites.count(), 2)
        self.assertIsNotNone(invites[0].used_at)  # voided
        self.assertIsNone(invites[1].used_at)

        teammate = APIClient()
        register_and_login(teammate, "rotate@trazeiq.io")

        stale = accept(teammate, first["invite_token"])
        self.assertEqual(stale.status_code, 400)
        self.assertEqual(stale.data["error"]["code"], "INVITE_USED")

        current = accept(teammate, second["invite_token"])
        self.assertEqual(current.status_code, 201)
        self.assertEqual(
            current.data["data"]["membership"]["role"], "admin"
        )

    def test_invite_rejects_owner_role(self):
        response = self.owner.post(
            f"/api/v1/organizations/{self.org_id}/invite/",
            {"email": "someone@trazeiq.io", "role": "owner"},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["error"]["code"], "VALIDATION_FAILED")
        self.assertIn("role", response.data["error"]["fields"])

    def test_invite_from_non_member_is_404(self):
        outsider = APIClient()
        register_and_login(outsider, "outsider@trazeiq.io")
        response = outsider.post(
            f"/api/v1/organizations/{self.org_id}/invite/",
            {"email": "anyone@trazeiq.io"},
            format="json",
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.data["error"]["code"], "NOT_FOUND")

    def test_invite_requires_auth(self):
        anonymous = APIClient()
        response = anonymous.post(
            f"/api/v1/organizations/{self.org_id}/invite/",
            {"email": "anyone@trazeiq.io"},
            format="json",
        )
        self.assertEqual(response.status_code, 401)


class InviteAcceptEdgeTests(TestCase):
    def setUp(self):
        cache.clear()
        self.owner = APIClient()
        register_and_login(self.owner, "owner@trazeiq.io")
        self.org_id = create_org(self.owner, "Acme")

    def _make_invite(self, email: str) -> str:
        return invite(self.owner, self.org_id, email, role="viewer")["invite_token"]

    def test_accept_with_different_email_is_403(self):
        token = self._make_invite("alice@trazeiq.io")
        bob = APIClient()
        register_and_login(bob, "bob@trazeiq.io")
        response = accept(bob, token)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data["error"]["code"], "INVITE_EMAIL_MISMATCH")
        self.assertIsNone(Invite.objects.get().used_at)  # not consumed

    def test_accept_invalid_token_is_400(self):
        alice = APIClient()
        register_and_login(alice, "alice@trazeiq.io")
        response = accept(alice, "not-a-real-token")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["error"]["code"], "INVITE_INVALID")

    def test_accept_expired_invite_is_400(self):
        token = self._make_invite("alice@trazeiq.io")
        Invite.objects.update(expires_at="2020-01-01T00:00:00Z")

        alice = APIClient()
        register_and_login(alice, "alice@trazeiq.io")
        response = accept(alice, token)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["error"]["code"], "INVITE_EXPIRED")
        self.assertEqual(Membership.objects.count(), 1)  # owner only

    def test_accept_when_already_member_is_409(self):
        token = self._make_invite("alice@trazeiq.io")
        alice = APIClient()
        register_and_login(alice, "alice@trazeiq.io")
        Membership.objects.create(
            user=User.objects.get(email="alice@trazeiq.io"),
            organization_id=self.org_id,
            role=MembershipRole.VIEWER,
        )
        response = accept(alice, token)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["error"]["code"], "ALREADY_MEMBER")

    def test_accept_requires_auth(self):
        token = self._make_invite("alice@trazeiq.io")
        response = APIClient().post(f"/api/v1/invites/{token}/accept/", format="json")
        self.assertEqual(response.status_code, 401)


class OrganizationMembersTests(TestCase):
    def setUp(self):
        cache.clear()
        self.owner = APIClient()
        register_and_login(self.owner, "owner@trazeiq.io")
        self.org_id = create_org(self.owner, "Acme")

        self.alice = APIClient()
        register_and_login(self.alice, "alice@trazeiq.io")
        token = invite(self.owner, self.org_id, "alice@trazeiq.io", role="viewer")["invite_token"]
        accept(self.alice, token)

    def test_members_list_includes_invited_member(self):
        response = self.owner.get(f"/api/v1/organizations/{self.org_id}/members/")
        self.assertEqual(response.status_code, 200)
        members = response.data["data"]["members"]
        self.assertEqual(len(members), 2)
        roles = {m["user"]: m["role"] for m in members}
        self.assertEqual(roles["owner@trazeiq.io"], "owner")
        self.assertEqual(roles["alice@trazeiq.io"], "viewer")
        self.assertIn("user_id", members[0])

    def test_members_list_is_visible_to_any_member(self):
        response = self.alice.get(f"/api/v1/organizations/{self.org_id}/members/")
        self.assertEqual(response.status_code, 200)

    def test_members_list_non_member_is_404(self):
        outsider = APIClient()
        register_and_login(outsider, "outsider@trazeiq.io")
        response = outsider.get(f"/api/v1/organizations/{self.org_id}/members/")
        self.assertEqual(response.status_code, 404)
