"""Imported fuel-card invoices (FleetCor), one row per invoice number."""
from alembic import op
import sqlalchemy as sa

revision = 'd8f3a61c2e07'
down_revision = 'c5e2a17d9b40'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('fuel_invoices',
        sa.Column('number', sa.String(40), primary_key=True),
        sa.Column('supplier', sa.String(40), nullable=False),
        sa.Column('issued', sa.Date(), nullable=True),
        sa.Column('total', sa.Numeric(24, 10), nullable=False),
        sa.Column('written', sa.Integer(), nullable=False),
        sa.Column('skipped', sa.Integer(), nullable=False),
        sa.Column('imported_by', sa.String(150), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False))


def downgrade():
    op.drop_table('fuel_invoices')
