from django.test import TestCase
from django.utils import timezone
from django.db import IntegrityError

from apps.accounts.models import User
from apps.organizations.models import Organization, Membership
from apps.projects.models import Project
from apps.events.models import Event, ErrorGroup
from apps.incidents.models import Incident
from apps.deployments.models import Release, Deployment


class DeploymentsModelTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="deployer@trazeiq.io", password="StrongPassword123!"
        )
        self.org = Organization.objects.create(name="Acme Corp", owner=self.user)
        Membership.objects.create(
            user=self.user,
            organization=self.org,
            role="owner",
        )
        self.project = Project.objects.create(
            organization=self.org,
            name="Checkout Service",
            api_key_hash="hash123",
            api_key_prefix="tr_live_",
            environment="production",
        )

    def test_create_release_and_deployment(self):
        release = Release.objects.create(
            project=self.project,
            version="v1.4.2",
            commit_sha="4f81b37abc",
            commit_message="feat: optimize checkout cache (#42)",
            commit_author="Devin Vance <devin@trazeiq.io>",
            commit_url="https://github.com/acme/checkout/commit/4f81b37abc",
            pull_request_url="https://github.com/acme/checkout/pull/42",
            pull_request_title="feat: optimize checkout cache (#42)",
        )
        self.assertEqual(str(release), f"{self.project.pk} @ v1.4.2")

        deploy = Deployment.objects.create(
            project=self.project,
            release=release,
            environment="production",
            status=Deployment.Status.COMPLETED,
        )
        self.assertEqual(deploy.release.version, "v1.4.2")
        self.assertEqual(deploy.status, Deployment.Status.COMPLETED)
        self.assertIn("v1.4.2", str(deploy))

    def test_release_version_unique_per_project(self):
        Release.objects.create(
            project=self.project,
            version="v1.0.0",
        )
        with self.assertRaises(IntegrityError):
            Release.objects.create(
                project=self.project,
                version="v1.0.0",
            )

    def test_event_has_release_field(self):
        group = ErrorGroup.objects.create(
            project=self.project,
            fingerprint="fp12345",
            title="ZeroDivisionError",
            first_seen=timezone.now(),
            last_seen=timezone.now(),
        )
        event = Event.objects.create(
            project=self.project,
            error_group=group,
            message="ZeroDivisionError: division by zero",
            release="v1.4.2",
        )
        self.assertEqual(event.release, "v1.4.2")

    def test_incident_status_supports_fix_verified(self):
        group = ErrorGroup.objects.create(
            project=self.project,
            fingerprint="fp67890",
            title="IndexError",
            first_seen=timezone.now(),
            last_seen=timezone.now(),
        )
        incident = Incident.objects.create(
            project=self.project,
            error_group=group,
            status=Incident.Status.FIX_VERIFIED,
        )
        self.assertEqual(incident.status, "fix_verified")
