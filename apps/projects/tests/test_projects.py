"""Phase 1C: project creation, one-time API key, tenant isolation."""

from uuid import uuid4

from rest_framework.test import APIClient

from django.core.cache import cache
from django.test import TestCase

from ..models import Project
from ..utils import hash_api_key

PASSWORD = "fdsK9Qop21z!"


def register_and_login(client: APIClient, email: str) -> None:
    client.post(
        "/api/v1/auth/register/request-otp/", {"email": email}, format="json"
    )
    verified = client.post(
        "/api/v1/auth/register/verify-otp/",
        {"email": email, "otp": "000000"},
        format="json",
    )
    # complete signs the account in directly (cookies) — no separate login call.
    client.post(
        "/api/v1/auth/register/complete/",
        {
            "registration_token": verified.data["data"]["registration_token"],
            "password": PASSWORD,
            "confirm_password": PASSWORD,
        },
        format="json",
    )


def create_org(client: APIClient, name: str) -> str:
    response = client.post(
        "/api/v1/organizations/", {"name": name}, format="json"
    )
    return response.data["data"]["organization"]["id"]


class ProjectCreateTests(TestCase):
    def setUp(self):
        cache.clear()  # reset the per-email signup cap between test cases
        self.client = APIClient()
        register_and_login(self.client, "dev@trazeiq.io")
        self.organization_id = create_org(self.client, "Acme")

    def test_create_returns_raw_key_exactly_once(self):
        response = self.client.post(
            "/api/v1/projects/",
            {"name": "Web", "organization": self.organization_id},
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["success"], True)
        data = response.data["data"]

        project = Project.objects.get()
        raw_key = data["api_key"]
        self.assertEqual(data["project"]["id"], str(project.id))
        self.assertEqual(data["project"]["name"], "Web")
        self.assertEqual(data["project"]["environment"], "production")
        self.assertEqual(data["project"]["api_key_prefix"], raw_key[:8])
        self.assertEqual(len(raw_key), 64)
        self.assertIn(f"X-API-Key: {raw_key}", data["integration_snippet"])

        # The raw key appears nowhere else later — only the prefix does.
        detail = self.client.get(f"/api/v1/projects/{project.id}/")
        self.assertNotIn("api_key", detail.data["data"]["project"])
        self.assertNotIn("api_key_hash", detail.data["data"]["project"])
        self.assertEqual(
            detail.data["data"]["project"]["api_key_prefix"], project.api_key_prefix
        )

    def test_api_key_is_hashed_at_rest(self):
        create_response = self.client.post(
            "/api/v1/projects/", {"name": "Web"}, format="json"
        )
        self.assertEqual(create_response.status_code, 201)
        raw_key = create_response.data["data"]["api_key"]
        project = Project.objects.get(name="Web")

        self.assertEqual(project.api_key_hash, hash_api_key(raw_key))
        self.assertEqual(len(project.api_key_hash), 64)
        self.assertNotEqual(project.api_key_hash, raw_key)
        self.assertTrue(project.api_key_hash.isalnum())

    def test_create_defaults_to_users_organization(self):
        response = self.client.post(
            "/api/v1/projects/", {"name": "Web"}, format="json"
        )
        self.assertEqual(response.status_code, 201)
        project = Project.objects.get()
        self.assertEqual(str(project.organization_id), self.organization_id)

    def test_create_with_unknown_organization_is_404(self):
        response = self.client.post(
            "/api/v1/projects/",
            {"name": "Web", "organization": str(uuid4())},
            format="json",
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.data["error"]["code"], "NOT_FOUND")

    def test_create_requires_auth(self):
        anonymous = APIClient()
        response = anonymous.post(
            "/api/v1/projects/", {"name": "Web"}, format="json"
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.data["error"]["code"], "NOT_AUTHENTICATED")

    def test_create_validates_name(self):
        response = self.client.post(
            "/api/v1/projects/",
            {"name": "", "organization": self.organization_id},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["error"]["code"], "VALIDATION_FAILED")


class ProjectMutationTests(TestCase):
    """Update/delete work for the project owner's organization."""

    def setUp(self):
        cache.clear()  # reset the per-email signup cap between test cases
        self.client = APIClient()
        register_and_login(self.client, "dev@trazeiq.io")
        self.organization_id = create_org(self.client, "Acme")
        self.project_id = self.client.post(
            "/api/v1/projects/", {"name": "Web"}, format="json"
        ).data["data"]["project"]["id"]

    def test_patch_updates_name_and_environment(self):
        response = self.client.patch(
            f"/api/v1/projects/{self.project_id}/",
            {"name": "API", "environment": "staging"},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        project = Project.objects.get(id=self.project_id)
        self.assertEqual(project.name, "API")
        self.assertEqual(project.environment, "staging")

    def test_delete_returns_204_and_removes(self):
        response = self.client.delete(f"/api/v1/projects/{self.project_id}/")
        self.assertEqual(response.status_code, 204)
        self.assertFalse(Project.objects.filter(id=self.project_id).exists())


class ProjectTenantIsolationTests(TestCase):
    """Agent.md rule 2: no tenant data is ever visible across organizations."""

    def setUp(self):
        cache.clear()  # reset the per-email signup cap between test cases
        self.alice = APIClient()
        register_and_login(self.alice, "alice@trazeiq.io")
        alice_org = create_org(self.alice, "Acme")
        self.project_id = self.alice.post(
            "/api/v1/projects/", {"name": "Secret", "organization": alice_org},
            format="json",
        ).data["data"]["project"]["id"]

        # Bob has his own organization (and thus a valid session of his own).
        self.bob = APIClient()
        register_and_login(self.bob, "bob@example.io")
        create_org(self.bob, "BobCorp")

    def test_cannot_list_another_orgs_project(self):
        response = self.bob.get("/api/v1/projects/")
        self.assertEqual(response.status_code, 200)
        ids = [p["id"] for p in response.data["data"]["projects"]]
        self.assertNotIn(self.project_id, ids)

    def test_cross_org_detail_is_404_not_200(self):
        response = self.bob.get(f"/api/v1/projects/{self.project_id}/")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.data["success"], False)
        self.assertEqual(response.data["error"]["code"], "NOT_FOUND")

    def test_cross_org_update_and_delete_are_404(self):
        patch = self.bob.patch(
            f"/api/v1/projects/{self.project_id}/", {"name": "Hacked"}, format="json"
        )
        self.assertEqual(patch.status_code, 404)

        delete = self.bob.delete(f"/api/v1/projects/{self.project_id}/")
        self.assertEqual(delete.status_code, 404)

        # And the project is really untouched.
        project = Project.objects.get(id=self.project_id)
        self.assertEqual(project.name, "Secret")

    def test_owner_can_still_access_own_project(self):
        detail = self.alice.get(f"/api/v1/projects/{self.project_id}/")
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.data["data"]["project"]["name"], "Secret")