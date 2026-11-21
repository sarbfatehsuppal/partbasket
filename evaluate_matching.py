from matching import Catalog

# These search phrases are separate from the catalog descriptions.
examples = {
    "dip timer 555 through hole": "NE555P",
    "soic smd 555 timer": "NE555DR",
    "dual opamp through hole lm358p": "LM358P",
    "soic dual amplifier lm358dr": "LM358DR",
    "axial do35 high speed diode": "1N4148",
    "diodes sod 123 switching smd": "1N4148W",
    "0603 yageo 10k resistance": "RC0603FR-0710KL",
    "chip 0603 resistor 1k": "RC0603FR-071KL",
    "ceramic 100nf x7r samsung": "CL10B104KB8NNNC",
    "50v ceramic 10nf capacitor": "CL10B103KB8NNNC",
}
catalog = Catalog()
for label, baseline in (("Fuzzy text baseline", True), ("TF-IDF ranking", False)):
    hits = [next((i for i, row in enumerate(catalog.suggest(query, baseline=baseline), 1)
                  if row["mpn"] == mpn), 0) for query, mpn in examples.items()]
    print(f"{label}: top 1 = {hits.count(1)}/{len(hits)}, top 3 = {sum(bool(i) for i in hits)}/{len(hits)}")
print("Small demo fixture. These scores do not estimate accuracy on real BOMs.")
