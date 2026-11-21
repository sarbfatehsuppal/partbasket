"""Candidate ranking for descriptions. The user must confirm the exact part number."""

from collections import Counter
import csv
from difflib import SequenceMatcher
from math import log, sqrt
import re

from planner import ROOT


def tokens(value):
    return re.findall(r"[a-z]+[0-9]+|[0-9]+[a-z]+|[a-z]+|[0-9]+", value.casefold())


class Catalog:
    def __init__(self):
        with (ROOT / "data/catalog.csv").open(encoding="utf-8", newline="") as handle:
            self.rows = list(csv.DictReader(handle))
        documents = [tokens(" ".join(row.values())) for row in self.rows]
        count = Counter(token for doc in documents for token in set(doc))
        self.idf = {token: log((len(documents)+1)/(frequency+1))+1 for token, frequency in count.items()}
        self.vectors = [self.vector(doc) for doc in documents]

    def vector(self, values):
        weights = {t: (1+log(n))*self.idf[t] for t, n in Counter(values).items() if t in self.idf}
        norm = sqrt(sum(weight*weight for weight in weights.values()))
        return {t: weight/norm for t, weight in weights.items()} if norm else {}

    def suggest(self, text, *, baseline=False):
        vector = self.vector(tokens(text))
        ranking = []
        for row, other in zip(self.rows, self.vectors):
            if baseline:
                score = SequenceMatcher(None, text.casefold(), row["description"].casefold()).ratio()
            else:
                score = sum(value*other.get(token, 0) for token, value in vector.items())
            if score > 0:
                ranking.append((score, row))
        ranking.sort(key=lambda item: (-item[0], item[1]["mpn"]))
        return [row for _, row in ranking[:3]]
