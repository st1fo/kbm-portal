"""Nástenky úloh (kanban) namiesto to-do; existujúce to-do sa presunú na osobnú nástenku „Moje úlohy“. Revision 0004."""
from alembic import op
revision='0004'
down_revision='0003'
branch_labels=None
depends_on=None
TABLES=['card_checklist','card_assignees','cards','board_columns','board_members','boards']
def upgrade():
    op.execute('\nCREATE TABLE boards (\n\tid SERIAL NOT NULL, \n\tname VARCHAR(100) NOT NULL, \n\tcolor VARCHAR(7) NOT NULL, \n\towner_id INTEGER NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (id), \n\tFOREIGN KEY(owner_id) REFERENCES users (id)\n)\n\n')
    op.execute('CREATE INDEX ix_boards_owner_id ON boards (owner_id)')
    op.execute('\nCREATE TABLE board_members (\n\tboard_id INTEGER NOT NULL, \n\tuser_id INTEGER NOT NULL, \n\tPRIMARY KEY (board_id, user_id), \n\tFOREIGN KEY(board_id) REFERENCES boards (id) ON DELETE CASCADE, \n\tFOREIGN KEY(user_id) REFERENCES users (id)\n)\n\n')
    op.execute('CREATE INDEX ix_board_members_user_id ON board_members (user_id)')
    op.execute('\nCREATE TABLE board_columns (\n\tid SERIAL NOT NULL, \n\tboard_id INTEGER NOT NULL, \n\tname VARCHAR(60) NOT NULL, \n\tposition INTEGER NOT NULL, \n\tis_done BOOLEAN NOT NULL, \n\tPRIMARY KEY (id), \n\tFOREIGN KEY(board_id) REFERENCES boards (id) ON DELETE CASCADE\n)\n\n')
    op.execute('CREATE INDEX ix_board_columns_board_id ON board_columns (board_id)')
    op.execute('\nCREATE TABLE cards (\n\tid SERIAL NOT NULL, \n\tboard_id INTEGER NOT NULL, \n\tcolumn_id INTEGER NOT NULL, \n\tposition INTEGER NOT NULL, \n\ttitle VARCHAR(500) NOT NULL, \n\tdescription VARCHAR(5000) NOT NULL, \n\tdue DATE, \n\tpriority SMALLINT NOT NULL, \n\tproject_id INTEGER, \n\tcustomer_id INTEGER, \n\tcreated_by INTEGER, \n\tdone_at TIMESTAMP WITH TIME ZONE, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tupdated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT ck_cards_priority CHECK (priority >= 0 AND priority <= 2), \n\tCONSTRAINT ck_cards_project_or_customer CHECK (project_id IS NULL OR customer_id IS NULL), \n\tFOREIGN KEY(board_id) REFERENCES boards (id) ON DELETE CASCADE, \n\tFOREIGN KEY(column_id) REFERENCES board_columns (id) ON DELETE CASCADE, \n\tFOREIGN KEY(project_id) REFERENCES projects (id), \n\tFOREIGN KEY(customer_id) REFERENCES customers (id), \n\tFOREIGN KEY(created_by) REFERENCES users (id)\n)\n\n')
    op.execute('CREATE INDEX ix_cards_board_id ON cards (board_id)')
    op.execute('CREATE INDEX ix_cards_column_id ON cards (column_id)')
    op.execute('\nCREATE TABLE card_assignees (\n\tcard_id INTEGER NOT NULL, \n\tuser_id INTEGER NOT NULL, \n\tPRIMARY KEY (card_id, user_id), \n\tFOREIGN KEY(card_id) REFERENCES cards (id) ON DELETE CASCADE, \n\tFOREIGN KEY(user_id) REFERENCES users (id)\n)\n\n')
    op.execute('CREATE INDEX ix_card_assignees_user_id ON card_assignees (user_id)')
    op.execute('\nCREATE TABLE card_checklist (\n\tid SERIAL NOT NULL, \n\tcard_id INTEGER NOT NULL, \n\ttext VARCHAR(300) NOT NULL, \n\tdone BOOLEAN NOT NULL, \n\tposition INTEGER NOT NULL, \n\tPRIMARY KEY (id), \n\tFOREIGN KEY(card_id) REFERENCES cards (id) ON DELETE CASCADE\n)\n\n')
    op.execute('CREATE INDEX ix_card_checklist_card_id ON card_checklist (card_id)')
    # Každý, kto mal to-do, dostane osobnú nástenku s tromi stĺpcami. Karty preberú ID to-do,
    # takže mapovanie starého importu (journal_import_map, kind='todo') ostane platné.
    op.execute("INSERT INTO boards (name, color, owner_id, created_at) SELECT 'Moje úlohy', '#326c56', user_id, min(created_at) FROM todos GROUP BY user_id")
    op.execute("INSERT INTO board_columns (board_id, name, position, is_done) SELECT b.id, c.name, c.pos, c.done FROM boards b CROSS JOIN (VALUES ('Na spracovanie', 0, false), ('Robím', 1, false), ('Hotovo', 2, true)) AS c(name, pos, done)")
    op.execute("""INSERT INTO cards (id, board_id, column_id, position, title, description, due, priority, project_id, customer_id, created_by, done_at, created_at, updated_at)
        SELECT t.id, b.id, col.id,
               row_number() OVER (PARTITION BY t.user_id, t.done_at IS NULL ORDER BY t.done_at DESC NULLS LAST, t.due NULLS LAST, t.priority DESC, t.created_at, t.id) - 1,
               t.text, '', t.due, t.priority, t.project_id, t.customer_id, t.user_id, t.done_at, t.created_at, t.created_at
        FROM todos t JOIN boards b ON b.owner_id = t.user_id
        JOIN board_columns col ON col.board_id = b.id AND col.position = CASE WHEN t.done_at IS NULL THEN 0 ELSE 2 END""")
    op.execute("SELECT setval(pg_get_serial_sequence('cards', 'id'), COALESCE((SELECT max(id) FROM cards), 0) + 1, false)")
    op.execute('DROP TABLE todos')
def downgrade():
    op.execute('\nCREATE TABLE todos (\n\tid SERIAL NOT NULL, \n\tuser_id INTEGER NOT NULL, \n\ttext VARCHAR(500) NOT NULL, \n\tdue DATE, \n\tpriority SMALLINT NOT NULL, \n\tproject_id INTEGER, \n\tcustomer_id INTEGER, \n\tdone_at TIMESTAMP WITH TIME ZONE, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT ck_todos_priority CHECK (priority >= 0 AND priority <= 2), \n\tCONSTRAINT ck_todos_project_or_customer CHECK (project_id IS NULL OR customer_id IS NULL), \n\tFOREIGN KEY(user_id) REFERENCES users (id), \n\tFOREIGN KEY(project_id) REFERENCES projects (id), \n\tFOREIGN KEY(customer_id) REFERENCES customers (id)\n)\n\n')
    op.execute('CREATE INDEX ix_todos_user_id ON todos (user_id)')
    op.execute("""INSERT INTO todos (id, user_id, text, due, priority, project_id, customer_id, done_at, created_at)
        SELECT c.id, b.owner_id, left(c.title, 500), c.due, c.priority, c.project_id, c.customer_id, c.done_at, c.created_at FROM cards c JOIN boards b ON b.id = c.board_id""")
    op.execute("SELECT setval(pg_get_serial_sequence('todos', 'id'), COALESCE((SELECT max(id) FROM todos), 0) + 1, false)")
    for table in TABLES:
        op.drop_table(table)
