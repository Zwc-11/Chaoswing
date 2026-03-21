import json

from django.test import Client, TestCase
from django.test.utils import override_settings
from django.urls import reverse


@override_settings(
    CHAOSWING_ENABLE_REMOTE_FETCH=False,
    CHAOSWING_ENABLE_LLM=False,
    CHAOSWING_RATE_LIMIT_ENABLED=False,
)
class ApiDocsTests(TestCase):
    def test_api_docs_page_renders_generated_reference(self):
        response = self.client.get(reverse("web:api_docs"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Developer API Reference")
        self.assertContains(response, "Download OpenAPI JSON")
        self.assertContains(response, "Integration workflows")
        self.assertContains(response, "Facts")
        self.assertContains(response, "/api/v1/graph/from-url/")
        self.assertContains(response, "/api/openapi.json")

    def test_api_root_exposes_discovery_document(self):
        response = self.client.get(reverse("web:api_root"))
        payload = response.json()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload["version"], "v1")
        self.assertEqual(payload["docs_url"], reverse("web:api_docs"))
        self.assertEqual(payload["openapi_url"], reverse("web:openapi_spec"))
        self.assertTrue(any(item["path"] == "/api/v1/graph/from-url/" for item in payload["resources"]))
        self.assertEqual(response["X-ChaosWing-Api-Version"], "v1")
        self.assertIn(reverse("web:api_docs"), response["Link"])

    def test_openapi_endpoint_includes_core_paths(self):
        response = self.client.get(reverse("web:openapi_spec"))
        payload = response.json()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload["openapi"], "3.1.0")
        self.assertEqual(payload["info"]["title"], "ChaosWing API")
        self.assertIn("/api/v1/graph/from-url/", payload["paths"])
        self.assertIn("post", payload["paths"]["/api/v1/graph/from-url/"])
        self.assertIn("/api/v1/runs/{run_id}/brief/", payload["paths"])

    def test_public_post_api_is_usable_with_csrf_checks_enabled(self):
        client = Client(enforce_csrf_checks=True)

        response = client.post(
            reverse("web:graph_from_url"),
            data=json.dumps({"url": "https://polymarket.com/event/fed-decision-in-march-885"}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["X-ChaosWing-Api-Version"], "v1")
        self.assertIn(reverse("web:openapi_spec"), response["Link"])
