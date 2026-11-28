"""Comparaison de la collection Obole avec ma collection sur le site Numista (Phase 4).

La correspondance se fait par fiche Numista (N#) : quantités totales de part et d'autre. Les objets
Obole sans N# ne peuvent pas être comparés (ils sont comptés à part).

Format des objets Numista (GET /users/{id}/collected_items), d'après le forum Numista, lu sans
supposer qu'un champ est présent : {"id", "quantity", "type": {"id", "title", "category",
"issuer": {"code", "name"}}, "issue": {"year", "gregorian_year", ...}, "grade", ...}.
"""

from dataclasses import dataclass, field

# Grades Numista -> états de la collection (abréviations françaises usuelles), et inverse.
# Numista : g (AB), vg (B), f (TB), vf (TTB), xf (SUP), au (SPL), unc (FDC/NC).
GRADE_TO_CONDITION = {"g": "AB", "vg": "B", "f": "TB", "vf": "TTB", "xf": "SUP", "au": "SPL", "unc": "UNC"}
CONDITION_TO_GRADE = {
    "AB": "g", "B": "vg", "TB": "f", "TTB": "vf", "SUP": "xf", "SPL": "au",
    "UNC": "unc", "FDC": "unc", "NEUF": "unc", "BU": "unc",
    # Abréviations anglaises, au cas où
    "G": "g", "VG": "vg", "F": "f", "VF": "vf", "XF": "xf", "EF": "xf", "AU": "au",
}
CATEGORY_TO_OBJECT_TYPE = {"coin": "coin", "banknote": "banknote", "exonumia": "token"}


def grade_of(condition: str | None) -> str | None:
    if not condition:
        return None
    return CONDITION_TO_GRADE.get(condition.strip().upper().replace(".", ""))


@dataclass
class NumistaEntry:
    """Un objet de ma collection Numista, réduit à ce qu'Obole utilise."""

    type_id: int
    title: str
    quantity: int
    issuer_code: str | None = None
    issuer_name: str | None = None
    category: str | None = None
    year: int | None = None
    grade: str | None = None

    @classmethod
    def parse(cls, raw: dict) -> "NumistaEntry | None":
        numista_type = raw.get("type") if isinstance(raw.get("type"), dict) else {}
        type_id = numista_type.get("id")
        if not isinstance(type_id, int):
            return None
        issuer = numista_type.get("issuer") if isinstance(numista_type.get("issuer"), dict) else {}
        issue = raw.get("issue") if isinstance(raw.get("issue"), dict) else {}
        year = issue.get("gregorian_year", issue.get("year"))
        quantity = raw.get("quantity", 1)
        category = numista_type.get("category")
        if category is None and isinstance(numista_type.get("object_type"), dict):
            category = numista_type["object_type"].get("name")
        return cls(
            type_id=type_id,
            title=str(numista_type.get("title") or f"N# {type_id}"),
            quantity=quantity if isinstance(quantity, int) and quantity > 0 else 1,
            issuer_code=issuer.get("code"),
            issuer_name=issuer.get("name"),
            category=category.lower() if isinstance(category, str) else None,
            year=year if isinstance(year, int) else None,
            grade=raw.get("grade") if isinstance(raw.get("grade"), str) else None,
        )


@dataclass
class OboleItem:
    id: int
    numista_id: int | None
    quantity: int
    title: str
    condition: str | None = None


@dataclass
class TypeComparison:
    numista_id: int
    title: str
    numista_quantity: int
    obole_quantity: int
    obole_item_ids: list[int] = field(default_factory=list)

    @property
    def difference(self) -> int:
        """> 0 : exemplaires en plus dans Obole ; < 0 : en plus sur Numista."""
        return self.obole_quantity - self.numista_quantity


@dataclass
class Comparison:
    only_obole: list[TypeComparison] = field(default_factory=list)
    only_numista: list[TypeComparison] = field(default_factory=list)
    different: list[TypeComparison] = field(default_factory=list)
    same: int = 0  # fiches présentes des deux côtés avec la même quantité
    obole_without_numista: int = 0  # objets Obole sans N# (non comparables)
    numista_entries: int = 0
    unreadable_entries: int = 0  # objets Numista dont le format n'a pas été compris


def compare(obole: list[OboleItem], numista_raw: list[dict]) -> Comparison:
    result = Comparison(numista_entries=len(numista_raw))
    by_type: dict[int, TypeComparison] = {}
    for raw in numista_raw:
        entry = NumistaEntry.parse(raw) if isinstance(raw, dict) else None
        if entry is None:
            result.unreadable_entries += 1
            continue
        row = by_type.setdefault(entry.type_id, TypeComparison(entry.type_id, entry.title, 0, 0))
        row.numista_quantity += entry.quantity
    for item in obole:
        if item.numista_id is None:
            result.obole_without_numista += 1
            continue
        row = by_type.setdefault(item.numista_id, TypeComparison(item.numista_id, item.title, 0, 0))
        row.obole_quantity += item.quantity
        row.obole_item_ids.append(item.id)
    for row in sorted(by_type.values(), key=lambda r: r.title.lower()):
        if row.numista_quantity == 0:
            result.only_obole.append(row)
        elif row.obole_quantity == 0:
            result.only_numista.append(row)
        elif row.difference:
            result.different.append(row)
        else:
            result.same += 1
    return result


def numista_entries_of(numista_raw: list[dict], type_id: int) -> list[NumistaEntry]:
    entries = [NumistaEntry.parse(raw) for raw in numista_raw if isinstance(raw, dict)]
    return [e for e in entries if e is not None and e.type_id == type_id]
