"""Match purchase names to catalog products and find products missing from the catalog."""

from __future__ import annotations

import copy
import re
import statistics
from collections import defaultdict
from typing import Any

from homeassistant.util import dt as dt_util

# A product not in the catalog must be bought on this many separate days to be offered.
CANDIDATE_MIN_DAYS = 2

_UNITS = (
    r"pcs?|pieces?|packs?|packets?|rolls?|bottles?|cans?|jars?|boxe?s?|bags?|cartons?|tubs?|tubes?|loaf|loaves|"
    r"bunch(?:es)?|heads?|dozens?|units?|servings?|slices?|kgs?|g|gr|grams?|lbs?|pounds?|oz|ounces?|l|liters?|"
    r"litres?|ml|cl"
)
_PARENTHESES = re.compile(r"^(?P<name>.*?)\s*\((?P<qty>\d+)(?:\s+[^()]+)?\)$")
_TRAILING = re.compile(rf"^(?P<name>.*?\S)\s+(?P<qty>\d+)\s*(?:{_UNITS})?\.?$", re.IGNORECASE)


def split_quantity(summary: str) -> tuple[str, int]:
    """Split "Milk (2)", "Milk (4 bottles)", "Milk 2", or "Chuck roast 2 lbs" into name and quantity."""
    text = " ".join(summary.split())
    for pattern in (_PARENTHESES, _TRAILING):
        if (match := pattern.match(text)) and match["name"].strip(" -"):
            return match["name"].strip(" -"), max(1, int(match["qty"]))
    return text, 1


def _singular(word: str) -> str:
    if len(word) <= 3 or word.endswith("ss"):
        return word
    if word.endswith("ies"):
        return word[:-3] + "y"
    if word.endswith(("oes", "shes", "ches", "xes", "zes", "sses")):
        return word[:-2]
    return word[:-1] if word.endswith("s") else word


def match_key(name: str) -> str:
    """Compare names ignoring case, spacing, and simple plurals."""
    return " ".join(_singular(word) for word in name.lower().replace(" - ", " | ").split())


def _variants(types: Any) -> list[str]:
    entries = types.split(",") if isinstance(types, str) else types if isinstance(types, list) else []
    names = (entry.get("name") if isinstance(entry, dict) else entry for entry in entries)
    return [name.strip() for name in names if isinstance(name, str) and name.strip()]


def catalog_index(catalog: dict[str, Any] | None) -> tuple[dict[str, str], list[dict[str, Any]]]:
    """Map match keys to the names the card uses, and list each product's bare title by category."""
    names: dict[str, str] = {}
    fallbacks: dict[str, str] = {}
    products: list[dict[str, Any]] = []
    for category, entries in (catalog or {}).items():
        for entry in entries if isinstance(entries, list) else []:
            title = entry.get("title") if isinstance(entry, dict) else None
            if not isinstance(title, str) or not title.strip():
                continue
            title = title.strip()
            prefix = entry.get("list_prefix") if isinstance(entry.get("list_prefix"), str) else ""

            def full(name: str, prefix: str = prefix) -> str:
                return f"{prefix} - {name}" if prefix else name

            subtitle = entry.get("subtitle") if isinstance(entry.get("subtitle"), str) else ""
            own = [f"{title} - {subtitle}" if subtitle else title]
            own += [f"{title} - {variant}" for variant in _variants(entry.get("types"))]
            for name in own:
                names.setdefault(match_key(full(name)), full(name))
                if prefix:
                    fallbacks.setdefault(match_key(name), full(name))
            # Purchases from before a product had variants carry its bare title.
            fallbacks.setdefault(match_key(full(title)), full(title))
            if prefix:
                fallbacks.setdefault(match_key(title), full(title))
            products.append(
                {"category": category, "title": title, "variants": bool(subtitle or _variants(entry.get("types")))}
            )
    return {**fallbacks, **names}, products


def canonical_purchases(purchases: list[dict[str, Any]], names: dict[str, str]) -> list[dict[str, Any]]:
    """Purchases with quantities split out and names matched to the catalog where possible."""
    result = []
    for purchase in purchases:
        name, quantity = split_quantity(purchase["name"])
        if quantity == 1:
            quantity = purchase.get("quantity", 1)
        result.append({**purchase, "name": names.get(match_key(name), name), "quantity": quantity})
    return result


