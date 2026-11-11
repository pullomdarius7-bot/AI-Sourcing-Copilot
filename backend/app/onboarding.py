"""Account types and the intake (profile qualification) form each one answers after verifying its email.

The form is defined here, on the server, and sent to the web app as data: the questions, the allowed answers
and the validation live in one place, and the client cannot store anything this module does not describe.
"""
from enum import StrEnum
from typing import Any

from app.errors import ApiError


class AccountType(StrEnum):
    SOLO_BUYER = "solo_buyer"  # one person sourcing for their own resale business
    ORG_BUYER = "org_buyer"  # a procurement team: members, roles, approvals
    SUPPLIER = "supplier"  # a wholesaler or liquidator listing inventory


def _options(*pairs: tuple[str, str]) -> list[dict[str, str]]:
    return [{"value": value, "label": label} for value, label in pairs]


PRODUCT_CATEGORIES = _options(
    ("apparel", "Apparel & footwear"),
    ("electronics", "Electronics"),
    ("tools", "Tools & hardware"),
    ("home_goods", "Home goods"),
    ("appliances", "Appliances"),
    ("furniture", "Furniture"),
    ("beauty", "Health & beauty"),
    ("toys", "Toys & baby"),
    ("sporting_goods", "Sporting goods"),
    ("automotive", "Automotive"),
    ("grocery", "Grocery & consumables"),
    ("general_merchandise", "General merchandise"),
    ("other", "Other"),
)

_BUDGET = _options(
    ("under_1k", "Under $1,000"),
    ("1k_5k", "$1,000 - $5,000"),
    ("5k_25k", "$5,000 - $25,000"),
    ("25k_100k", "$25,000 - $100,000"),
    ("over_100k", "Over $100,000"),
)


def _field(key: str, label: str, kind: str, options: list[dict[str, str]], help_text: str = "") -> dict[str, Any]:
    return {"key": key, "label": label, "kind": kind, "options": options, "help": help_text}


FORMS: dict[AccountType, list[dict[str, Any]]] = {
    AccountType.SOLO_BUYER: [
        _field("business_type", "How do you sell?", "select", _options(
            ("online_marketplace", "Online marketplaces (eBay, Amazon, Poshmark...)"),
            ("own_store", "My own online store"),
            ("retail_store", "Physical store or bin store"),
            ("flea_market", "Flea markets and local sales"),
            ("wholesale", "I resell to other businesses"),
            ("getting_started", "Just getting started"),
            ("other", "Other"),
        )),
        _field("monthly_sourcing_budget", "Monthly sourcing budget", "select", _BUDGET),
        _field("product_categories", "Product categories you buy", "multiselect", PRODUCT_CATEGORIES,
               "Choose every category that applies."),
    ],
    AccountType.ORG_BUYER: [
        _field("business_type", "Type of business", "select", _options(
            ("retailer", "Retailer"),
            ("wholesaler", "Wholesaler"),
            ("distributor", "Distributor"),
            ("exporter", "Exporter"),
            ("manufacturer", "Manufacturer"),
            ("other", "Other"),
        )),
        _field("team_size", "People who will source with this account", "select", _options(
            ("2_5", "2 - 5"),
            ("6_20", "6 - 20"),
            ("21_100", "21 - 100"),
            ("over_100", "More than 100"),
        )),
        _field("monthly_sourcing_budget", "Monthly sourcing budget", "select", _BUDGET),
        _field("product_categories", "Product categories you buy", "multiselect", PRODUCT_CATEGORIES,
               "Choose every category that applies."),
    ],
    AccountType.SUPPLIER: [
        _field("business_type", "Type of business", "select", _options(
            ("liquidator", "Liquidator"),
            ("wholesaler", "Wholesaler"),
            ("retailer_overstock", "Retailer selling overstock or returns"),
            ("manufacturer", "Manufacturer"),
            ("distributor", "Distributor"),
            ("returns_processor", "Returns processor / 3PL"),
            ("other", "Other"),
        )),
        _field("monthly_inventory_volume", "Inventory you move each month", "select", _options(
            ("under_5_pallets", "Fewer than 5 pallets"),
            ("5_25_pallets", "5 - 25 pallets"),
            ("1_5_truckloads", "1 - 5 truckloads"),
            ("over_5_truckloads", "More than 5 truckloads"),
        )),
        _field("product_categories", "Product categories you sell", "multiselect", PRODUCT_CATEGORIES,
               "Choose every category that applies."),
    ],
}


def form_for(account_type: str) -> list[dict[str, Any]]:
    return FORMS[AccountType(account_type)]


def _invalid(field: str, message: str) -> ApiError:
    return ApiError(422, "validation_error", f"{field}: {message}")


def validate_answers(account_type: str, answers: dict[str, Any]) -> dict[str, Any]:
    """Return the cleaned profile for this account type, or raise a 422 naming the first bad field."""
    fields = form_for(account_type)
    known = {f["key"] for f in fields}
    extra = sorted(set(answers) - known)
    if extra:
        raise _invalid(extra[0], "is not part of this form")
    profile: dict[str, Any] = {}
    for field in fields:
        key, allowed = field["key"], {o["value"] for o in field["options"]}
        value = answers.get(key)
        if field["kind"] == "multiselect":
            if not isinstance(value, list) or not value:
                raise _invalid(key, "choose at least one option")
            if any(not isinstance(v, str) or v not in allowed for v in value):
                raise _invalid(key, "contains an option that does not exist")
            profile[key] = sorted(set(value))
        else:
            if not isinstance(value, str) or value not in allowed:
                raise _invalid(key, "choose one of the listed options")
            profile[key] = value
    return profile
