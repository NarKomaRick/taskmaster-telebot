from datetime import date, datetime, timezone
from sqlalchemy import BigInteger, Boolean, CheckConstraint, Date, DateTime, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow():
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Record:
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class User(Record, Base):
    __tablename__ = 'users'
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True)
    username: Mapped[str | None] = mapped_column(String(64), index=True)
    name: Mapped[str] = mapped_column(String(200))
    active_project_id: Mapped[int | None] = mapped_column(Integer)


class Project(Record, Base):
    __tablename__ = 'projects'
    name: Mapped[str] = mapped_column(String(120))
    key: Mapped[str] = mapped_column(String(12), unique=True)
    description: Mapped[str] = mapped_column(Text, default='')
    owner_id: Mapped[int] = mapped_column(ForeignKey('users.id'), index=True)
    counter: Mapped[int] = mapped_column(Integer, default=0)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ProjectMember(Record, Base):
    __tablename__ = 'project_members'
    __table_args__ = (UniqueConstraint('project_id', 'user_id'),)
    project_id: Mapped[int] = mapped_column(ForeignKey('projects.id'), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'), index=True)


class ProjectStatus(Record, Base):
    __tablename__ = 'project_statuses'
    __table_args__ = (UniqueConstraint('project_id', 'name'),)
    project_id: Mapped[int] = mapped_column(ForeignKey('projects.id'), index=True)
    name: Mapped[str] = mapped_column(String(80))
    position: Mapped[int] = mapped_column(Integer)
    is_done: Mapped[bool] = mapped_column(Boolean, default=False)


class Sprint(Record, Base):
    __tablename__ = 'sprints'
    __table_args__ = (CheckConstraint('end_date >= start_date'),)
    project_id: Mapped[int] = mapped_column(ForeignKey('projects.id'), index=True)
    name: Mapped[str] = mapped_column(String(120))
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)
    state: Mapped[str] = mapped_column(String(20), default='planned')
    result: Mapped[dict | None] = mapped_column(JSON)


