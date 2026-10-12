import hashlib
import re
import secrets
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from .config import settings
from .models import *
from .schemas import TaskCreate


EVENTS = ('assigned', 'status', 'comment', 'deadline', 'overdue', 'github', 'sprint')


def row(value):
    return jsonable_encoder({column.name: getattr(value, column.name) for column in value.__table__.columns})


def today():
    return datetime.now(ZoneInfo(settings().timezone)).date()


async def register(db, identity):
    statement = insert(User).values(telegram_id=identity['id'], username=(identity.get('username') or '').lower() or None,
                                  name=((identity.get('first_name') or '') + ' ' + (identity.get('last_name') or '')).strip() or 'Telegram user')
    statement = statement.on_conflict_do_update(index_elements=['telegram_id'], set_={
        'username': statement.excluded.username, 'name': statement.excluded.name}).returning(User)
    return (await db.execute(statement)).scalar_one()


async def access(db, user_id, project_id, owner=False, lock=False):
    query = select(Project).join(ProjectMember, ProjectMember.project_id == Project.id).where(
        Project.id == project_id, Project.deleted_at.is_(None), ProjectMember.user_id == user_id)
    if lock:
        query = query.with_for_update(of=Project)
    project = (await db.execute(query)).scalar_one_or_none()
    if not project:
        raise HTTPException(404, 'Проект не найден')
    if owner and project.owner_id != user_id:
        raise HTTPException(403, 'Действие доступно только владельцу')
    return project


async def projects(db, user_id):
    return (await db.scalars(select(Project).join(ProjectMember).where(
        ProjectMember.user_id == user_id, Project.deleted_at.is_(None)).order_by(Project.created_at.desc()))).all()


async def members(db, user_id, project_id):
    await access(db, user_id, project_id)
    return [row(u) for u in (await db.scalars(select(User).join(ProjectMember).where(ProjectMember.project_id == project_id))).all()]


async def statuses(db, project_id):
    return (await db.scalars(select(ProjectStatus).where(ProjectStatus.project_id == project_id).order_by(ProjectStatus.position, ProjectStatus.id))).all()


async def create_project(db, user, payload):
    if await db.scalar(select(Project.id).where(Project.key == payload.key)):
        raise HTTPException(409, 'Ключ проекта уже занят')
    project = Project(**payload.model_dump(), owner_id=user.id)
    db.add(project)
    await db.flush()
    db.add(ProjectMember(project_id=project.id, user_id=user.id))
    for position, name in enumerate(('Backlog', 'To Do', 'In Progress', 'Review', 'Done')):
        db.add(ProjectStatus(project_id=project.id, name=name, position=position, is_done=name == 'Done'))
    user.active_project_id = project.id
    await db.flush()
    await activity(db, project.id, user.id, 'project_created', {'name': project.name})
    return project


async def activity(db, project_id, actor_id, kind, data, task_id=None):
    event = TaskActivity(project_id=project_id, actor_id=actor_id, kind=kind, data=jsonable_encoder(data), task_id=task_id)
    db.add(event)
    await db.flush()
    return event


async def notify(db, project_id, actor_id, kind, message, dedupe):
    users = (await db.scalars(select(ProjectMember.user_id).where(ProjectMember.project_id == project_id))).all()
    preferences = {p.user_id: p.events for p in (await db.scalars(select(NotificationSettings).where(NotificationSettings.project_id == project_id))).all()}
    for user_id in users:
        if user_id == actor_id or not preferences.get(user_id, {}).get(kind, True):
            continue
        await db.execute(insert(Notification).values(project_id=project_id, user_id=user_id, text=message[:4000],
            dedupe_key=f'{dedupe}:{user_id}').on_conflict_do_nothing(index_elements=['dedupe_key']))


async def validate_task_refs(db, project_id, data):
    for name, model in (('status_id', ProjectStatus), ('sprint_id', Sprint)):
        value = data.get(name)
        if value is not None and not await db.scalar(select(model.id).where(model.id == value, model.project_id == project_id)):
            raise HTTPException(422, 'Статус или спринт из другого проекта')
    assignee = data.get('assignee_id')
    if assignee is not None and not await db.scalar(select(ProjectMember.id).where(ProjectMember.project_id == project_id, ProjectMember.user_id == assignee)):
        raise HTTPException(422, 'Исполнитель не является участником проекта')


