"""The contact scrubber applied to negotiation messages, offer terms and listings."""
import pytest

from app.marketplace.scrub import scrub


@pytest.mark.parametrize(
    ("text", "flag", "gone"),
    [
        ("email me at jane.doe@acme-liquidators.com today", "email", "jane.doe@acme-liquidators.com"),
        ("reach me: jane dot doe at gmail dot com", "email", "gmail"),
        ("jane [at] gmail [dot] com", "email", "gmail"),
        ("call 312-555-0199 after 5", "phone", "555-0199"),
        ("text (312) 555 0199", "phone", "555 0199"),
        ("whatsapp +44 20 7946 0958", "phone", "7946"),
        ("pay at https://paypal.me/palletkings", "payment_link", "paypal.me"),
        ("venmo.com/u/palletkings works", "payment_link", "venmo.com"),
        ("send to $PalletKings on cash app", "payment_handle", "$PalletKings"),
        ("see www.palletkings.com for more", "link", "palletkings.com"),
        ("photos at palletkings.shop/lot-7", "link", "palletkings.shop"),
        ("find us @palletkings_official", "handle", "@palletkings_official"),
    ],
)
def test_contact_details_are_removed_and_reported(text, flag, gone):
    result = scrub(text)
    assert flag in result.flags
    assert gone not in result.text
    assert result.changed and result.warnings


@pytest.mark.parametrize(
    "text",
    [
        "I can do $12,500.00 for the lot, 1,200 units",
        "UPC 885911425308 is the drill kit",
        "ASIN B0CHWRXH8B and model DCD771C2",
        "Can you ship by 2026-12-01? Liftgate needed, 26 pallets",
        "SKU: 4471-0099-1234",
        "885911425308",
    ],
)
def test_product_data_and_prices_are_left_alone(text):
    result = scrub(text)
    assert result.text == text and result.flags == []


def test_off_platform_payment_is_warned_about_but_text_is_kept():
    result = scrub("Can we just use Zelle instead?")
    assert result.flags == ["payment_off_platform"]
    assert result.text == "Can we just use Zelle instead?" and not result.changed


def test_the_senders_company_name_is_hidden():
    result = scrub("This is Pallet Kings Liquidation, we load fast", names=("Pallet Kings Liquidation",))
    assert "Pallet Kings" not in result.text and result.flags == ["company_name"]


def test_several_kinds_are_reported_once_each_in_order():
    result = scrub("a@b.co or c@d.co or 312-555-0101")
    assert result.flags == ["email", "phone"]
    assert result.text.count("[email removed]") == 2
