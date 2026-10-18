"""Standalone migration verification against the explicitly isolated test database."""
import asyncio
import os
import subprocess
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from app.models import Base

url = os.environ['TEST_DATABASE_URL']
if not url.rsplit('/', 1)[-1].endswith('_test'):
    raise RuntimeError('A dedicated _test database is required')
env = {**os.environ, 'DATABASE_URL': url}


async def reset():
    engine = create_async_engine(url)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.execute(text('DROP TABLE IF EXISTS alembic_version'))
    await engine.dispose()


async def seed():
    engine = create_async_engine(url)
    async with engine.begin() as connection:
        await connection.execute(text("""INSERT INTO users(id,created_at,telegram_id,name) VALUES
            (1,now(),900000001,'Owner'),(2,now(),900000002,'Assignee'),(3,now(),900000003,'Outsider');
        """))
        await connection.execute(text("INSERT INTO projects(id,created_at,name,key,description,owner_id,counter) VALUES (1,now(),'Legacy','OLD','',1,1)"))
        await connection.execute(text("INSERT INTO project_members(id,created_at,project_id,user_id) VALUES (1,now(),1,1),(2,now(),1,2)"))
        await connection.execute(text("INSERT INTO project_statuses(id,created_at,project_id,name,position,is_done) VALUES (1,now(),1,'To Do',0,false)"))
        await connection.execute(text("INSERT INTO tasks(id,created_at,project_id,key,title,description,status_id,priority,author_id,assignee_id,deadline,version,updated_at) VALUES (1,now(),1,'OLD-1','Keep my task','Original note',1,'Medium',1,2,'2026-10-25',1,now())"))
        for table in ('users', 'projects', 'project_members', 'project_statuses', 'tasks'):
            await connection.execute(text(f"SELECT setval(pg_get_serial_sequence('{table}','id'),(SELECT max(id) FROM {table}))"))
    await engine.dispose()


async def verify():
    engine = create_async_engine(url)
    async with engine.begin() as connection:
        rows = (await connection.execute(text('SELECT user_id,title,note,source_task_id FROM personal_tasks ORDER BY user_id'))).all()
        assert [r.user_id for r in rows] == [1, 2], rows
        assert all(r.title == 'Keep my task' and r.note == 'Original note' and r.source_task_id == 1 for r in rows)
        assert (await connection.execute(text('SELECT count(*) FROM tasks'))).scalar() == 1
    await engine.dispose()


asyncio.run(reset())
subprocess.run(['alembic', 'upgrade', '0001'], env=env, check=True)
asyncio.run(seed())
subprocess.run(['alembic', 'upgrade', 'head'], env=env, check=True)
asyncio.run(verify())
subprocess.run(['alembic', 'check'], env=env, check=True)
print('Migration verified: original retained, author and assignee imported, outsider excluded')
