"""Initial schema; frozen PostgreSQL DDL. Revision 0001."""
from alembic import op
revision='0001'
down_revision=None
branch_labels=None
depends_on=None
def upgrade():
    op.execute('\nCREATE TABLE application_settings (\n\tkey VARCHAR(100) NOT NULL, \n\tvalue JSON NOT NULL, \n\tPRIMARY KEY (key)\n)\n\n')
    op.execute('\nCREATE TABLE attendance_types (\n\tid SERIAL NOT NULL, \n\tname VARCHAR(100) NOT NULL, \n\ticon VARCHAR(40) NOT NULL, \n\tcolor VARCHAR(7) NOT NULL, \n\tdescription VARCHAR(500) NOT NULL, \n\trequires_approval BOOLEAN NOT NULL, \n\tactive BOOLEAN NOT NULL, \n\tPRIMARY KEY (id), \n\tUNIQUE (name)\n)\n\n')
    op.execute('\nCREATE TABLE public_holidays (\n\tdate DATE NOT NULL, \n\tname VARCHAR(150) NOT NULL, \n\tPRIMARY KEY (date)\n)\n\n')
    op.execute("\nCREATE TABLE users (\n\tid SERIAL NOT NULL, \n\tname VARCHAR(150) NOT NULL, \n\temail VARCHAR(254) NOT NULL, \n\tpassword_hash VARCHAR(300) NOT NULL, \n\trole VARCHAR(20) NOT NULL, \n\tactive BOOLEAN NOT NULL, \n\tdaily_hours DOUBLE PRECISION NOT NULL, \n\tPRIMARY KEY (id), \n\tCHECK (role IN ('admin','employee')), \n\tCHECK (daily_hours > 0 AND daily_hours <= 24), \n\tUNIQUE (email)\n)\n\n")
    op.execute('\nCREATE TABLE audit_logs (\n\tid SERIAL NOT NULL, \n\tactor_id INTEGER NOT NULL, \n\taction VARCHAR(100) NOT NULL, \n\tdetail VARCHAR(2000) NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (id), \n\tFOREIGN KEY(actor_id) REFERENCES users (id)\n)\n\n')
    op.execute("\nCREATE TABLE leave_requests (\n\tid SERIAL NOT NULL, \n\tuser_id INTEGER NOT NULL, \n\ttype_id INTEGER NOT NULL, \n\tstart_date DATE NOT NULL, \n\tend_date DATE NOT NULL, \n\tnote VARCHAR(2000) NOT NULL, \n\tstatus VARCHAR(20) NOT NULL, \n\treason VARCHAR(2000) NOT NULL, \n\treviewed_by INTEGER, \n\treviewed_at TIMESTAMP WITH TIME ZONE, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (id), \n\tCHECK (end_date >= start_date), \n\tCHECK (status IN ('pending','approved','rejected')), \n\tFOREIGN KEY(user_id) REFERENCES users (id), \n\tFOREIGN KEY(type_id) REFERENCES attendance_types (id), \n\tFOREIGN KEY(reviewed_by) REFERENCES users (id)\n)\n\n")
    op.execute('CREATE INDEX ix_leave_requests_user_id ON leave_requests (user_id)')
    op.execute('\nCREATE TABLE notifications (\n\tid SERIAL NOT NULL, \n\tuser_id INTEGER NOT NULL, \n\tmessage VARCHAR(500) NOT NULL, \n\tread BOOLEAN NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (id), \n\tFOREIGN KEY(user_id) REFERENCES users (id)\n)\n\n')
    op.execute('CREATE INDEX ix_notifications_user_id ON notifications (user_id)')
    op.execute('\nCREATE TABLE work_time_entries (\n\tid SERIAL NOT NULL, \n\tuser_id INTEGER NOT NULL, \n\tdate DATE NOT NULL, \n\tstart VARCHAR(5) NOT NULL, \n\t"end" VARCHAR(5), \n\tbreak_minutes INTEGER NOT NULL, \n\tPRIMARY KEY (id), \n\tUNIQUE (user_id, date), \n\tCHECK (break_minutes >= 0), \n\tFOREIGN KEY(user_id) REFERENCES users (id)\n)\n\n')
    op.execute('CREATE INDEX ix_work_time_entries_user_id ON work_time_entries (user_id)')
    op.execute('\nCREATE TABLE attendance_entries (\n\tid SERIAL NOT NULL, \n\tuser_id INTEGER NOT NULL, \n\tdate DATE NOT NULL, \n\ttype_id INTEGER NOT NULL, \n\tstart VARCHAR(5), \n\t"end" VARCHAR(5), \n\tnote VARCHAR(2000) NOT NULL, \n\trequest_id INTEGER, \n\tPRIMARY KEY (id), \n\tUNIQUE (user_id, date), \n\tFOREIGN KEY(user_id) REFERENCES users (id), \n\tFOREIGN KEY(type_id) REFERENCES attendance_types (id), \n\tFOREIGN KEY(request_id) REFERENCES leave_requests (id)\n)\n\n')
    op.execute('CREATE INDEX ix_attendance_entries_user_id ON attendance_entries (user_id)')
    op.execute('CREATE INDEX ix_attendance_date ON attendance_entries (date)')
def downgrade():
    op.drop_table('attendance_entries')
    op.drop_table('work_time_entries')
    op.drop_table('notifications')
    op.drop_table('leave_requests')
    op.drop_table('audit_logs')
    op.drop_table('users')
    op.drop_table('public_holidays')
    op.drop_table('attendance_types')
    op.drop_table('application_settings')
