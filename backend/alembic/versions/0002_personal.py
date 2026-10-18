"""Personal tasks, persistent reminders and voice capture queue."""
from alembic import op
import sqlalchemy as sa

revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('personal_tasks',
        sa.Column('id', sa.Integer(), primary_key=True), sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('title', sa.String(500), nullable=False), sa.Column('note', sa.Text(), nullable=False),
        sa.Column('due_at', sa.DateTime(timezone=True)), sa.Column('due_has_time', sa.Boolean(), nullable=False),
        sa.Column('bucket', sa.String(20), nullable=False), sa.Column('done_at', sa.DateTime(timezone=True)),
        sa.Column('deleted_at', sa.DateTime(timezone=True)), sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False), sa.Column('source_task_id', sa.Integer(), sa.ForeignKey('tasks.id')),
        sa.UniqueConstraint('user_id', 'source_task_id'), sa.CheckConstraint("bucket IN ('inbox', 'today', 'later')"))
    for name, columns in [('ix_personal_tasks_user_id', ['user_id']), ('ix_personal_tasks_source_task_id', ['source_task_id']), ('ix_personal_tasks_user_due', ['user_id', 'due_at'])]:
        op.create_index(name, 'personal_tasks', columns)
    op.create_table('personal_reminders',
        sa.Column('id', sa.Integer(), primary_key=True), sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('task_id', sa.Integer(), sa.ForeignKey('personal_tasks.id'), nullable=False, unique=True),
        sa.Column('next_at', sa.DateTime(timezone=True), nullable=False), sa.Column('interval_seconds', sa.Integer(), nullable=False),
        sa.Column('enabled', sa.Boolean(), nullable=False), sa.Column('last_sent_at', sa.DateTime(timezone=True)),
        sa.Column('retry_at', sa.DateTime(timezone=True)), sa.Column('attempts', sa.Integer(), nullable=False),
        sa.CheckConstraint('interval_seconds IN (0, 3600, 86400, 259200, 604800)'))
    op.create_index('ix_personal_reminders_next_at', 'personal_reminders', ['next_at'])
    op.create_table('personal_captures',
        sa.Column('id', sa.Integer(), primary_key=True), sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=False), sa.Column('message_id', sa.BigInteger(), nullable=False),
        sa.Column('file_id', sa.String(500)), sa.Column('transcript', sa.Text()), sa.Column('draft', sa.JSON()),
        sa.Column('task_id', sa.Integer(), sa.ForeignKey('personal_tasks.id')), sa.Column('state', sa.String(24), nullable=False),
        sa.Column('available_at', sa.DateTime(timezone=True), nullable=False), sa.Column('attempts', sa.Integer(), nullable=False),
        sa.Column('notified', sa.Boolean(), nullable=False), sa.UniqueConstraint('user_id', 'message_id'))
    for name in ['user_id', 'task_id', 'state', 'available_at']:
        op.create_index('ix_personal_captures_' + name, 'personal_captures', [name])
    op.execute("""INSERT INTO personal_tasks (created_at, user_id, title, note, due_at, due_has_time, bucket, done_at, deleted_at, updated_at, version, source_task_id)
        SELECT t.created_at, m.user_id, t.title, t.description,
          CASE WHEN t.deadline IS NULL THEN NULL ELSE ((t.deadline + time '23:59') AT TIME ZONE 'Europe/Moscow') END,
          false, CASE WHEN t.deadline IS NULL THEN 'inbox' ELSE 'later' END,
          CASE WHEN s.is_done THEN t.updated_at ELSE NULL END, t.deleted_at, t.updated_at, 1, t.id
        FROM tasks t JOIN project_statuses s ON s.id=t.status_id
        JOIN projects p ON p.id=t.project_id AND p.deleted_at IS NULL
        JOIN project_members m ON m.project_id=t.project_id AND (m.user_id=t.author_id OR m.user_id=t.assignee_id)
        ON CONFLICT (user_id, source_task_id) DO NOTHING""")


def downgrade():
    op.drop_table('personal_captures')
    op.drop_table('personal_reminders')
    op.drop_table('personal_tasks')
