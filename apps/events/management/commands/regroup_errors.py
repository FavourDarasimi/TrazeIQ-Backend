from django.core.management.base import BaseCommand, CommandError

from apps.projects.models import Project
from apps.events.services import regroup_project_error_groups


class Command(BaseCommand):
    help = (
        "Regroup project errors using fingerprint algorithm v2 "
        "(in-app frame filtering, chunk normalization, line number immunity)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--project",
            type=str,
            help="UUID or slug of a specific project to regroup. If omitted, regroups all projects.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Simulate the regroup operation and print expected consolidation counts without committing changes.",
        )

    def handle(self, *args, **options):
        project_arg = options.get("project")
        dry_run = options.get("dry_run", False)

        if project_arg:
            projects = Project.objects.filter(id=project_arg)
            if not projects.exists():
                projects = Project.objects.filter(slug=project_arg)
            if not projects.exists():
                raise CommandError(f"Project '{project_arg}' not found.")
        else:
            projects = Project.objects.all()

        total_consolidated = 0
        total_events = 0

        self.stdout.write(
            self.style.NOTICE(
                f"Starting regroup for {projects.count()} project(s) (dry_run={dry_run})..."
            )
        )

        for proj in projects:
            res = regroup_project_error_groups(proj, dry_run=dry_run)
            total_consolidated += res["groups_consolidated"]
            total_events += res["events_reprocessed"]
            self.stdout.write(
                f"  [{proj.name}] Events: {res['events_reprocessed']}, "
                f"Groups: {res['groups_before']} -> {res['groups_after']} "
                f"({res['groups_consolidated']} consolidated)"
            )

        self.stdout.write(
            self.style.SUCCESS(
                f"Done. Reprocessed {total_events} events, consolidated {total_consolidated} groups."
            )
        )
