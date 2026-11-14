"""Account types and the qualification wizard every new account completes after signing up.

The wizard is defined here, on the server, and sent to the web app as data: the roles, the questions, the
allowed answers and the validation live in one place, and nothing this module does not describe can be stored.
"""
from enum import StrEnum
from typing import Any

from app.errors import ApiError


class AccountType(StrEnum):
    SOLO_BUYER = "solo_buyer"  # one person sourcing for their own resale business
    ORG_BUYER = "org_buyer"  # a procurement team: members, roles, approvals
    SUPPLIER = "supplier"  # a wholesaler or liquidator listing inventory


ROLES: list[dict[str, str]] = [
    {"value": AccountType.SOLO_BUYER.value, "label": "Solo Buyer / Reseller",
     "description": "Sourcing pallets/truckloads for resale."},
    {"value": AccountType.ORG_BUYER.value, "label": "Purchasing Team / Enterprise",
     "description": "Corporate procurement or multi-member team."},
    {"value": AccountType.SUPPLIER.value, "label": "Inventory Supplier / Liquidator",
     "description": "Selling wholesale lots, returns, or overstock."},
]


def _options(*pairs: tuple[str, str]) -> list[dict[str, str]]:
    return [{"value": value, "label": label} for value, label in pairs]


def _field(key: str, label: str, kind: str, options: list[dict[str, str]], help_text: str = "") -> dict[str, Any]:
    return {"key": key, "label": label, "kind": kind, "options": options, "help": help_text}


_BUYER_FORM = [
    _field("product_categories", "What do you buy?", "multiselect", _options(
        ("luxury_apparel", "Luxury Apparel"),
        ("footwear", "Footwear"),
        ("consumer_electronics", "Consumer Electronics"),
        ("power_tools", "Power Tools"),
        ("small_appliances", "Small Appliances"),
        ("beauty_personal_care", "Beauty & Personal Care"),
        ("general_merchandise", "General Merchandise"),
    ), "Choose every category you source."),
    _field("monthly_budget", "Monthly purchasing budget", "select", _options(
        ("under_5k", "< $5,000"),
        ("5k_25k", "$5,000 - $25,000"),
        ("25k_100k", "$25,000 - $100,000"),
        ("over_100k", "$100,000+"),
    )),
    _field("lot_sizes", "Preferred lot sizes", "multiselect", _options(
        ("box_lots", "Box lots"),
        ("pallets", "Pallets"),
        ("truckloads", "Full Truckloads (FTL)"),
    ), "Choose every size you buy."),
]

FORMS: dict[AccountType, list[dict[str, Any]]] = {
    AccountType.SOLO_BUYER: _BUYER_FORM,
    AccountType.ORG_BUYER: _BUYER_FORM,
    AccountType.SUPPLIER: [
        _field("inventory_types", "Primary inventory types", "multiselect", _options(
            ("customer_returns", "Customer Returns"),
            ("shelf_pulls", "Shelf Pulls"),
            ("overstock", "Overstock"),
            ("salvage", "Salvage"),
        ), "Choose every type you sell."),
        _field("manifest_format", "Typical manifest format", "select", _options(
            ("csv", "CSV"),
            ("excel", "Excel"),
            ("scanned_pdf", "Scanned PDF"),
            ("api_feed", "Real-Time API feed"),
        )),
        _field("dispatch_regions", "Warehouse dispatch regions", "multiselect", _options(
            ("northeast", "Northeast"),
            ("midwest", "Midwest"),
            ("south", "South"),
            ("west_coast", "West Coast"),
        ), "Choose every region you ship from."),
    ],
}


def form_for(account_type: str) -> list[dict[str, Any]]:
    return FORMS[AccountType(account_type)]


def all_forms() -> dict[str, list[dict[str, Any]]]:
    return {account_type.value: fields for account_type, fields in FORMS.items()}


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
