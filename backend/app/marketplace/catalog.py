"""Fixed vocabularies of the marketplace: categories, condition grades, lot types and regions."""
import re

CATEGORIES: dict[str, str] = {
    "apparel": "Apparel",
    "footwear": "Footwear",
    "electronics": "Electronics",
    "tools": "Tools",
    "appliances": "Appliances",
    "home": "Home & Kitchen",
    "beauty": "Beauty & Personal Care",
    "toys": "Toys",
    "sports": "Sporting Goods",
    "general": "General Merchandise",
}

CONDITIONS: dict[str, str] = {
    "new": "New",
    "like_new": "Like New",
    "customer_return": "Customer Return",
    "salvage": "Salvage",
}

LOT_TYPES: dict[str, str] = {
    "single_pallet": "Single Pallet",
    "multi_pallet": "Multi-Pallet",
    "truckload": "Full Truckload (FTL)",
}

REGIONS: dict[str, str] = {
    "northeast": "Northeast",
    "midwest": "Midwest",
    "south": "South",
    "west_coast": "West",
}

# First digit of a US ZIP code -> region.
_ZIP_REGION = {
    "0": "northeast", "1": "northeast", "2": "south", "3": "south", "4": "midwest", "5": "midwest",
    "6": "midwest", "7": "south", "8": "west_coast", "9": "west_coast",
}
ZIP_RE = re.compile(r"^\d{5}$")


def region_of(zip_code: str | None) -> str | None:
    if not zip_code or not ZIP_RE.match(zip_code):
        return None
    return _ZIP_REGION[zip_code[0]]


def zip3(zip_code: str | None) -> str | None:
    """What buyers see of the dispatch location: the first three digits only."""
    return f"{zip_code[:3]}xx" if zip_code and ZIP_RE.match(zip_code) else None
