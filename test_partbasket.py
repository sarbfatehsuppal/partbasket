from itertools import product
from random import Random
import unittest
from unittest.mock import patch

from app import app
from planner import Offer, Part, money, optimize
from suppliers import DigiKey, Mouser


class PartBasketTests(unittest.TestCase):
    def test_order_comparison(self):
        client = app.test_client()
        with client.get("/") as page:
            self.assertEqual(page.status_code, 200)
        bom = client.get("/api/demo").json["bom"]
        shipping = {"Northstar": 8, "CircuitHouse": 10}
        response = client.post("/api/plan", json={"bom": bom, "boards": 3, "shipping": shipping})
        self.assertEqual(response.status_code, 200, response.json)
        plans = response.json["plans"]
        self.assertEqual(plans["optimized"]["shipping"], "8.00")
        self.assertEqual(plans["per_part"]["shipping"], "18.00")
        self.assertGreater(float(response.json["savings"]), 0)
        print({name: plan["total"] if plan else None for name, plan in plans.items()})

    def test_missing_part(self):
        response = app.test_client().post("/api/plan", json={
            "bom": "MPN,Quantity\nUNKNOWN,1", "shipping": {"Northstar": 8, "CircuitHouse": 10},
        })
        self.assertEqual(response.json["unavailable"], ["UNKNOWN"])
        self.assertEqual(response.json["plans"], {})

    def test_description_query(self):
        response = app.test_client().post("/api/suggest", json={"description": "SOIC dual amp LM358DR"})
        self.assertEqual(response.json["candidates"][0]["mpn"], "LM358DR")

    def test_exact_solver_against_exhaustive_cart_search(self):
        rng = Random(12)
        for _ in range(60):
            names = ["a", "b", "c"]
            shipping = {name: rng.randint(0, 8) * 100000 for name in names}
            parts = [Part(str(i), rng.randint(1, 7)) for i in range(3)]
            offers = [
                Offer(part.mpn, name, f"{part.mpn}{name}", rng.randint(7, 15),
                      rng.randint(1, 5), rng.randint(1, 3),
                      {1: rng.randint(2, 9)*10000, 5: rng.randint(1, 9)*10000, 9: rng.randint(1, 9)*10000})
                for part in parts for name in names
            ]
            choices = []
            for part in parts:
                options = []
                for offer in offers:
                    if offer.mpn != part.mpn:
                        continue
                    for units in range(max(part.quantity, offer.minimum), offer.stock+1):
                        if units % offer.multiple == 0:
                            tier = max(k for k in offer.tiers if k <= units)
                            options.append((offer.supplier, units*offer.tiers[tier]))
                choices.append(options)
            result = optimize(parts, offers, shipping)
            if all(choices):
                expected = min(sum(cost for _, cost in cart) +
                               sum(shipping[s] for s in {s for s, _ in cart})
                               for cart in product(*choices))
                self.assertEqual(result["plans"]["optimized"]["total"], money(expected))
            else:
                self.assertTrue(result["unavailable"])

    def test_official_response_shapes(self):
        part = Part("ABC-1", 5, "Example")
        digikey = {"SettingsUsed": {"SearchLocale": {"Currency": "CAD"}}, "ProductPricings": [
            {"ManufacturerProductNumber": "ABC-1", "Manufacturer": {"Name": "Example"},
             "ProductVariations": [{"DigiKeyProductNumber": "DK-1", "QuantityAvailableforPackageType": 100,
                                    "MinimumOrderQuantity": 1,
                                    "StandardPricing": [{"BreakQuantity": 1, "UnitPrice": 0.5}]}]}]}
        mouser = {"SearchResults": {"Parts": [
            {"ManufacturerPartNumber": "ABC-1", "Manufacturer": "Example", "MouserPartNumber": "M-1",
             "AvailabilityInStock": "250", "Min": "5", "Mult": "5",
             "PriceBreaks": [{"Quantity": 1, "Price": "$0.50", "Currency": "CAD"}]}]}}
        with patch("suppliers.fetch_json", return_value=digikey), patch.object(DigiKey, "token", return_value="fixture"):
            self.assertEqual(DigiKey("fixture", "fixture").find(part)[0].tiers, {1: 500000})
        with patch("suppliers.fetch_json", return_value=mouser):
            self.assertEqual(Mouser("fixture").find(part)[0].multiple, 5)


if __name__ == "__main__":
    unittest.main()