async def create_task(db, user_id, project_id, payload: TaskCreate):
    project = await access(db, user_id, project_id, lock=True)
    data = payload.model_dump()
    await validate_task_refs(db, project_id, data)
    if data['status_id'] is None:
        data['status_id'] = (await statuses(db, project_id))[0].id
    project.counter += 1
    task = Task(project_id=project_id, author_id=user_id, key=f'{project.key}-{project.counter}', **data)
    db.add(task)
    await db.flush()
    await activity(db, project_id, user_id, 'created', {'title': task.title}, task.id)
    if task.assignee_id:
        await notify(db, project_id, user_id, 'assigned', f'{task.key}: назначена задача {task.title}', f'assigned:{task.id}:1')
    return task


async def get_task(db, user_id, task_id, lock=False):
    query = select(Task).where(Task.id == task_id)
    if lock:
        query = query.with_for_update()
    task = (await db.execute(query)).scalar_one_or_none()
    if not task:
        raise HTTPException(404, 'Задача не найдена')
    await access(db, user_id, task.project_id)
    return task


async def task_by_key(db, user_id, key):
    task_id = await db.scalar(select(Task.id).where(Task.key == key.upper()))
    return await get_task(db, user_id, task_id or 0)


async def update_task(db, user_id, task_id, payload):
    task = await get_task(db, user_id, task_id, lock=True)
    if task.version != payload.version:
        raise HTTPException(409, 'Задача уже изменена. Обновите её и повторите действие.')
    data = payload.model_dump(exclude_unset=True)
    data.pop('version')
    if any(key in data and data[key] is None for key in ('title', 'status_id', 'priority', 'description')):
        raise HTTPException(422, 'Обязательные поля не могут быть пустыми')
    if 'title' in data:
        if not data['title'].strip():
            raise HTTPException(422, 'Заголовок не может быть пустым')
        data['title'] = data['title'].strip()
    await validate_task_refs(db, task.project_id, data)
    changes = {}
    for name, value in data.items():
        if name == 'deleted':
            name, value = 'deleted_at', utcnow() if value else None
        old = getattr(task, name)
        if old != value:
            changes[name] = {'before': jsonable_encoder(old), 'after': jsonable_encoder(value)}
            setattr(task, name, value)
    if changes:
        task.version += 1
        task.updated_at = utcnow()
        await activity(db, task.project_id, user_id, 'updated', changes, task.id)
        for field, kind in (('status_id', 'status'), ('assignee_id', 'assigned')):
            if field in changes:
                await notify(db, task.project_id, user_id, kind, f'{task.key}: обновлена задача {task.title}', f'{kind}:{task.id}:{task.version}')
    await db.flush()
    return task


async def list_tasks(db, user_id, project_id=None, deleted=False, q='', mine=False):
    query = select(Task).join(ProjectMember, ProjectMember.project_id == Task.project_id).join(Project, Project.id == Task.project_id).where(
        ProjectMember.user_id == user_id, Project.deleted_at.is_(None), Task.deleted_at.is_not(None) if deleted else Task.deleted_at.is_(None))
    if project_id:
        await access(db, user_id, project_id)
        query = query.where(Task.project_id == project_id)
    if q:
        escaped = q.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
        query = query.where(Task.title.ilike(f'%{escaped}%', escape='\\') | Task.key.ilike(f'%{escaped}%', escape='\\'))
    if mine:
        query = query.where(Task.assignee_id == user_id)
    return (await db.scalars(query.order_by(Task.updated_at.desc()).limit(1000))).all()


async def task_details(db, user_id, task_id):
    task = await get_task(db, user_id, task_id)
    result = row(task)
    for name, model in (('comments', TaskComment), ('checklist', TaskChecklistItem), ('attachments', TaskAttachment), ('activity', TaskActivity), ('time', TimeEntry), ('github', GitHubTaskLink)):
        result[name] = [row(item) for item in (await db.scalars(select(model).where(model.task_id == task.id).order_by(model.created_at.desc()).limit(500))).all()]
    for item in result['attachments']:
        item.pop('storage_key')
    result['total_seconds'] = sum(entry['seconds'] for entry in result['time'])
    return result


