import os
from alembic import context
from sqlalchemy import engine_from_config, pool
from app.db import Base
from app import models
config=context.config
config.set_main_option('sqlalchemy.url',os.getenv('DATABASE_URL','sqlite:///./workplan.db').replace('%','%%'))
target_metadata=Base.metadata
if context.is_offline_mode():
    context.configure(url=config.get_main_option('sqlalchemy.url'),target_metadata=target_metadata,literal_binds=True)
    with context.begin_transaction(): context.run_migrations()
else:
    engine=engine_from_config(config.get_section(config.config_ini_section),prefix='sqlalchemy.',poolclass=pool.NullPool)
    with engine.connect() as conn:
        context.configure(connection=conn,target_metadata=target_metadata,compare_type=True)
        with context.begin_transaction(): context.run_migrations()
