from __future__ import annotations

from django.db import migrations, models
from django.db.models import Q

REQUIRED_STAGES = ["planner", "retriever", "graph_editor", "critic", "verifier"]


def dedupe_required_agent_traces(apps, schema_editor):
    AgentTrace = apps.get_model("web", "AgentTrace")
    duplicate_keys = (
        AgentTrace.objects.filter(stage__in=REQUIRED_STAGES)
        .values("graph_run_id", "stage")
        .annotate(row_count=models.Count("id"))
        .filter(row_count__gt=1)
    )
    for key in duplicate_keys:
        duplicates = list(
            AgentTrace.objects.filter(
                graph_run_id=key["graph_run_id"],
                stage=key["stage"],
            ).order_by("-created_at", "-id")
        )
        keep = duplicates[0]
        AgentTrace.objects.filter(
            graph_run_id=key["graph_run_id"],
            stage=key["stage"],
        ).exclude(id=keep.id).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("web", "0007_relatedmarketjudgment"),
    ]

    operations = [
        migrations.RunPython(
            dedupe_required_agent_traces,
            migrations.RunPython.noop,
        ),
        migrations.AddConstraint(
            model_name="agenttrace",
            constraint=models.UniqueConstraint(
                condition=Q(stage__in=REQUIRED_STAGES),
                fields=("graph_run", "stage"),
                name="web_agent_trace_required_stage_unique",
            ),
        ),
    ]
