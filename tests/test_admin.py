"""Admin HTTP workflows against migrated isolated storage; secrets exist only at runtime."""
import os
import secrets
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.server import app
from backend.services.admin_auth import COOKIE
from backend.services.scheme_service import SchemeService
from backend.services.session_store import InMemorySessionStore
from tests.support import test_database as make_database


class AdminTests(unittest.TestCase):
    def setUp(self):
        self.password = secrets.token_urlsafe(32)
        self.env = patch.dict(os.environ, {"ADMIN_PASSWORD": self.password,
            "ADMIN_SESSION_SECRET": secrets.token_urlsafe(48), "ADMIN_COOKIE_SECURE": "false"})
        self.env.start()
        self.engine, self.factory = make_database()
        with self.factory.begin() as session:
            service = SchemeService(session)
            scheme = service.create_with_rules(name="Central education", government_level="CENTRAL",
                state="All India", category="education", status_confidence="NEEDS_REVIEW",
                manual_conditions=["Verify the original certificate"], source_metadata={"master": {"untouched": True}},
                rules=[{"field": "age", "operator": ">=", "value": 18, "value_type": "integer"}])
            self.scheme_id = scheme.id
            service.create_with_rules(name="State education", government_level="STATE", state="Bihar",
                category="education", status_confidence="VERIFIED", rules=[], is_active=False)
            service.create_with_rules(name="State 100% support", government_level="STATE", state="Bihar",
                category="social_welfare", status_confidence="LIKELY_ACTIVE", rules=[])
        app.state.session_factory = self.factory
        app.state.conversation_store = InMemorySessionStore()
        self.client = TestClient(app).__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        del app.state.session_factory
        del app.state.conversation_store
        self.engine.dispose()
        self.env.stop()

    def login(self):
        response = self.client.post("/admin/login", json={"password": self.password}, headers={"X-Admin-Login": "1"})
        # Do not include the request payload or cookie value in assertion diagnostics.
        self.assertEqual(response.status_code, 200)
        self.client.headers["X-Admin-CSRF"] = self.client.get("/api/admin/session").json()["csrf"]
        return response

    def detail(self):
        return self.client.get(f"/api/admin/schemes/{self.scheme_id}").json()

    def test_all_admin_resources_require_authentication(self):
        self.assertEqual(self.client.get("/admin", follow_redirects=False).status_code, 303)
        self.assertEqual(self.client.get("/static/panel.html").status_code, 404)
        for method, path in [("GET", "/session"), ("GET", "/filters"), ("GET", "/schemes"),
                             ("GET", "/schemes/1"), ("PATCH", "/schemes/1"),
                             ("POST", "/schemes/1/rules"), ("PUT", "/schemes/1/rules/1"),
                             ("DELETE", "/schemes/1/rules/1"), ("POST", "/logout")]:
            with self.subTest(method=method, path=path):
                self.assertEqual(self.client.request(method, "/api/admin" + path, json={}).status_code, 401)

    def test_login_cookie_csrf_logout_and_no_secret_echo(self):
        response = self.client.post("/admin/login", json={"password": secrets.token_urlsafe(20)}, headers={"X-Admin-Login": "1"})
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json(), {"detail": "Incorrect admin password."})
        self.assertEqual(self.client.post("/admin/login", json={"password": self.password}).status_code, 403)
        response = self.login()
        cookie = response.headers["set-cookie"].lower()
        self.assertTrue("httponly" in cookie and "samesite=strict" in cookie)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(self.client.get("/admin").status_code, 200)
        self.assertEqual(self.client.patch("/api/admin/schemes/1", json={"is_active": False},
                                          headers={"X-Admin-CSRF": ""}).status_code, 403)
        self.assertEqual(self.client.post("/api/admin/logout").status_code, 200)
        self.assertIsNone(self.client.cookies.get(COOKIE))
        self.assertEqual(self.client.get("/api/admin/schemes").status_code, 401)

    def test_expired_tampered_and_rotated_sessions(self):
        self.login()
        with patch("backend.services.admin_auth.time.time", return_value=0):
            self.login()
        self.assertEqual(self.client.get("/api/admin/session").status_code, 401)
        self.login()
        self.client.cookies.clear()
        self.client.cookies.set(COOKIE, "invalid.session.signature")
        self.assertEqual(self.client.get("/api/admin/session").status_code, 401)
        self.client.cookies.clear()
        self.login()
        with patch.dict(os.environ, {"ADMIN_PASSWORD": secrets.token_urlsafe(32)}):
            self.assertEqual(self.client.get("/api/admin/session").status_code, 401)

    def test_disabled_when_unconfigured_and_https_cookie(self):
        for variable in ["ADMIN_PASSWORD", "ADMIN_SESSION_SECRET"]:
            with patch.dict(os.environ, {variable: ""}):
                self.assertEqual(self.client.get("/api/admin/schemes").status_code, 503)
        with patch.dict(os.environ, {"ADMIN_COOKIE_SECURE": "true"}):
            response = self.client.post("/admin/login", json={"password": self.password}, headers={"X-Admin-Login": "1"})
            self.assertTrue("; Secure" in response.headers["set-cookie"])

    def test_search_filters_pagination_and_options(self):
        self.login()
        cases = [({}, 3), ({"is_active": "true"}, 2), ({"is_active": "false"}, 1),
                 ({"government_level": "CENTRAL"}, 1), ({"state_or_ut": "Bihar"}, 2),
                 ({"category": "education"}, 2), ({"status_confidence": "NEEDS_REVIEW"}, 1),
                 ({"status_confidence": "LIKELY_ACTIVE"}, 1), ({"status_confidence": "VERIFIED"}, 1),
                 ({"search": "EDUCATION"}, 2), ({"search": "%"}, 1),
                 ({"search": "education", "is_active": "false", "state_or_ut": "Bihar"}, 1)]
        for params, count in cases:
            with self.subTest(params=params):
                response = self.client.get("/api/admin/schemes", params=params)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["total"], count)
        first = self.client.get("/api/admin/schemes?page_size=1&page=1").json()
        second = self.client.get("/api/admin/schemes?page_size=1&page=2").json()
        self.assertNotEqual(first["items"][0]["id"], second["items"][0]["id"])
        self.assertEqual(self.client.get("/api/admin/schemes?page=0").status_code, 422)
        self.assertEqual(self.client.get("/api/admin/filters").json()["state_or_ut"], ["All India", "Bihar"])

    def test_metadata_edit_archive_and_preserve_provenance(self):
        self.login()
        before = self.detail()
        patch_data = {"name": "Updated scheme", "official_url": "https://example.gov.in/scheme",
                      "secondary_source_url": "https://example.org/source", "last_checked_date": "2026-09-19",
                      "benefit_amount": "Varies", "confidence_notes": "Check locally", "is_active": False}
        response = self.client.patch(f"/api/admin/schemes/{self.scheme_id}", json=patch_data)
        self.assertEqual(response.status_code, 200)
        for field, value in patch_data.items():
            self.assertEqual(response.json()[field], value)
        for field in ["manual_conditions", "rules", "source_metadata", "status_confidence"]:
            self.assertEqual(response.json()[field], before[field])
        self.assertNotIn(self.scheme_id, [s["id"] for s in self.client.get("/api/schemes").json()])
        self.assertNotIn(self.scheme_id, [s["scheme_id"] for s in self.client.post("/api/eligibility", json={"attributes": {}}).json()])
        self.assertEqual(self.client.delete(f"/api/admin/schemes/{self.scheme_id}").status_code, 405)
        self.client.patch(f"/api/admin/schemes/{self.scheme_id}", json={"is_active": True})
        self.assertIn(self.scheme_id, [s["id"] for s in self.client.get("/api/schemes").json()])

    def test_invalid_metadata_is_atomic(self):
        self.login()
        before = self.detail()
        for payload in [{"name": None}, {"name": " "}, {"is_active": None}, {"is_active": "false"},
                        {"status_confidence": "CERTAIN"}, {"government_level": "OTHER"},
                        {"official_url": "javascript:alert(1)"}, {"last_checked_date": "2026-02-31"},
                        {"category": "x" * 101}, {"source_metadata": {}}, {"manual_conditions": []},
                        {"name": "Changed", "official_url": "not a URL"}]:
            with self.subTest(payload=payload):
                self.assertEqual(self.client.patch(f"/api/admin/schemes/{self.scheme_id}", json=payload).status_code, 422)
                self.assertEqual(self.detail(), before)

    def test_rule_crud_validation_and_scheme_ownership(self):
        self.login()
        path = f"/api/admin/schemes/{self.scheme_id}/rules"
        payload = {"definition": {"field": "gender", "operator": "IN", "value": ["Female"], "value_type": "string"},
                   "confidence": "HIGH", "notes": "Confirmed in source"}
        response = self.client.post(path, json=payload)
        self.assertEqual(response.status_code, 201)
        rule_id = response.json()["id"]
        self.assertEqual(response.json()["value"], ["female"])
        self.assertEqual(response.json()["source_metadata"]["notes"], payload["notes"])
        before = self.detail()
        for definition in [{"field": "unknown"}, {"operator": "BAD"}, {"value": []},
                           {"value_type": "boolean"}, {"operator": ">"}, {"value": [None]}]:
            invalid = {**payload, "definition": {**payload["definition"], **definition}}
            self.assertEqual(self.client.put(path + f"/{rule_id}", json=invalid).status_code, 422)
            self.assertEqual(self.client.post(path, json=invalid).status_code, 422)
            self.assertEqual(self.detail(), before)
        for method in ["PUT", "DELETE"]:
            response = self.client.request(method, f"/api/admin/schemes/2/rules/{rule_id}", json=payload)
            self.assertEqual(response.status_code, 404)
        payload["definition"]["value"] = ["male"]
        self.assertEqual(self.client.put(path + f"/{rule_id}", json=payload).status_code, 200)
        results = self.client.post("/api/eligibility", json={"attributes": {"age": 20, "gender": "female"}}).json()
        self.assertEqual(next(r for r in results if r["scheme_id"] == self.scheme_id)["status"], "NOT_ELIGIBLE")
        self.assertEqual(self.client.delete(path + f"/{rule_id}").status_code, 204)
        self.assertEqual(self.client.delete(path + f"/{rule_id}").status_code, 404)
        self.assertEqual(self.detail()["manual_conditions"], before["manual_conditions"])
        self.assertEqual(len(self.detail()["rules"]), 1)

    def test_rule_metadata_edit_preserves_source_keys(self):
        self.login()
        with self.factory.begin() as session:
            scheme = SchemeService(session).get_by_id(self.scheme_id)
            rule = scheme.rules[0]
            rule.source_metadata = {"rule_confidence": "NEEDS_REVIEW", "notes": "Original", "source_basis": "Official table"}
            rule_id = rule.id
        path = f"/api/admin/schemes/{self.scheme_id}/rules/{rule_id}"
        payload = {"definition": {"field": "age", "operator": ">=", "value": 21, "value_type": "integer"},
                   "notes": "Updated"}
        response = self.client.put(path, json=payload)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["source_metadata"], {"rule_confidence": "NEEDS_REVIEW", "notes": "Updated", "source_basis": "Official table"})

    def test_researched_and_archived_catalogue(self):
        from backend.db.import_dataset import import_dataset
        from backend.db.seed import seed_database

        engine, factory = make_database()
        try:
            seed_database(factory)
            import_dataset(factory)
            app.state.session_factory = factory
            self.login()
            active = self.client.get("/api/admin/schemes?is_active=true").json()
            archived = self.client.get("/api/admin/schemes?is_active=false").json()
            self.assertEqual(active["total"], 297)
            self.assertEqual(archived["total"], 67)
            self.assertEqual(self.client.get("/api/admin/schemes?status_confidence=NEEDS_REVIEW").json()["total"], 5)
            self.scheme_id = active["items"][0]["id"]
            before = self.detail()
            self.assertTrue(before["manual_conditions"])
            self.assertTrue(before["official_url"])
            response = self.client.patch(f"/api/admin/schemes/{self.scheme_id}", json={"confidence_notes": "Admin review note"})
            self.assertEqual(response.status_code, 200)
            for field in ["rules", "manual_conditions", "source_metadata", "status_confidence"]:
                self.assertEqual(response.json()[field], before[field])
        finally:
            app.state.session_factory = self.factory
            engine.dispose()
