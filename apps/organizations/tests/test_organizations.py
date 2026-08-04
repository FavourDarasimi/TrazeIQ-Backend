"""Phase 1C: organization creation, ownership and membership scoping."""

from rest_framework.test import APIClient

from django.contrib.auth import get_user_model
from django.test import TestCase

from ..models import Membership, MembershipRole, Organization

User = get_user_model()


def register_and_login(client: APIClient, email: str) -> None:
    """Full auth flow — the same cookie-based session the frontend uses."""
    client.post(
        "/api/v1/auth/register/",
        {"email": email, "password": "fdsK9Qop21z!"},
        format="json",
    )
    client.post(
        "/api/v1/auth/verify/",
        {"email": email, "otp": "000000"},
        format="json",
    )
    client.post(
        "/api/v1/auth/login/",
        {"email": email, "password": "fdsK9Qop21z!"},
        format="json",
    )


class OrganizationCreateTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        register_and_login(self.client, "owner@trazeiq.io")

    def test_create_makes_creator_owner(self):
        response = self.client.post(
            "/api/v1/organizations/", {"name": "Acme"}, format="json"
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["success"], True)

        organization = Organization.objects.get()
        self.assertEqual(organization.name, "Acme")
        self.assertEqual(organization.owner.email, "owner@trazeiq.io")

        membership = Membership.objects.get()
        self.assertEqual(membership.organization, organization)
        self.assertEqual(membership.user, organization.owner)
        self.assertEqual(membership.role, MembershipRole.OWNER)

        # The response exposes the org, not the membership rows.
        data = response.data["data"]["organization"]
        self.assertEqual(data["id"], organization.id)
        self.assertEqual(data["name"], "Acme")
        self.assertEqual(data["owner"], organization.owner_id)

    def test_create_requires_name(self):
        response = self.client.post(
            "/api/v1/organizations/", {}, format="json"
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["success"], False)
        self.assertEqual(response.data["error"]["code"], "VALIDATION_FAILED")
        self.assertIn("name", response.data["error"]["fields"])

    def test_create_requires_auth(self):
        anonymous = APIClient()
        response = anonymous.post(
            "/api/v1/organizations/", {"name": "Acme"}, format="json"
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.data["error"]["code"], "NOT_AUTHENTICATED")


class OrganizationListTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        register_and_login(self.client, "member@trazeiq.io")

    def test_list_only_returns_member_organizations(self):
        mine = self.client.post(
            "/api/v1/organizations/", {"name": "Mine"}, format="json"
        )
        organization_id = mine.data["data"]["organization"]["id"]

        # Another user creates an org we don't belong to.
        other = APIClient()
        register_and_login(other, "other@trazeiq.io")
        other.post("/api/v1/organizations/", {"name": "Theirs"}, format="json")

        response = self.client.get("/api/v1/organizations/")
        self.assertEqual(response.status_code, 200)
        organizations = response.data["data"]["organizations"]
        self.assertEqual(len(organizations), 1)
        self.assertEqual(organizations[0]["id"], organization_id)
        self.assertEqual(organizations[0]["name"], "Mine")

    def test_detail_is_scoped_to_membership(self):
        response = self.client.post(
            "/api/v1/organizations/", {"name": "Mine"}, format="json"
        )
        organization_id = response.data["data"]["organization"]["id"]

        other = APIClient()
        register_and_login(other, "other@trazeiq.io")
        other.post("/api/v1/organizations/", {"name": "Theirs"}, format="json")
        their_org = Organization.objects.exclude(id=organization_id).get()

        # Mine is visible to me.
        ok = self.client.get(f"/api/v1/organizations/{organization_id}/")
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(ok.data["data"]["organization"]["id"], organization_id)

        # Theirs is not — 404, never a 403 that leaks existence.
        denied = self.client.get(f"/api/v1/organizations/{their_org.id}/")
        self.assertEqual(denied.status_code, 404)
        self.assertEqual(denied.data["error"]["code"], "NOT_FOUND")
