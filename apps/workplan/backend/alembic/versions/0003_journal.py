"""Pracovný denník: úseky dochádzky, zákazníci, projekty, úlohy, aktivity, to-do; frozen PostgreSQL DDL. Revision 0003."""
from alembic import op
revision='0003'
down_revision='0002'
branch_labels=None
depends_on=None
def upgrade():
    # Kategórie plánu, ktoré sa rátajú ako pracovný deň (očakávané hodiny v Denníku).
    op.execute('ALTER TABLE attendance_types ADD COLUMN counts_as_work BOOLEAN NOT NULL DEFAULT false')
    op.execute("UPDATE attendance_types SET counts_as_work = true WHERE icon IN ('Building2','House','Plane','GraduationCap')")
    op.execute('\nCREATE TABLE customers (\n\tid SERIAL NOT NULL, \n\tname VARCHAR(150) NOT NULL, \n\tname_key VARCHAR(150) NOT NULL, \n\tactive BOOLEAN NOT NULL, \n\tcreated_by INTEGER, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (id), \n\tUNIQUE (name_key), \n\tFOREIGN KEY(created_by) REFERENCES users (id)\n)\n\n')
    op.execute('\nCREATE TABLE projects (\n\tid SERIAL NOT NULL, \n\tname VARCHAR(150) NOT NULL, \n\tname_key VARCHAR(150) NOT NULL, \n\tcustomer_id INTEGER, \n\tactive BOOLEAN NOT NULL, \n\tcreated_by INTEGER, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (id), \n\tFOREIGN KEY(customer_id) REFERENCES customers (id), \n\tFOREIGN KEY(created_by) REFERENCES users (id)\n)\n\n')
    op.execute('CREATE UNIQUE INDEX ux_projects_key ON projects (name_key, coalesce(customer_id, 0))')
    op.execute('\nCREATE TABLE tasks (\n\tid SERIAL NOT NULL, \n\tname VARCHAR(200) NOT NULL, \n\tproject_id INTEGER, \n\tcustomer_id INTEGER, \n\tdone BOOLEAN NOT NULL, \n\tactive BOOLEAN NOT NULL, \n\tcreated_by INTEGER, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT ck_tasks_project_or_customer CHECK (project_id IS NULL OR customer_id IS NULL), \n\tFOREIGN KEY(project_id) REFERENCES projects (id), \n\tFOREIGN KEY(customer_id) REFERENCES customers (id), \n\tFOREIGN KEY(created_by) REFERENCES users (id)\n)\n\n')
    op.execute('CREATE INDEX ix_tasks_project_id ON tasks (project_id)')
    op.execute('\nCREATE TABLE work_days (\n\tid SERIAL NOT NULL, \n\tuser_id INTEGER NOT NULL, \n\tdate DATE NOT NULL, \n\tdone BOOLEAN NOT NULL, \n\tupdated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (id), \n\tUNIQUE (user_id, date), \n\tFOREIGN KEY(user_id) REFERENCES users (id)\n)\n\n')
    op.execute('\nCREATE TABLE work_segments (\n\tid SERIAL NOT NULL, \n\twork_day_id INTEGER NOT NULL, \n\tposition SMALLINT NOT NULL, \n\tstart VARCHAR(5) NOT NULL, \n\t"end" VARCHAR(5), \n\tstart_next_day BOOLEAN NOT NULL, \n\tend_next_day BOOLEAN NOT NULL, \n\tPRIMARY KEY (id), \n\tUNIQUE (work_day_id, position), \n\tFOREIGN KEY(work_day_id) REFERENCES work_days (id) ON DELETE CASCADE\n)\n\n')
    op.execute('\nCREATE TABLE activities (\n\tid SERIAL NOT NULL, \n\tuser_id INTEGER NOT NULL, \n\ttask_id INTEGER, \n\tstart_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tend_at TIMESTAMP WITH TIME ZONE, \n\tminutes INTEGER, \n\ttext VARCHAR(5000) NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tupdated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT ck_activities_minutes CHECK (minutes IS NULL OR minutes >= 0), \n\tCONSTRAINT ck_activities_span CHECK (end_at IS NULL OR end_at > start_at), \n\tFOREIGN KEY(user_id) REFERENCES users (id), \n\tFOREIGN KEY(task_id) REFERENCES tasks (id)\n)\n\n')
    op.execute('CREATE INDEX ix_activities_task_id ON activities (task_id)')
    op.execute('CREATE INDEX ix_activities_user_start ON activities (user_id, start_at)')
    op.execute('\nCREATE TABLE todos (\n\tid SERIAL NOT NULL, \n\tuser_id INTEGER NOT NULL, \n\ttext VARCHAR(500) NOT NULL, \n\tdue DATE, \n\tpriority SMALLINT NOT NULL, \n\tproject_id INTEGER, \n\tcustomer_id INTEGER, \n\tdone_at TIMESTAMP WITH TIME ZONE, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT ck_todos_priority CHECK (priority >= 0 AND priority <= 2), \n\tCONSTRAINT ck_todos_project_or_customer CHECK (project_id IS NULL OR customer_id IS NULL), \n\tFOREIGN KEY(user_id) REFERENCES users (id), \n\tFOREIGN KEY(project_id) REFERENCES projects (id), \n\tFOREIGN KEY(customer_id) REFERENCES customers (id)\n)\n\n')
    op.execute('CREATE INDEX ix_todos_user_id ON todos (user_id)')
    op.execute("\nCREATE TABLE work_punches (\n\tid SERIAL NOT NULL, \n\tuser_id INTEGER NOT NULL, \n\tclient_id VARCHAR(64) NOT NULL, \n\tkind VARCHAR(10) NOT NULL, \n\tat TIMESTAMP WITH TIME ZONE NOT NULL, \n\treceived_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tresult VARCHAR(20) NOT NULL, \n\tPRIMARY KEY (id), \n\tUNIQUE (user_id, client_id), \n\tCONSTRAINT ck_work_punches_kind CHECK (kind IN ('in','break','out','undo')), \n\tFOREIGN KEY(user_id) REFERENCES users (id)\n)\n\n")
    op.execute('\nCREATE TABLE journal_import_map (\n\tid SERIAL NOT NULL, \n\tuser_id INTEGER NOT NULL, \n\tkind VARCHAR(20) NOT NULL, \n\tlegacy_id VARCHAR(100) NOT NULL, \n\ttarget_id INTEGER NOT NULL, \n\tPRIMARY KEY (id), \n\tUNIQUE (user_id, kind, legacy_id), \n\tFOREIGN KEY(user_id) REFERENCES users (id)\n)\n\n')
def downgrade():
    for table in ['journal_import_map','work_punches','todos','activities','work_segments','work_days','tasks','projects','customers']:
        op.drop_table(table)
    op.execute('ALTER TABLE attendance_types DROP COLUMN counts_as_work')
