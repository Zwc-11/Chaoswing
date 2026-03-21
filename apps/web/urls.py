from django.urls import path

from . import api_views, partial_views, views

app_name = "web"

urlpatterns = [
    path("", views.landing, name="landing"),
    path("app/", views.dashboard, name="dashboard"),
    path("developers/api/", views.api_docs, name="api_docs"),
    path("briefs/<uuid:run_id>/", views.market_brief, name="market_brief"),
    path("lead-lag/", views.leadlag_monitor, name="leadlag_monitor"),
    path("benchmarks/", views.benchmarks, name="benchmarks"),
    path("benchmarks/review/related-markets/", views.related_market_review, name="related_market_review"),
    path("watchlists/", views.watchlists, name="watchlists"),
    path("api/", api_views.api_root, name="api_root"),
    path("api/openapi.json", api_views.openapi_spec, name="openapi_spec"),
    path("api/v1/graph/from-url/", api_views.graph_from_url, name="graph_from_url"),
    path("api/v1/runs/", api_views.list_graph_runs, name="list_graph_runs"),
    path("api/v1/runs/<uuid:run_id>/", api_views.graph_run_detail, name="graph_run_detail"),
    path("api/v1/runs/<uuid:run_id>/brief/", api_views.graph_run_brief, name="graph_run_brief"),
    path("api/v1/runs/<uuid:run_id>/related-markets/", api_views.graph_run_related_markets, name="graph_run_related_markets"),
    path("api/v1/runs/<uuid:run_id>/changes/", api_views.graph_run_changes, name="graph_run_changes"),
    path("api/v1/runs/<uuid:run_id>/review/", api_views.review_graph_run, name="review_graph_run"),
    path("api/v1/benchmarks/summary/", api_views.benchmark_summary, name="benchmark_summary"),
    path("api/v1/benchmarks/related-market-review/", api_views.related_market_review_queue, name="related_market_review_queue"),
    path("api/v1/benchmarks/related-market-review/submit/", api_views.submit_related_market_judgment, name="submit_related_market_judgment"),
    path("api/v1/lead-lag/summary/", api_views.leadlag_summary, name="leadlag_summary"),
    path("api/v1/lead-lag/signals/", api_views.leadlag_signals, name="leadlag_signals"),
    path("api/v1/lead-lag/pairs/", api_views.leadlag_pairs, name="leadlag_pairs"),
    path("api/v1/lead-lag/pairs/<int:pair_id>/", api_views.leadlag_pair_detail, name="leadlag_pair_detail"),
    path("api/v1/markets/trending/", api_views.trending_markets, name="trending_markets"),
    path("api/v1/markets/verify/", api_views.verify_links, name="verify_links"),
    path("api/v1/watchlists/", api_views.list_watchlists, name="watchlists_api"),
    path("partials/inspector/empty/", partial_views.inspector_empty, name="inspector_empty_partial"),
    path("partials/inspector/node/", partial_views.inspector_node, name="inspector_node_partial"),
    path("partials/inspector/edge/", partial_views.inspector_edge, name="inspector_edge_partial"),
]