async def add_comment(db, user_id, task_id, text):
    task = await get_task(db, user_id, task_id)
    comment = TaskComment(task_id=task.id, author_id=user_id, text=text)
    db.add(comment)
    await db.flush()
    await activity(db, task.project_id, user_id, 'comment', {'text': text}, task.id)
    await notify(db, task.project_id, user_id, 'comment', f'{task.key}: комментарий\n{text}', f'comment:{comment.id}')
    return comment


async def create_invite(db, user_id, project_id, target=None):
    await access(db, user_id, project_id)
    token = secrets.token_urlsafe(24)
    db.add(Invitation(project_id=project_id, token_hash=hashlib.sha256(token.encode()).hexdigest(),
                      target=target.lower().lstrip('@') if target else None, expires_at=utcnow() + timedelta(days=7)))
    await db.flush()
    return {'token': token, 'url': f'https://t.me/{settings().bot_username}?start=invite_{token}', 'expires_in_days': 7}


async def accept_invite(db, user, token):
    invite = (await db.execute(select(Invitation).where(Invitation.token_hash == hashlib.sha256(token.encode()).hexdigest()).with_for_update())).scalar_one_or_none()
    if not invite or invite.expires_at < utcnow() or invite.accepted_by:
        raise HTTPException(404, 'Приглашение недействительно или уже использовано')
    project = await db.get(Project, invite.project_id)
    if not project or project.deleted_at:
        raise HTTPException(404, 'Проект недоступен')
    if invite.target and invite.target not in (str(user.telegram_id), user.username):
        raise HTTPException(403, 'Приглашение предназначено другому пользователю')
    await db.execute(insert(ProjectMember).values(project_id=project.id, user_id=user.id).on_conflict_do_nothing())
    invite.accepted_by = user.id
    user.active_project_id = project.id
    await activity(db, project.id, user.id, 'member_joined', {'name': user.name})
    return project


async def timer(db, user_id):
    return (await db.execute(select(TimeEntry).where(TimeEntry.user_id == user_id, TimeEntry.ended_at.is_(None)))).scalar_one_or_none()


def timer_row(entry):
    result = row(entry)
    result['elapsed_seconds'] = entry.seconds + (max(0, int((utcnow() - entry.running_since).total_seconds())) if entry.running_since else 0)
    return result


async def timer_action(db, user_id, action, task_id=None):
    await db.execute(select(User.id).where(User.id == user_id).with_for_update())
    entry = await timer(db, user_id)
    now = utcnow()
    if action == 'start':
        task = await get_task(db, user_id, task_id)
        if task.deleted_at:
            raise HTTPException(409, 'Задача удалена')
        if entry and entry.task_id != task.id:
            raise HTTPException(409, 'Сначала остановите текущий таймер')
        if entry is None:
            entry = TimeEntry(task_id=task.id, user_id=user_id, running_since=now)
            db.add(entry)
        elif entry.running_since is None:
            entry.running_since = now
    else:
        if entry is None:
            raise HTTPException(409, 'Нет активного таймера')
        if entry.running_since:
            entry.seconds += max(0, int((now - entry.running_since).total_seconds()))
            entry.running_since = None
        if action == 'stop':
            entry.ended_at = now
    await db.flush()
    task = await db.get(Task, entry.task_id)
    await activity(db, task.project_id, user_id, f'timer_{action}', {'seconds': entry.seconds}, task.id)
    return timer_row(entry)


async def manual_time(db, user_id, task_id, payload):
    task = await get_task(db, user_id, task_id)
    entry = TimeEntry(task_id=task.id, user_id=user_id, seconds=payload.seconds, note=payload.note,
                      ended_at=utcnow(), running_since=None)
    db.add(entry)
    await db.flush()
    await activity(db, task.project_id, user_id, 'time_added', {'seconds': payload.seconds}, task.id)
    return entry
