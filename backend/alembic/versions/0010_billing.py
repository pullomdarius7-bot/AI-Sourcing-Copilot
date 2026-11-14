"""Stripe subscriptions, metered credit transactions and the legacy trial.

- credit_ledgers becomes credit_transactions and gains what metering needs: the action charged for, the id of
  the thing it was charged for (a document, an analysis run, an assistant answer) and a state. A charge is
  written as 'held' before the model is called and becomes 'settled' when the result is stored or 'refunded'
  when the call fails. Amounts stay immutable: the application may update the state column and nothing else.
- organizations gain the Stripe customer and subscription, the plan, the subscription status and period end.
- stripe_events remembers processed webhook events so a redelivery is applied once.
- Accounts that existed before trials were introduced start a standard trial now, so enforcing credits does
  not lock them out.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None

APP_ROLE = "copilot_app"
LEGACY_TRIAL_CREDITS = 10
LEGACY_TRIAL_DAYS = 7


def upgrade() -> None:
    op.rename_table("credit_ledgers", "credit_transactions")
    op.execute("ALTER INDEX ix_credit_ledgers_org_id_id RENAME TO ix_credit_transactions_org_id_id")
    op.execute("ALTER INDEX uq_credit_ledgers_org_reference RENAME TO uq_credit_transactions_org_reference")
    op.add_column("credit_transactions", sa.Column("action", sa.String(40)))
    op.add_column("credit_transactions", sa.Column("subject_id", pg.UUID(as_uuid=True)))
    op.add_column(
        "credit_transactions", sa.Column("state", sa.String(10), nullable=False, server_default="settled")
    )
    op.create_check_constraint(
        "ck_credit_transactions_state", "credit_transactions", "state IN ('held','settled','refunded')"
    )
    op.create_index(
        "ix_credit_transactions_subject", "credit_transactions", ["organization_id", "action", "subject_id"]
    )
    op.execute(f"GRANT UPDATE (state) ON credit_transactions TO {APP_ROLE}")

    op.add_column("organizations", sa.Column("stripe_customer_id", sa.String(64), unique=True))
    op.add_column("organizations", sa.Column("stripe_subscription_id", sa.String(64)))
    op.add_column("organizations", sa.Column("plan", sa.String(40)))
    op.add_column(
        "organizations", sa.Column("subscription_status", sa.String(12), nullable=False, server_default="none")
    )
    op.create_check_constraint(
        "ck_organizations_subscription_status", "organizations",
        "subscription_status IN ('none','trialing','active','past_due','cancelled')",
    )
    op.add_column("organizations", sa.Column("current_period_end", sa.DateTime(timezone=True)))

    op.create_table(
        "stripe_events",
        sa.Column("id", sa.String(255), primary_key=True),
        sa.Column("type", sa.String(80), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.execute(f"GRANT SELECT, INSERT ON stripe_events TO {APP_ROLE}")

    # Accounts created before trials existed: start the standard trial today.
    op.execute("SELECT set_config('app.system', 'on', true)")
    op.execute(
        f"""
        WITH legacy AS (
            UPDATE organizations
               SET credit_balance = credit_balance + {LEGACY_TRIAL_CREDITS},
                   trial_ends_at = now() + interval '{LEGACY_TRIAL_DAYS} days'
             WHERE onboarding_completed AND trial_ends_at IS NULL
         RETURNING id, credit_balance
        )
        INSERT INTO credit_transactions (organization_id, kind, delta, balance_after, reference, note)
        SELECT id, 'trial_grant', {LEGACY_TRIAL_CREDITS}, credit_balance, 'trial', 'Trial for an existing account'
          FROM legacy
        """
    )


def downgrade() -> None:
    op.drop_table("stripe_events")
    op.drop_column("organizations", "current_period_end")
    op.drop_constraint("ck_organizations_subscription_status", "organizations", type_="check")
    op.drop_column("organizations", "subscription_status")
    op.drop_column("organizations", "plan")
    op.drop_column("organizations", "stripe_subscription_id")
    op.drop_column("organizations", "stripe_customer_id")

    op.execute(f"REVOKE UPDATE (state) ON credit_transactions FROM {APP_ROLE}")
    op.drop_index("ix_credit_transactions_subject", table_name="credit_transactions")
    op.drop_constraint("ck_credit_transactions_state", "credit_transactions", type_="check")
    op.drop_column("credit_transactions", "state")
    op.drop_column("credit_transactions", "subject_id")
    op.drop_column("credit_transactions", "action")
    op.execute("ALTER INDEX uq_credit_transactions_org_reference RENAME TO uq_credit_ledgers_org_reference")
    op.execute("ALTER INDEX ix_credit_transactions_org_id_id RENAME TO ix_credit_ledgers_org_id_id")
    op.rename_table("credit_transactions", "credit_ledgers")