class Task(Record, Base):
    __tablename__ = 'tasks'
    __table_args__ = (CheckConstraint("priority IN ('Low', 'Medium', 'High', 'Critical')"), Index('ix_tasks_project_deadline', 'project_id', 'deadline'))
    project_id: Mapped[int] = mapped_column(ForeignKey('projects.id'), index=True)
    key: Mapped[str] = mapped_column(String(32), unique=True)
    title: Mapped[str] = mapped_column(String(300))
    description: Mapped[str] = mapped_column(Text, default='')
    status_id: Mapped[int] = mapped_column(ForeignKey('project_statuses.id'), index=True)
    priority: Mapped[str] = mapped_column(String(20), default='Medium')
    assignee_id: Mapped[int | None] = mapped_column(ForeignKey('users.id'), index=True)
    author_id: Mapped[int] = mapped_column(ForeignKey('users.id'), index=True)
    deadline: Mapped[date | None] = mapped_column(Date)
    sprint_id: Mapped[int | None] = mapped_column(ForeignKey('sprints.id'), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class TaskChecklistItem(Record, Base):
    __tablename__ = 'task_checklist_items'
    task_id: Mapped[int] = mapped_column(ForeignKey('tasks.id'), index=True)
    text: Mapped[str] = mapped_column(String(500))
    done: Mapped[bool] = mapped_column(Boolean, default=False)


class TaskComment(Record, Base):
    __tablename__ = 'task_comments'
    task_id: Mapped[int] = mapped_column(ForeignKey('tasks.id'), index=True)
    author_id: Mapped[int] = mapped_column(ForeignKey('users.id'), index=True)
    text: Mapped[str] = mapped_column(Text)


class TaskAttachment(Record, Base):
    __tablename__ = 'task_attachments'
    task_id: Mapped[int] = mapped_column(ForeignKey('tasks.id'), index=True)
    author_id: Mapped[int] = mapped_column(ForeignKey('users.id'), index=True)
    name: Mapped[str] = mapped_column(String(255))
    storage_key: Mapped[str] = mapped_column(String(64), unique=True)
    content_type: Mapped[str] = mapped_column(String(100))
    size: Mapped[int] = mapped_column(Integer)


class TimeEntry(Record, Base):
    __tablename__ = 'time_entries'
    __table_args__ = (CheckConstraint('seconds >= 0'), Index('ix_one_open_timer', 'user_id', unique=True, postgresql_where=text('ended_at IS NULL')))
    task_id: Mapped[int] = mapped_column(ForeignKey('tasks.id'), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    running_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    seconds: Mapped[int] = mapped_column(Integer, default=0)
    note: Mapped[str] = mapped_column(String(500), default='')


class TaskActivity(Record, Base):
    __tablename__ = 'task_activity'
    project_id: Mapped[int] = mapped_column(ForeignKey('projects.id'), index=True)
    task_id: Mapped[int | None] = mapped_column(ForeignKey('tasks.id'), index=True)
    actor_id: Mapped[int | None] = mapped_column(ForeignKey('users.id'), index=True)
    kind: Mapped[str] = mapped_column(String(40))
    data: Mapped[dict] = mapped_column(JSON, default=dict)


class Invitation(Record, Base):
    __tablename__ = 'invitations'
    project_id: Mapped[int] = mapped_column(ForeignKey('projects.id'), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    target: Mapped[str | None] = mapped_column(String(80))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    accepted_by: Mapped[int | None] = mapped_column(ForeignKey('users.id'), index=True)


class NotificationSettings(Record, Base):
    __tablename__ = 'notification_settings'
    __table_args__ = (UniqueConstraint('project_id', 'user_id'),)
    project_id: Mapped[int] = mapped_column(ForeignKey('projects.id'), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'), index=True)
    events: Mapped[dict] = mapped_column(JSON, default=dict)


class Notification(Record, Base):
    __tablename__ = 'notifications'
    project_id: Mapped[int] = mapped_column(ForeignKey('projects.id'), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'), index=True)
    dedupe_key: Mapped[str] = mapped_column(String(200), unique=True)
    text: Mapped[str] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failed: Mapped[bool] = mapped_column(Boolean, default=False)


class GitHubConnection(Record, Base):
    __tablename__ = 'github_connections'
    project_id: Mapped[int] = mapped_column(ForeignKey('projects.id'), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'), index=True)
    encrypted_token: Mapped[str | None] = mapped_column(Text)


class GitHubRepository(Record, Base):
    __tablename__ = 'github_repositories'
    __table_args__ = (UniqueConstraint('project_id', 'full_name'),)
    project_id: Mapped[int] = mapped_column(ForeignKey('projects.id'), index=True)
    connection_id: Mapped[int] = mapped_column(ForeignKey('github_connections.id'), index=True)
    full_name: Mapped[str] = mapped_column(String(200))
    encrypted_secret: Mapped[str] = mapped_column(Text)


class GitHubTaskLink(Record, Base):
    __tablename__ = 'github_task_links'
    __table_args__ = (UniqueConstraint('task_id', 'url'),)
    task_id: Mapped[int] = mapped_column(ForeignKey('tasks.id'), index=True)
    repository_id: Mapped[int | None] = mapped_column(ForeignKey('github_repositories.id'), index=True)
    kind: Mapped[str] = mapped_column(String(20))
    title: Mapped[str] = mapped_column(String(500))
    url: Mapped[str] = mapped_column(String(1000))
    state: Mapped[str | None] = mapped_column(String(40))


class WebhookDelivery(Record, Base):
    __tablename__ = 'webhook_deliveries'
    __table_args__ = (UniqueConstraint('repository_id', 'delivery_id'),)
    repository_id: Mapped[int] = mapped_column(ForeignKey('github_repositories.id'), index=True)
    delivery_id: Mapped[str] = mapped_column(String(100))


class PersonalTask(Record, Base):
    __tablename__ = 'personal_tasks'
    __table_args__ = (UniqueConstraint('user_id', 'source_task_id'), CheckConstraint("bucket IN ('inbox', 'today', 'later')"), Index('ix_personal_tasks_user_due', 'user_id', 'due_at'))
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'), index=True)
    title: Mapped[str] = mapped_column(String(500))
    note: Mapped[str] = mapped_column(Text, default='')
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    due_has_time: Mapped[bool] = mapped_column(Boolean, default=False)
    bucket: Mapped[str] = mapped_column(String(20), default='inbox')
    done_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    version: Mapped[int] = mapped_column(Integer, default=1)
    source_task_id: Mapped[int | None] = mapped_column(ForeignKey('tasks.id'), index=True)


class PersonalReminder(Record, Base):
    __tablename__ = 'personal_reminders'
    __table_args__ = (CheckConstraint('interval_seconds IN (0, 3600, 86400, 259200, 604800)'),)
    task_id: Mapped[int] = mapped_column(ForeignKey('personal_tasks.id'), unique=True)
    next_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    interval_seconds: Mapped[int] = mapped_column(Integer, default=0)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, default=0)


class PersonalCapture(Record, Base):
    __tablename__ = 'personal_captures'
    __table_args__ = (UniqueConstraint('user_id', 'message_id'),)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'), index=True)
    message_id: Mapped[int] = mapped_column(BigInteger)
    file_id: Mapped[str | None] = mapped_column(String(500))
    transcript: Mapped[str | None] = mapped_column(Text)
    draft: Mapped[dict | None] = mapped_column(JSON)
    task_id: Mapped[int | None] = mapped_column(ForeignKey('personal_tasks.id'), index=True)
    state: Mapped[str] = mapped_column(String(24), default='queued', index=True)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    notified: Mapped[bool] = mapped_column(Boolean, default=False)
