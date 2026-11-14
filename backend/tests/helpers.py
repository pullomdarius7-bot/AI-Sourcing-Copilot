import re
import time
from dataclasses import dataclass

import pyotp

from app import email as mail

PASSWORD = "correct-horse-battery-9"


@dataclass
class Account:
    email: str
    password: str
    access: str
    refresh: str
    user_id: str
    org_id: str

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.access}"}


def token_from_email(to: str) -> str:
    """Latest emailed token for an address."""
    for message in reversed(mail.outbox):
        if message["to"] == to:
            match = re.search(r"token=([A-Za-z0-9_\-]+)", message["body"])
            assert match, message["body"]
            return match.group(1)
    raise AssertionError(f"no email sent to {to}")


_BUYER_ANSWERS = {
    "product_categories": ["power_tools", "consumer_electronics"], "monthly_budget": "5k_25k",
    "lot_sizes": ["pallets", "truckloads"],
}
ONBOARDING_ANSWERS = {
    "solo_buyer": _BUYER_ANSWERS,
    "org_buyer": _BUYER_ANSWERS,
    "supplier": {
        "inventory_types": ["customer_returns", "overstock"], "manifest_format": "excel",
        "dispatch_regions": ["midwest", "south"],
    },
}


async def register_admin(
    client, email="admin@acme.test", org="Acme Corp", password=PASSWORD, account_type="org_buyer", onboard=True
):
    """Register, verify and sign in the first user of a new account. onboard=False stops before the intake form."""
    payload = {"account_type": account_type, "full_name": "Ada Admin", "email": email, "password": password}
    if org is not None:
        payload["organization_name"] = org
    res = await client.post("/auth/register", json=payload)
    assert res.status_code == 201, res.text
    verify = await client.post("/auth/verify-email", json={"token": token_from_email(email)})
    assert verify.status_code == 200, verify.text
    account = await login(client, email, password)
    if onboard:
        await complete_onboarding(client, account, account_type)
    return account


async def complete_onboarding(client, account: "Account", account_type="org_buyer") -> None:
    res = await client.put(
        "/onboarding", json={"answers": ONBOARDING_ANSWERS[account_type]}, headers=account.headers
    )
    assert res.status_code == 200, res.text


async def login(client, email, password=PASSWORD) -> Account:
    res = await client.post("/auth/login", json={"email": email, "password": password})
    assert res.status_code == 200, res.text
    body = res.json()
    me = await client.get("/auth/me", headers={"Authorization": f"Bearer {body['access_token']}"})
    assert me.status_code == 200, me.text
    return Account(
        email=email, password=password, access=body["access_token"], refresh=body["refresh_token"],
        user_id=me.json()["id"], org_id=me.json()["organization_id"],
    )


async def add_member(client, admin: Account, email: str, role: str) -> Account:
    res = await client.post(
        "/invitations", json={"email": email, "role": role}, headers=admin.headers
    )
    assert res.status_code == 201, res.text
    accept = await client.post(
        "/auth/accept-invitation",
        json={"token": token_from_email(email), "full_name": f"{role.title()} User", "password": PASSWORD},
    )
    assert accept.status_code == 201, accept.text
    return await login(client, email)


def totp_code(secret: str, steps_ahead: int = 0) -> str:
    return pyotp.TOTP(secret).at(time.time() + 30 * steps_ahead)
