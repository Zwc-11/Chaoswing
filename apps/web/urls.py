from django.urls import path

from . import api_views, partial_views, views

app_name = "web"

urlpatterns = [
    path("", views.landing, name="landing"),
    path("app/", views.dashboard, name="dashboard"),
    path("api/v1/graph/from-url/", api_views.graph_from_url, name="graph_from_url"),
    path("api/v1/runs/", api_views.list_graph_runs, name="list_graph_runs"),
    path("api/v1/runs/<uuid:run_id>/", api_views.graph_run_detail, name="graph_run_detail"),
    path("api/v1/runs/<uuid:run_id>/review/", api_views.review_graph_run, name="review_graph_run"),
    path("api/v1/markets/trending/", api_views.trending_markets, name="trending_markets"),
    path("api/v1/markets/verify/", api_views.verify_links, name="verify_links"),
    path("partials/inspector/empty/", partial_views.inspector_empty, name="inspector_empty_partial"),
    path("partials/inspector/node/", partial_views.inspector_node, name="inspector_node_partial"),
    path("partials/inspector/edge/", partial_views.inspector_edge, name="inspector_edge_partial"),
]
