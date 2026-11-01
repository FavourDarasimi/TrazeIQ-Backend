from django.core.management.base import BaseCommand, CommandError
from apps.projects.models import Project
from apps.deployments.services import record_deployment
from apps.deployments.models import Deployment
from apps.deployments.views import _resolve_project_from_key


class Command(BaseCommand):
    help = "Record a deployment marker for a TrazeIQ project."

    def add_arguments(self, parser):
        parser.add_argument(
            "--release",
            required=True,
            help="Release version identifier (e.g. v1.4.2, 2026.11.01).",
        )
        parser.add_argument(
            "--project-id",
            help="UUID of the project.",
        )
        parser.add_argument(
            "--api-key",
            help="Raw API key of the project.",
        )
        parser.add_argument(
            "--environment",
            default="production",
            help="Target environment (default: production).",
        )
        parser.add_argument(
            "--status",
            default=Deployment.Status.COMPLETED,
            choices=Deployment.Status.values,
            help="Deployment status.",
        )
        parser.add_argument(
            "--commit-sha",
            default="",
            help="Git commit SHA.",
        )
        parser.add_argument(
            "--commit-message",
            default="",
            help="Git commit message.",
        )
        parser.add_argument(
            "--commit-author",
            default="",
            help="Git commit author name/email.",
        )
        parser.add_argument(
            "--commit-url",
            default="",
            help="Web URL to the commit on GitHub/GitLab.",
        )
        parser.add_argument(
            "--pr-url",
            default="",
            help="Web URL to the associated pull request.",
        )
        parser.add_argument(
            "--pr-title",
            default="",
            help="Title of the associated pull request.",
        )
        parser.add_argument(
            "--auto-resolve",
            action="store_true",
            help="Auto-resolve incidents referenced in commit message.",
        )

    def handle(self, *args, **options):
        project_id = options.get("project_id")
        api_key = options.get("api_key")

        project = None
        if api_key:
            project = _resolve_project_from_key(api_key)
            if not project:
                raise CommandError(f"No project found for provided --api-key.")
        elif project_id:
            try:
                project = Project.objects.get(pk=project_id)
            except (Project.DoesNotExist, ValueError):
                raise CommandError(f"Project with ID '{project_id}' not found.")
        else:
            raise CommandError("Either --project-id or --api-key must be provided.")

        deployment = record_deployment(
            project=project,
            release_version=options["release"],
            environment=options["environment"],
            status=options["status"],
            commit_sha=options["commit_sha"],
            commit_message=options["commit_message"],
            commit_author=options["commit_author"],
            commit_url=options["commit_url"],
            pull_request_url=options["pr_url"],
            pull_request_title=options["pr_title"],
            auto_resolve_incidents=options["auto_resolve"],
        )

        self.stdout.write(
            self.style.SUCCESS(
                f"Successfully recorded deployment: {deployment.release.version} ({deployment.environment}) -> ID: {deployment.pk}"
            )
        )
