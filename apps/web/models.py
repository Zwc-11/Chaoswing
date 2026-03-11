from __future__ import annotations

import uuid

from django.db import models


class GraphRun(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    source_url = models.URLField()
    event_slug = models.CharField(max_length=300, blank=True)
    event_title = models.CharField(max_length=300, blank=True)
    status = models.CharField(max_length=32, default="completed")
    mode = models.CharField(max_length=64, default="deterministic-fallback")
    model_name = models.CharField(max_length=120, blank=True)
    source_snapshot = models.JSONField(default=dict, blank=True)
    graph_stats = models.JSONField(default=dict, blank=True)
    payload = models.JSONField(default=dict, blank=True)
    workflow_log = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        title = self.event_title or self.source_url
        return f"{title} [{self.mode}]"
