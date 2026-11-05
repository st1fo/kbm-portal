"""Add reviewed monthly drafts and trip provenance."""
from alembic import op
import sqlalchemy as sa

revision = 'b84f152d8362'
down_revision = '7f6ab447b1e0'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('monthly_drafts',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('vehicle_id', sa.Integer(), sa.ForeignKey('vehicles.id'), nullable=False),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('month', sa.Date(), nullable=False),
        sa.Column('fingerprint', sa.String(64), nullable=False),
        sa.Column('payload', sa.JSON(), nullable=False),
        sa.Column('result', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False))
    op.create_index('ix_monthly_drafts_vehicle_id', 'monthly_drafts', ['vehicle_id'])
    with op.batch_alter_table('trips') as batch:
        batch.add_column(sa.Column('draft_id', sa.String(36), nullable=True))
        batch.create_foreign_key('fk_trips_draft_id', 'monthly_drafts', ['draft_id'], ['id'])
        batch.create_index('ix_trips_draft_id', ['draft_id'])


def downgrade():
    with op.batch_alter_table('trips') as batch:
        batch.drop_index('ix_trips_draft_id')
        batch.drop_constraint('fk_trips_draft_id', type_='foreignkey')
        batch.drop_column('draft_id')
    op.drop_table('monthly_drafts')
