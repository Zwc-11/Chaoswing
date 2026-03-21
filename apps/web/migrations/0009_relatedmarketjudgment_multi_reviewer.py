from __future__ import annotations

import re

from django.db import migrations, models


def _normalized_reviewer_key(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", str(value or "").strip().lower()).strip("-")
    return normalized[:80] or "anonymous"


def populate_reviewer_keys(apps, schema_editor):
    RelatedMarketJudgment = apps.get_model("web", "RelatedMarketJudgment")
    for judgment in RelatedMarketJudgment.objects.all().iterator():
        reviewer_key = _normalized_reviewer_key(getattr(judgment, "reviewer", ""))
        if judgment.reviewer_key != reviewer_key:
            judgment.reviewer_key = reviewer_key
            judgment.save(update_fields=["reviewer_key"])


class Migration(migrations.Migration):
    dependencies = [
        ("web", "0008_agenttrace_required_stage_unique"),
    ]

    operations = [
        migrations.AddField(
            model_name="relatedmarketjudgment",
            name="reviewer_key",
            field=models.CharField(db_index=True, default="anonymous", max_length=80),
        ),
        migrations.RunPython(populate_reviewer_keys, migrations.RunPython.noop),
        migrations.RemoveConstraint(
            model_name="relatedmarketjudgment",
            name="web_related_market_judgment_unique",
        ),
        migrations.AddConstraint(
            model_name="relatedmarketjudgment",
            constraint=models.UniqueConstraint(
                fields=("graph_run", "candidate_key", "reviewer_key"),
                name="web_related_market_judgment_reviewer_unique",
            ),
        ),
    ]