def _placement(name: str, products: list[dict[str, Any]]) -> dict[str, str] | None:
    """Suggest the existing product this name could be a variant of."""
    by_title: dict[str, dict[str, Any]] = {}
    for product in products:
        entry = by_title.setdefault(match_key(product["title"]), {**product})
        entry["variants"] = entry["variants"] or product["variants"]

    def offer(product: dict[str, Any], variant: str) -> dict[str, str]:
        return {
            "category": product["category"],
            "title": product["title"],
            "variant": variant[:1].upper() + variant[1:],
        }

    if " - " in name:
        head, variant = (part.strip() for part in name.split(" - ", 1))
        if (product := by_title.get(match_key(head))) and variant:
            return offer(product, variant)
    words = name.split()
    # "Mini cucumbers" ends with the product it describes; "Strawberry jam" does not.
    for size in range(len(words) - 1, 0, -1):
        if product := by_title.get(match_key(" ".join(words[-size:]))):
            return offer(product, " ".join(words[:-size]))
    # "Chicken wings" starts with a product that already comes in variants. An exact match is required:
    # "Strawberry jam" uses the word as a modifier, not as the product "Strawberries".
    for size in range(len(words) - 1, 0, -1):
        head = " ".join(words[:size])
        product = by_title.get(match_key(head))
        if product and product["variants"] and head.lower() == product["title"].lower():
            return offer(product, " ".join(words[size:]))
    return None


def add_to_catalog(catalog: dict[str, Any], candidate: dict[str, Any], category: str | None) -> dict[str, Any]:
    """Return the catalog with a candidate added as a new product in category, or as a variant when category is None."""
    data = copy.deepcopy(catalog)
    if category is not None:
        if not isinstance(data.get(category), list):
            raise ValueError(f'The catalog has no "{category}" category.')
        data[category].append({"title": candidate["name"]})
        return data
    place = candidate.get("variant_of")
    entries = data.get(place["category"]) if place else None
    if not isinstance(entries, list):
        raise ValueError(f"{candidate['name']} can't be added as a variant.")
    title = place["title"].lower()
    matches = [
        index
        for index, entry in enumerate(entries)
        if isinstance(entry, dict) and str(entry.get("title", "")).strip().lower() == title
    ]
    if not matches:
        raise ValueError(f"{place['title']} is no longer in the catalog.")
    if all(entries[index].get("subtitle") and not _variants(entries[index].get("types")) for index in matches):
        # Variants of this product are separate entries told apart by subtitle.
        entries.insert(matches[-1] + 1, {"title": entries[matches[0]]["title"], "subtitle": place["variant"]})
        return data
    index = next((index for index in matches if not entries[index].get("subtitle")), matches[0])
    types = entries[index].get("types")
    existing = list(types) if isinstance(types, list) else _variants(types)
    entries[index] = {**entries[index], "types": [*existing, place["variant"]]}
    return data


def find_candidates(
    purchases: list[dict[str, Any]], catalog: dict[str, Any] | None, dismissed: set[str]
) -> list[dict[str, Any]]:
    """Products bought on separate days that the catalog does not have yet, most often bought first."""
    names, products = catalog_index(catalog)
    rows: dict[str, list[tuple[str, int, str]]] = defaultdict(list)
    for purchase in canonical_purchases(purchases, names):
        key = match_key(purchase["name"])
        when = dt_util.parse_datetime(purchase["time"])
        if key in names or key in dismissed or when is None:
            continue
        rows[key].append((dt_util.as_local(when).date().isoformat(), purchase["quantity"], purchase["name"]))
    found = []
    for key, entries in rows.items():
        days = sorted({entry[0] for entry in entries})
        if len(days) < CANDIDATE_MIN_DAYS:
            continue
        name = max(entries)[2]
        found.append(
            {
                "key": key,
                "name": name,
                "times": len(days),
                "last": days[-1],
                "quantity": max(1, round(statistics.median(entry[1] for entry in entries))),
                "variant_of": _placement(name, products),
            }
        )
    found.sort(key=lambda item: (-item["times"], item["name"].lower()))
    return found
