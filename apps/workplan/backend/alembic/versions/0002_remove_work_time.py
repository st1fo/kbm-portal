"""Remove work-time tracking; drop work_time_entries. Revision 0002."""
from alembic import op
revision='0002'
down_revision='0001'
branch_labels=None
depends_on=None
def upgrade():
    op.execute('DROP INDEX IF EXISTS ix_work_time_entries_user_id')
    op.execute('DROP TABLE IF EXISTS work_time_entries')
def downgrade():
    op.execute('\nCREATE TABLE work_time_entries (\n\tid SERIAL NOT NULL, \n\tuser_id INTEGER NOT NULL, \n\tdate DATE NOT NULL, \n\tstart VARCHAR(5) NOT NULL, \n\t"end" VARCHAR(5), \n\tbreak_minutes INTEGER NOT NULL, \n\tPRIMARY KEY (id), \n\tUNIQUE (user_id, date), \n\tCHECK (break_minutes >= 0), \n\tFOREIGN KEY(user_id) REFERENCES users (id)\n)\n\n')
    op.execute('CREATE INDEX ix_work_time_entries_user_id ON work_time_entries (user_id)')
