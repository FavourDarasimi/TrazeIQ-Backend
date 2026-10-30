"""Unit tests for the tokenized search parser and ORM query builder."""

from django.test import TestCase
from django.utils import timezone

from apps.events.models import ErrorGroup, Event
from apps.incidents.models import Incident
from apps.incidents.selectors import list_incidents_for_user
from apps.organizations.models import Membership, MembershipRole, Organization
from apps.platform.search_parser import (
    SearchToken,
    build_event_search_q,
    build_incident_search_q,
    parse_search_query,
)
from apps.projects.models import Project
from django.contrib.auth import get_user_model

User = get_user_model()


class SearchParserTokenTests(TestCase):
    def test_parse_empty_query(self):
        self.assertEqual(parse_search_query(""), [])
        self.assertEqual(parse_search_query("   "), [])
        self.assertEqual(parse_search_query(None), [])

    def test_parse_key_value(self):
        tokens = parse_search_query("status:open severity:critical")
        self.assertEqual(len(tokens), 2)
        self.assertEqual(tokens[0], SearchToken(key="status", value="open", negated=False, raw="status:open"))
        self.assertEqual(tokens[1], SearchToken(key="severity", value="critical", negated=False, raw="severity:critical"))

    def test_parse_negation(self):
        tokens = parse_search_query("-status:resolved !severity:low -env:dev")
        self.assertEqual(len(tokens), 3)
        self.assertEqual(tokens[0], SearchToken(key="status", value="resolved", negated=True, raw="-status:resolved"))
        self.assertEqual(tokens[1], SearchToken(key="severity", value="low", negated=True, raw="!severity:low"))
        self.assertEqual(tokens[2], SearchToken(key="env", value="dev", negated=True, raw="-env:dev"))

    def test_parse_quoted_values(self):
        tokens = parse_search_query('service:"payment gateway" metadata.os:"Mac OS X" "database connection error"')
        self.assertEqual(len(tokens), 3)
        self.assertEqual(tokens[0].key, "service")
        self.assertEqual(tokens[0].value, "payment gateway")
        self.assertFalse(tokens[0].negated)

        self.assertEqual(tokens[1].key, "metadata.os")
        self.assertEqual(tokens[1].value, "Mac OS X")
        self.assertFalse(tokens[1].negated)

        self.assertIsNone(tokens[2].key)
        self.assertEqual(tokens[2].value, "database connection error")
        self.assertFalse(tokens[2].negated)

    def test_parse_combined_syntax(self):
        query = 'is:open severity:critical env:production user:usr_123 metadata.browser:Chrome -service:auth "timeout occurred"'
        tokens = parse_search_query(query)
        keys_and_values = [(t.key, t.value, t.negated) for t in tokens]
        self.assertEqual(
            keys_and_values,
            [
                ("is", "open", False),
                ("severity", "critical", False),
                ("env", "production", False),
                ("user", "usr_123", False),
                ("metadata.browser", "Chrome", False),
                ("service", "auth", True),
                (None, "timeout occurred", False),
            ],
        )


class SearchParserIncidentIntegrationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="analyst", email="analyst@trazeiq.io", password="password123"
        )
        self.org = Organization.objects.create(name="Acme Corp", owner=self.user)
        Membership.objects.create(
            user=self.user, organization=self.org, role=MembershipRole.DEVELOPER
        )
        self.project = Project.objects.create(
            name="Payment Gateway", organization=self.org
        )

        # Seed error group 1 and incident 1 (critical, open, production, Chrome)
        self.group1 = ErrorGroup.objects.create(
            project=self.project,
            fingerprint="fp_group_1",
            title="DatabaseConnectionTimeout in checkout",
            first_seen=timezone.now(),
            last_seen=timezone.now(),
        )
        self.incident1 = Incident.objects.create(
            project=self.project,
            error_group=self.group1,
            severity=Incident.Severity.CRITICAL,
            status=Incident.Status.OPEN,
            assigned_to=self.user,
        )
        self.event1 = Event.objects.create(
            project=self.project,
            error_group=self.group1,
            message="Connection timeout connecting to db",
            environment="production",
            service="checkout-api",
            endpoint="/v1/checkout",
            user_id="usr_100",
            metadata={"browser": "Google Chrome", "release": "v1.2.0"},
            fingerprint="fp_group_1",
        )

        # Seed error group 2 and incident 2 (low, resolved, staging, Firefox)
        self.group2 = ErrorGroup.objects.create(
            project=self.project,
            fingerprint="fp_group_2",
            title="InvalidPostalCodeFormat in profile",
            first_seen=timezone.now(),
            last_seen=timezone.now(),
        )
        self.incident2 = Incident.objects.create(
            project=self.project,
            error_group=self.group2,
            severity=Incident.Severity.LOW,
            status=Incident.Status.RESOLVED,
            assigned_to=None,
        )
        self.event2 = Event.objects.create(
            project=self.project,
            error_group=self.group2,
            message="Postal code invalid format",
            environment="staging",
            service="profile-api",
            endpoint="/v1/profile",
            user_id="usr_200",
            metadata={"browser": "Mozilla Firefox", "release": "v2.0.0"},
            fingerprint="fp_group_2",
        )

    def test_filter_by_is_status(self):
        qs = list_incidents_for_user(self.user, search="is:open")
        self.assertEqual(list(qs), [self.incident1])

        qs = list_incidents_for_user(self.user, search="is:resolved")
        self.assertEqual(list(qs), [self.incident2])

    def test_filter_by_negated_status(self):
        qs = list_incidents_for_user(self.user, search="-status:resolved")
        self.assertEqual(list(qs), [self.incident1])

    def test_filter_by_severity(self):
        qs = list_incidents_for_user(self.user, search="severity:critical")
        self.assertEqual(list(qs), [self.incident1])

        qs = list_incidents_for_user(self.user, search="-severity:low")
        self.assertEqual(list(qs), [self.incident1])

    def test_filter_by_environment(self):
        qs = list_incidents_for_user(self.user, search="env:production")
        self.assertEqual(list(qs), [self.incident1])

        qs = list_incidents_for_user(self.user, search="env:staging")
        self.assertEqual(list(qs), [self.incident2])

    def test_filter_by_user_id(self):
        qs = list_incidents_for_user(self.user, search="user:usr_100")
        self.assertEqual(list(qs), [self.incident1])

    def test_filter_by_metadata_json_lookup(self):
        qs = list_incidents_for_user(self.user, search='metadata.browser:"Google Chrome"')
        self.assertEqual(list(qs), [self.incident1])

        qs = list_incidents_for_user(self.user, search="metadata.browser:Firefox")
        self.assertEqual(list(qs), [self.incident2])

    def test_filter_by_assigned_and_unassigned(self):
        qs = list_incidents_for_user(self.user, search="is:assigned")
        self.assertEqual(list(qs), [self.incident1])

        qs = list_incidents_for_user(self.user, search="is:unassigned")
        self.assertEqual(list(qs), [self.incident2])

    def test_combined_search_tokens(self):
        qs = list_incidents_for_user(
            self.user,
            search='is:open severity:critical env:production user:usr_100 metadata.browser:Chrome "timeout"',
        )
        self.assertEqual(list(qs), [self.incident1])

        # If a contradiction or non-matching term is added, returns empty
        qs = list_incidents_for_user(
            self.user,
            search='is:open severity:critical -env:production',
        )
        self.assertEqual(list(qs), [])


class SearchParserEventIntegrationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="eventanalyst", email="events@trazeiq.io", password="password123"
        )
        self.org = Organization.objects.create(name="Acme Events", owner=self.user)
        Membership.objects.create(
            user=self.user, organization=self.org, role=MembershipRole.DEVELOPER
        )
        self.project = Project.objects.create(
            name="API Service", organization=self.org
        )
        self.group = ErrorGroup.objects.create(
            project=self.project,
            fingerprint="fp_evt_1",
            title="NullPointerException in worker",
            first_seen=timezone.now(),
            last_seen=timezone.now(),
        )
        self.event1 = Event.objects.create(
            project=self.project,
            error_group=self.group,
            message="NullPointer in AuthWorker.process",
            level=Event.Level.FATAL,
            environment="production",
            service="auth-worker",
            endpoint="/internal/auth",
            user_id="usr_999",
            metadata={"release": "v3.0.0", "datacenter": "us-east-1"},
            fingerprint="fp_evt_1",
        )
        self.event2 = Event.objects.create(
            project=self.project,
            error_group=self.group,
            message="Slow query warning",
            level=Event.Level.WARNING,
            environment="staging",
            service="query-runner",
            endpoint="/internal/query",
            user_id="usr_888",
            metadata={"release": "v3.1.0", "datacenter": "eu-west-1"},
            fingerprint="fp_evt_2",
        )

    def test_filter_events_tokenized(self):
        from apps.events.selectors import list_events_for_user

        qs = list_events_for_user(self.user, search="is:fatal env:production")
        self.assertEqual(list(qs), [self.event1])

        qs = list_events_for_user(self.user, search="metadata.datacenter:eu-west-1")
        self.assertEqual(list(qs), [self.event2])

        qs = list_events_for_user(self.user, search="-level:fatal")
        self.assertEqual(list(qs), [self.event2])

