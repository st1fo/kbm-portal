"""Chat assistant drafts, uploaded receipts and fuel provenance."""
from alembic import op
import sqlalchemy as sa

revision = 'c5e2a17d9b40'
down_revision = 'b84f152d8362'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('assistant_sessions',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('vehicle_id', sa.Integer(), sa.ForeignKey('vehicles.id'), nullable=False),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('month', sa.Date(), nullable=False),
        sa.Column('status', sa.String(20), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('state', sa.JSON(), nullable=False),
        sa.Column('result', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('vehicle_id', 'user_id', 'month'))
    op.create_index('ix_assistant_sessions_vehicle_id', 'assistant_sessions', ['vehicle_id'])
    op.create_table('assistant_files',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('session_id', sa.String(36), sa.ForeignKey('assistant_sessions.id', ondelete='CASCADE'), nullable=False),
        sa.Column('filename', sa.String(200), nullable=False),
        sa.Column('mime', sa.String(60), nullable=False),
        sa.Column('size', sa.Integer(), nullable=False),
        sa.Column('sha256', sa.String(64), nullable=False),
        sa.Column('data', sa.LargeBinary(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False))
    op.create_index('ix_assistant_files_session_id', 'assistant_files', ['session_id'])
    with op.batch_alter_table('fuel_purchases') as batch:
        batch.alter_column('odo', existing_type=sa.Integer(), nullable=True)
        batch.add_column(sa.Column('draft_id', sa.String(36), nullable=True))
        batch.create_foreign_key('fk_fuel_purchases_draft_id', 'monthly_drafts', ['draft_id'], ['id'])
        batch.create_index('ix_fuel_purchases_draft_id', ['draft_id'])


def downgrade():
    with op.batch_alter_table('fuel_purchases') as batch:
        batch.drop_index('ix_fuel_purchases_draft_id')
        batch.drop_constraint('fk_fuel_purchases_draft_id', type_='foreignkey')
        batch.drop_column('draft_id')
        batch.alter_column('odo', existing_type=sa.Integer(), nullable=False)
    op.drop_table('assistant_files')
    op.drop_table('assistant_sessions')
