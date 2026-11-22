from decimal import Decimal
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from app import app
from mcp_server import compare_boms, get_sample_order
from workspace import compare_orders


class WorkspaceTests(unittest.TestCase):
    def test_compare_rejects_invalid_json_before_saving(self):
        client = app.test_client()
        example = client.get("/api/workspace/example").json
        with patch("app.history.save") as save:
            response = client.post("/api/workspace/compare", json={**example, "save": "true"})
            self.assertEqual(response.status_code, 400)
            self.assertEqual(response.json["details"][0]["loc"], ["save"])
            self.assertNotIn("input", response.json["details"][0])
            save.assert_not_called()

    def test_compare_accepts_legacy_board_counts_and_numeric_shipping(self):
        client = app.test_client()
        project = {"name": "Timer", "bom": "mpn,quantity\nNE555P,1"}
        response = client.post("/api/workspace/compare", json={
            "projects": [project, {**project, "name": "Spare timer", "boards": "02"}],
            "shipping": {"Northstar": 8, "CircuitHouse": 10.5},
        })
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual([item["boards"] for item in response.json["projects"]], [1, 2])

    def test_allocations_and_history(self):
        client = app.test_client()
        example = client.get("/api/workspace/example").json
        with TemporaryDirectory() as storage, patch.dict(os.environ, {
            "PARTBASKET_DB": str(Path(storage) / "orders.db"),
        }):
            response = client.post("/api/workspace/compare", json={**example, "mode": "sample", "save": True})
            self.assertEqual(response.status_code, 200, response.json)
            plan = response.json
            self.assertEqual(plan["plans"]["optimized"]["total"], "24.43")
            self.assertEqual(plan["plans"]["per_part"]["total"], "33.22")
            self.assertEqual(sum(Decimal(p["total"]) for p in plan["projects"]), Decimal("24.43"))
            identifier = plan["saved_id"]
            path = f"/api/workspace/plans/{identifier}"
            self.assertEqual(client.get("/api/workspace/plans").json["orders"][0]["id"], identifier)
            self.assertEqual(client.get(path).json["saved_id"], identifier)
            self.assertEqual(client.delete(path).status_code, 204)
            self.assertEqual(client.get(path).status_code, 404)

    def test_subcent_allocations_reconcile(self):
        projects = [{"name": "Board A", "boards": 1, "bom": "mpn,quantity\nA1,1"},
                    {"name": "Board B", "boards": 1, "bom": "mpn,quantity\nB1,1"}]
        quotes = ("mpn,supplier,sku,stock,minimum,multiple,threshold,unit_cad\n"
                  "A1,Vendor,A1-stock,10,1,1,1,0.014\nB1,Vendor,B1-stock,10,1,1,1,0.014")
        result = compare_orders(projects, "quotes", quotes, {"Vendor": ".01"})
        self.assertEqual(result["plans"]["optimized"]["total"], "0.04")
        self.assertEqual(sum(Decimal(p["total"]) for p in result["projects"]), Decimal("0.04"))
        self.assertEqual(sum(Decimal(r["cost"]) for r in result["suppliers"][0]["rows"]), Decimal("0.03"))

    def test_shortfall_keeps_project_context(self):
        example = [{"name":"Fixture A", "boards":2, "bom":"mpn,quantity\nUNQUOTED,3"},
                   {"name":"Fixture B", "boards":1, "bom":"mpn,quantity\nUNQUOTED,1"}]
        result = compare_orders(example, "sample", None, None)
        self.assertEqual(result["unavailable"], [{"mpn":"UNQUOTED", "needed":7, "projects":["Fixture A","Fixture B"]}])

    def test_disallowed_origin_and_workspace_password(self):
        client = app.test_client()
        response = client.post("/api/workspace/compare", json={}, headers={"Origin": "https://example.test"})
        self.assertEqual(response.status_code, 403)
        with patch.dict(os.environ, {"PARTBASKET_WEB_PASSWORD":"fixture-pass"}):
            self.assertEqual(client.get("/api/workspace/plans").status_code, 401)


class PluginTests(unittest.IsolatedAsyncioTestCase):
    async def test_tool_returns_same_plan_without_history(self):
        example = get_sample_order()
        comparison = await compare_boms(example.projects, "sample")
        self.assertEqual(comparison.plans["optimized"].total, "24.43")
        self.assertEqual([item.total for item in comparison.projects], ["15.12", "9.31"])
        self.assertEqual(len(comparison.optimized_lines), 5)
        self.assertNotIn("saved_id", comparison.model_dump())
