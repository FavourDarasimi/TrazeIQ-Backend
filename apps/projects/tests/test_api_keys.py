from datetime import timedelta
from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.organizations.models import Membership, MembershipRole
from apps.projects.models import ApiKeyScope, ApiKeyStatus, Project, ProjectApiKey
from apps.projects.tests.test_projects import create_org, register_and_login


class ProjectApiKeyManagementTests(TestCase):
    def setUp(self):
        cache.clear()
        self.owner_client = APIClient()
        self.ingest_client = APIClient()
        register_and_login(self.owner_client, "owner@example.com")
        self.org_id = create_org(self.owner_client, "Acme Corp")

        # Create project
        resp = self.owner_client.post(
            "/api/v1/projects/",
            {"name": "Backend Service", "organization": self.org_id},
            format="json",
        )
        self.assertEqual(resp.status_code, 201)
        self.project_id = resp.data["data"]["project"]["id"]
        self.default_raw_key = resp.data["data"]["api_key"]

    def test_project_has_default_api_key_on_creation(self):
        resp = self.owner_client.get(f"/api/v1/projects/{self.project_id}/keys/")
        self.assertEqual(resp.status_code, 200)
        keys = resp.data["data"]["keys"]
        self.assertEqual(len(keys), 1)
        self.assertEqual(keys[0]["name"], "Default Key")
        self.assertEqual(keys[0]["status"], ApiKeyStatus.ACTIVE)
        self.assertIn(ApiKeyScope.INGEST_ONLY, keys[0]["scopes"])
        self.assertEqual(keys[0]["request_count"], 0)
        self.assertIsNone(keys[0]["last_used_at"])

    def test_create_scoped_api_key(self):
        resp = self.owner_client.post(
            f"/api/v1/projects/{self.project_id}/keys/",
            {
                "name": "CI Sourcemaps",
                "scopes": [ApiKeyScope.SOURCEMAPS_UPLOAD, ApiKeyScope.READ_ONLY],
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 201)
        data = resp.data["data"]
        self.assertIn("api_key", data)
        self.assertEqual(len(data["api_key"]), 64)
        key_data = data["key"]
        self.assertEqual(key_data["name"], "CI Sourcemaps")
        self.assertEqual(key_data["status"], ApiKeyStatus.ACTIVE)
        self.assertListEqual(
            key_data["scopes"],
            [ApiKeyScope.SOURCEMAPS_UPLOAD, ApiKeyScope.READ_ONLY],
        )

        # List reflects new key
        list_resp = self.owner_client.get(f"/api/v1/projects/{self.project_id}/keys/")
        self.assertEqual(len(list_resp.data["data"]["keys"]), 2)

    def test_revoke_api_key(self):
        # Create a key
        create_resp = self.owner_client.post(
            f"/api/v1/projects/{self.project_id}/keys/",
            {"name": "Temporary Worker"},
            format="json",
        )
        raw_key = create_resp.data["data"]["api_key"]
        key_id = create_resp.data["data"]["key"]["id"]

        # Valid before revocation
        ingest_resp = self.ingest_client.post(
            "/api/v1/events/",
            {"message": "Worker running", "level": "info"},
            headers={"X-API-Key": raw_key},
            format="json",
        )
        self.assertEqual(ingest_resp.status_code, 201)

        # Revoke the key
        del_resp = self.owner_client.delete(
            f"/api/v1/projects/{self.project_id}/keys/{key_id}/"
        )
        self.assertEqual(del_resp.status_code, 200)
        self.assertEqual(del_resp.data["data"]["key"]["status"], ApiKeyStatus.REVOKED)
        self.assertIsNotNone(del_resp.data["data"]["key"]["revoked_at"])

        # Ingestion is now rejected
        rejected_resp = self.ingest_client.post(
            "/api/v1/events/",
            {"message": "Worker running after revoke", "level": "info"},
            headers={"X-API-Key": raw_key},
            format="json",
        )
        self.assertEqual(rejected_resp.status_code, 401)

    def test_rotate_api_key_with_grace_period(self):
        # Create a key
        create_resp = self.owner_client.post(
            f"/api/v1/projects/{self.project_id}/keys/",
            {"name": "Production API"},
            format="json",
        )
        old_raw_key = create_resp.data["data"]["api_key"]
        old_key_id = create_resp.data["data"]["key"]["id"]

        # Rotate with 48h grace period
        rotate_resp = self.owner_client.post(
            f"/api/v1/projects/{self.project_id}/keys/{old_key_id}/rotate/",
            {"grace_period_hours": 48},
            format="json",
        )
        self.assertEqual(rotate_resp.status_code, 200)
        new_raw_key = rotate_resp.data["data"]["api_key"]
        old_key_data = rotate_resp.data["data"]["old_key"]
        new_key_data = rotate_resp.data["data"]["key"]

        self.assertEqual(old_key_data["status"], ApiKeyStatus.GRACE_PERIOD)
        self.assertIsNotNone(old_key_data["expires_at"])
        self.assertEqual(new_key_data["status"], ApiKeyStatus.ACTIVE)

        # Old key STILL WORKS during the grace period!
        old_ingest = self.ingest_client.post(
            "/api/v1/events/",
            {"message": "From old key during grace", "level": "warning"},
            headers={"X-API-Key": old_raw_key},
            format="json",
        )
        self.assertEqual(old_ingest.status_code, 201)

        # New key works too
        new_ingest = self.ingest_client.post(
            "/api/v1/events/",
            {"message": "From new key", "level": "warning"},
            headers={"X-API-Key": new_raw_key},
            format="json",
        )
        self.assertEqual(new_ingest.status_code, 201)

        # Advance expires_at to the past to simulate expiration of grace period
        old_obj = ProjectApiKey.objects.get(id=old_key_id)
        old_obj.expires_at = timezone.now() - timedelta(seconds=1)
        old_obj.save(update_fields=["expires_at"])

        # Now old key fails with 401
        expired_ingest = self.ingest_client.post(
            "/api/v1/events/",
            {"message": "After grace expired", "level": "warning"},
            headers={"X-API-Key": old_raw_key},
            format="json",
        )
        self.assertEqual(expired_ingest.status_code, 401)

        # But new key continues to work
        new_ingest2 = self.ingest_client.post(
            "/api/v1/events/",
            {"message": "From new key after expiration", "level": "warning"},
            headers={"X-API-Key": new_raw_key},
            format="json",
        )
        self.assertEqual(new_ingest2.status_code, 201)

    def test_request_auditing_and_last_used_timestamp(self):
        create_resp = self.owner_client.post(
            f"/api/v1/projects/{self.project_id}/keys/",
            {"name": "Audited Key"},
            format="json",
        )
        raw_key = create_resp.data["data"]["api_key"]
        key_id = create_resp.data["data"]["key"]["id"]

        # Send 3 events
        for i in range(3):
            resp = self.ingest_client.post(
                "/api/v1/events/",
                {"message": f"Event {i}", "level": "error"},
                headers={"X-API-Key": raw_key},
                format="json",
            )
            self.assertEqual(resp.status_code, 201)

        key_obj = ProjectApiKey.objects.get(id=key_id)
        self.assertEqual(key_obj.request_count, 3)
        self.assertIsNotNone(key_obj.last_used_at)

    def test_scope_enforcement_blocks_non_ingest_key(self):
        # Create key with only read_only scope
        create_resp = self.owner_client.post(
            f"/api/v1/projects/{self.project_id}/keys/",
            {"name": "Dashboard Read Only", "scopes": [ApiKeyScope.READ_ONLY]},
            format="json",
        )
        raw_key = create_resp.data["data"]["api_key"]

        # Attempt to ingest with a read-only key must be forbidden (403)
        resp = self.ingest_client.post(
            "/api/v1/events/",
            {"message": "Illegal ingest", "level": "error"},
            headers={"X-API-Key": raw_key},
            format="json",
        )
        self.assertEqual(resp.status_code, 403)
        self.assertIn("ingest_only", resp.data["message"])
