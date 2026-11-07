"""Žiadosť o absenciu na časť dňa (počet hodín, napr. lekár 2 h). Revision 0005."""
from alembic import op
revision='0005'
down_revision='0004'
branch_labels=None
depends_on=None
def upgrade():
    op.execute('ALTER TABLE leave_requests ADD COLUMN hours DOUBLE PRECISION')
    op.execute('ALTER TABLE leave_requests ADD CONSTRAINT ck_leave_requests_hours CHECK (hours IS NULL OR (hours > 0 AND hours <= 24 AND start_date = end_date))')
def downgrade():
    op.execute('ALTER TABLE leave_requests DROP CONSTRAINT ck_leave_requests_hours')
    op.execute('ALTER TABLE leave_requests DROP COLUMN hours')
