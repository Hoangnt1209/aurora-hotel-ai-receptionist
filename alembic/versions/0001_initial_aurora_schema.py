"""Initial Aurora schema with 15 domain tables and pgvector extension.

Revision ID: 0001_initial_aurora_schema
Revises:
Create Date: 2026-11-16 18:10:00

"""
from typing import Sequence, Union

from alembic import op
import pgvector.sqlalchemy
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0001_initial_aurora_schema"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create pgvector extension and all 15 Aurora core tables."""
    op.execute("CREATE EXTENSION IF NOT EXISTS vector;")

    op.create_table(
        "properties",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("code", sa.String(length=10), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("city", sa.String(length=80), nullable=False),
        sa.Column("timezone", sa.String(length=64), nullable=False),
        sa.Column("check_in_time", sa.Time(), nullable=False),
        sa.Column("check_out_time", sa.Time(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_properties")),
        sa.UniqueConstraint("code", name=op.f("uq_properties_code")),
    )

    op.create_table(
        "guests",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("full_name", sa.String(length=150), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("phone_e164", sa.String(length=32), nullable=False),
        sa.Column("preferred_language", sa.String(length=5), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_guests")),
    )
    op.create_index(op.f("ix_guests_email"), "guests", ["email"], unique=False)
    op.create_index(op.f("ix_guests_phone_e164"), "guests", ["phone_e164"], unique=False)

    op.create_table(
        "idempotency_records",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("actor_scope", sa.String(length=64), nullable=False),
        sa.Column("endpoint", sa.String(length=128), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("response_status", sa.Integer(), nullable=False),
        sa.Column(
            "response_body",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_idempotency_records")),
        sa.UniqueConstraint(
            "actor_scope",
            "endpoint",
            "idempotency_key",
            name="uq_idempotency_actor_endpoint_key",
        ),
    )

    op.create_table(
        "audit_logs",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("trace_id", sa.String(length=64), nullable=False),
        sa.Column("actor_type", sa.String(length=32), nullable=False),
        sa.Column("actor_id", sa.String(length=128), nullable=False),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("entity_type", sa.String(length=64), nullable=False),
        sa.Column("entity_id", sa.String(length=64), nullable=False),
        sa.Column(
            "before_state",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "after_state",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_logs")),
    )
    op.create_index("ix_audit_logs_entity", "audit_logs", ["entity_type", "entity_id"], unique=False)
    op.create_index(op.f("ix_audit_logs_trace_id"), "audit_logs", ["trace_id"], unique=False)

    op.create_table(
        "policy_chunks",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("property_code", sa.String(length=16), nullable=False),
        sa.Column("doc_id", sa.String(length=120), nullable=False),
        sa.Column("section_title", sa.String(length=200), nullable=False),
        sa.Column("language", sa.String(length=5), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("embedding", pgvector.sqlalchemy.vector.VECTOR(dim=384), nullable=False),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_policy_chunks")),
        sa.UniqueConstraint(
            "property_code",
            "doc_id",
            "language",
            name="uq_policy_chunks_prop_doc_lang",
        ),
    )
    op.create_index(
        "ix_policy_chunks_prop_lang",
        "policy_chunks",
        ["property_code", "language"],
        unique=False,
    )

    op.create_table(
        "room_types",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("property_id", sa.UUID(), nullable=False),
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("max_adults", sa.Integer(), nullable=False),
        sa.Column("max_children", sa.Integer(), nullable=False),
        sa.Column("total_rooms", sa.Integer(), nullable=False),
        sa.Column("base_rate_vnd", sa.Numeric(precision=12, scale=0), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.CheckConstraint("base_rate_vnd > 0", name=op.f("ck_room_types_base_rate_positive")),
        sa.CheckConstraint(
            "max_adults >= 1", name=op.f("ck_room_types_max_adults_positive")
        ),
        sa.CheckConstraint(
            "max_children >= 0", name=op.f("ck_room_types_max_children_non_negative")
        ),
        sa.CheckConstraint(
            "total_rooms >= 1", name=op.f("ck_room_types_total_rooms_positive")
        ),
        sa.ForeignKeyConstraint(
            ["property_id"],
            ["properties.id"],
            name=op.f("fk_room_types_property_id_properties"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_room_types")),
        sa.UniqueConstraint("property_id", "code", name="uq_room_types_property_code"),
    )

    op.create_table(
        "staff_users",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("full_name", sa.String(length=150), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("property_id", sa.UUID(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "role IN ('RECEPTIONIST', 'MANAGER', 'ADMIN')",
            name=op.f("ck_staff_users_valid_staff_role"),
        ),
        sa.ForeignKeyConstraint(
            ["property_id"],
            ["properties.id"],
            name=op.f("fk_staff_users_property_id_properties"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_staff_users")),
        sa.UniqueConstraint("email", name=op.f("uq_staff_users_email")),
    )

    op.create_table(
        "conversations",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("property_id", sa.UUID(), nullable=True),
        sa.Column("guest_id", sa.UUID(), nullable=True),
        sa.Column("channel", sa.String(length=32), nullable=False),
        sa.Column("language", sa.String(length=5), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("external_session_id", sa.String(length=128), nullable=True),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["guest_id"],
            ["guests.id"],
            name=op.f("fk_conversations_guest_id_guests"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["property_id"],
            ["properties.id"],
            name=op.f("fk_conversations_property_id_properties"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_conversations")),
    )

    op.create_table(
        "rate_plans",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("room_type_id", sa.UUID(), nullable=False),
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("price_multiplier", sa.Numeric(precision=6, scale=4), nullable=False),
        sa.Column("is_refundable", sa.Boolean(), nullable=False),
        sa.Column("free_cancel_hours_before_checkin", sa.Integer(), nullable=False),
        sa.Column("breakfast_included", sa.Boolean(), nullable=False),
        sa.CheckConstraint(
            "free_cancel_hours_before_checkin >= 0",
            name=op.f("ck_rate_plans_free_cancel_hours_non_negative"),
        ),
        sa.CheckConstraint(
            "price_multiplier > 0",
            name=op.f("ck_rate_plans_price_multiplier_positive"),
        ),
        sa.ForeignKeyConstraint(
            ["room_type_id"],
            ["room_types.id"],
            name=op.f("fk_rate_plans_room_type_id_room_types"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_rate_plans")),
        sa.UniqueConstraint("room_type_id", "code", name="uq_rate_plans_room_type_code"),
    )

    op.create_table(
        "room_inventory_daily",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("room_type_id", sa.UUID(), nullable=False),
        sa.Column("stay_date", sa.Date(), nullable=False),
        sa.Column("total_rooms", sa.Integer(), nullable=False),
        sa.Column("reserved_rooms", sa.Integer(), nullable=False),
        sa.Column("blocked_rooms", sa.Integer(), nullable=False),
        sa.Column("price_override_vnd", sa.Numeric(precision=12, scale=0), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "(reserved_rooms + blocked_rooms) <= total_rooms",
            name=op.f("ck_room_inventory_daily_inventory_no_overbooking"),
        ),
        sa.CheckConstraint(
            "blocked_rooms >= 0",
            name=op.f("ck_room_inventory_daily_inventory_blocked_non_negative"),
        ),
        sa.CheckConstraint(
            "reserved_rooms >= 0",
            name=op.f("ck_room_inventory_daily_inventory_reserved_non_negative"),
        ),
        sa.CheckConstraint(
            "total_rooms >= 0",
            name=op.f("ck_room_inventory_daily_inventory_total_non_negative"),
        ),
        sa.ForeignKeyConstraint(
            ["room_type_id"],
            ["room_types.id"],
            name=op.f("fk_room_inventory_daily_room_type_id_room_types"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_room_inventory_daily")),
        sa.UniqueConstraint(
            "room_type_id", "stay_date", name="uq_inventory_room_type_date"
        ),
    )

    op.create_table(
        "conversation_messages",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("conversation_id", sa.UUID(), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("tool_calls", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("trace_id", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.id"],
            name=op.f("fk_conversation_messages_conversation_id_conversations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_conversation_messages")),
    )
    op.create_index(
        op.f("ix_conversation_messages_conversation_id"),
        "conversation_messages",
        ["conversation_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_conversation_messages_trace_id"),
        "conversation_messages",
        ["trace_id"],
        unique=False,
    )

    op.create_table(
        "reservations",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("confirmation_code", sa.String(length=24), nullable=False),
        sa.Column("property_id", sa.UUID(), nullable=False),
        sa.Column("room_type_id", sa.UUID(), nullable=False),
        sa.Column("rate_plan_id", sa.UUID(), nullable=False),
        sa.Column("guest_id", sa.UUID(), nullable=False),
        sa.Column("check_in_date", sa.Date(), nullable=False),
        sa.Column("check_out_date", sa.Date(), nullable=False),
        sa.Column("adults", sa.Integer(), nullable=False),
        sa.Column("children", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("total_amount_vnd", sa.Numeric(precision=14, scale=0), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("special_requests", sa.Text(), nullable=True),
        sa.Column("source_channel", sa.String(length=32), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("adults >= 1", name=op.f("ck_reservations_adults_positive")),
        sa.CheckConstraint(
            "check_out_date > check_in_date",
            name=op.f("ck_reservations_checkout_after_checkin"),
        ),
        sa.CheckConstraint(
            "children >= 0", name=op.f("ck_reservations_children_non_negative")
        ),
        sa.CheckConstraint(
            "status IN ('PENDING_CONFIRMATION', 'CONFIRMED', 'CHECKED_IN', "
            "'CHECKED_OUT', 'CANCELLED', 'NO_SHOW', 'EXPIRED')",
            name=op.f("ck_reservations_valid_reservation_status"),
        ),
        sa.CheckConstraint(
            "total_amount_vnd >= 0",
            name=op.f("ck_reservations_total_amount_non_negative"),
        ),
        sa.ForeignKeyConstraint(
            ["guest_id"],
            ["guests.id"],
            name=op.f("fk_reservations_guest_id_guests"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["property_id"],
            ["properties.id"],
            name=op.f("fk_reservations_property_id_properties"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["rate_plan_id"],
            ["rate_plans.id"],
            name=op.f("fk_reservations_rate_plan_id_rate_plans"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["room_type_id"],
            ["room_types.id"],
            name=op.f("fk_reservations_room_type_id_room_types"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_reservations")),
        sa.UniqueConstraint(
            "confirmation_code", name=op.f("uq_reservations_confirmation_code")
        ),
    )

    op.create_table(
        "reservation_nights",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("reservation_id", sa.UUID(), nullable=False),
        sa.Column("room_type_id", sa.UUID(), nullable=False),
        sa.Column("stay_date", sa.Date(), nullable=False),
        sa.Column("nightly_rate_vnd", sa.Numeric(precision=12, scale=0), nullable=False),
        sa.CheckConstraint(
            "nightly_rate_vnd >= 0",
            name=op.f("ck_reservation_nights_nightly_rate_non_negative"),
        ),
        sa.ForeignKeyConstraint(
            ["reservation_id"],
            ["reservations.id"],
            name=op.f("fk_reservation_nights_reservation_id_reservations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["room_type_id"],
            ["room_types.id"],
            name=op.f("fk_reservation_nights_room_type_id_room_types"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_reservation_nights")),
        sa.UniqueConstraint(
            "reservation_id", "stay_date", name="uq_res_nights_res_date"
        ),
    )

    op.create_table(
        "support_tickets",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("ticket_code", sa.String(length=24), nullable=False),
        sa.Column("property_id", sa.UUID(), nullable=False),
        sa.Column("reservation_id", sa.UUID(), nullable=True),
        sa.Column("conversation_id", sa.UUID(), nullable=True),
        sa.Column("category", sa.String(length=32), nullable=False),
        sa.Column("priority", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "category IN ('HOUSEKEEPING', 'AIRPORT_TRANSFER', 'COMPLAINT', 'SPECIAL_REQUEST')",
            name=op.f("ck_support_tickets_valid_ticket_category"),
        ),
        sa.CheckConstraint(
            "priority IN ('LOW', 'NORMAL', 'HIGH', 'URGENT')",
            name=op.f("ck_support_tickets_valid_ticket_priority"),
        ),
        sa.CheckConstraint(
            "status IN ('OPEN', 'IN_PROGRESS', 'RESOLVED', 'CANCELLED')",
            name=op.f("ck_support_tickets_valid_ticket_status"),
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.id"],
            name=op.f("fk_support_tickets_conversation_id_conversations"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["property_id"],
            ["properties.id"],
            name=op.f("fk_support_tickets_property_id_properties"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["reservation_id"],
            ["reservations.id"],
            name=op.f("fk_support_tickets_reservation_id_reservations"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_support_tickets")),
        sa.UniqueConstraint("ticket_code", name=op.f("uq_support_tickets_ticket_code")),
    )

    op.create_table(
        "escalations",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("escalation_code", sa.String(length=24), nullable=False),
        sa.Column("conversation_id", sa.UUID(), nullable=False),
        sa.Column("property_id", sa.UUID(), nullable=True),
        sa.Column("reservation_id", sa.UUID(), nullable=True),
        sa.Column("assigned_staff_id", sa.UUID(), nullable=True),
        sa.Column("reason_code", sa.String(length=64), nullable=False),
        sa.Column("priority", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column(
            "context_snapshot",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "priority IN ('NORMAL', 'HIGH', 'URGENT')",
            name=op.f("ck_escalations_valid_escalation_priority"),
        ),
        sa.CheckConstraint(
            "status IN ('OPEN', 'ASSIGNED', 'RESOLVED')",
            name=op.f("ck_escalations_valid_escalation_status"),
        ),
        sa.ForeignKeyConstraint(
            ["assigned_staff_id"],
            ["staff_users.id"],
            name=op.f("fk_escalations_assigned_staff_id_staff_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.id"],
            name=op.f("fk_escalations_conversation_id_conversations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["property_id"],
            ["properties.id"],
            name=op.f("fk_escalations_property_id_properties"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["reservation_id"],
            ["reservations.id"],
            name=op.f("fk_escalations_reservation_id_reservations"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_escalations")),
        sa.UniqueConstraint(
            "escalation_code", name=op.f("uq_escalations_escalation_code")
        ),
    )


def downgrade() -> None:
    """Drop all 15 Aurora core tables in reverse dependency order."""
    op.drop_table("escalations")
    op.drop_table("support_tickets")
    op.drop_table("reservation_nights")
    op.drop_table("reservations")
    op.drop_index(op.f("ix_conversation_messages_trace_id"), table_name="conversation_messages")
    op.drop_index(
        op.f("ix_conversation_messages_conversation_id"), table_name="conversation_messages"
    )
    op.drop_table("conversation_messages")
    op.drop_table("room_inventory_daily")
    op.drop_table("rate_plans")
    op.drop_table("conversations")
    op.drop_table("staff_users")
    op.drop_table("room_types")
    op.drop_index("ix_policy_chunks_prop_lang", table_name="policy_chunks")
    op.drop_table("policy_chunks")
    op.drop_index(op.f("ix_audit_logs_trace_id"), table_name="audit_logs")
    op.drop_index("ix_audit_logs_entity", table_name="audit_logs")
    op.drop_table("audit_logs")
    op.drop_table("idempotency_records")
    op.drop_index(op.f("ix_guests_phone_e164"), table_name="guests")
    op.drop_index(op.f("ix_guests_email"), table_name="guests")
    op.drop_table("guests")
    op.drop_table("properties")
