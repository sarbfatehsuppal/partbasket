---
name: parts-order
description: Compare electronic parts orders, combine project BOMs, and divide order costs using PartBasket.
---

Ask for each project name, number of boards, exact manufacturer part numbers, and quantity per board. If the user provided a file, use the exact extracted CSV only when the file is available and the headers and values are clear. Otherwise ask for the relevant rows.

Use sample quotes only when the user requests a demo. Use the quote lookup tool only if it is available and the user requests live prices. Otherwise use CAD quotes the user supplies. Keep each source and timestamp. Obtain the shipping estimate for each quoted supplier. Show any unquoted or unfillable parts. Never invent parts, substitute parts, invent price or shipping values, or place orders. The result is a price estimate under flat shipping with one supplier per part.
