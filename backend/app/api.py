import asyncio
import hashlib
import json
import logging
import os
import re
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated
from cryptography.fernet import Fernet
from fastapi import Depends, FastAPI, File, Header, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
import httpx
from redis.asyncio import Redis
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from . import service as svc
from .auth import validate_init_data, verify_github
from .config import settings
from .db import engine, session
from .models import *
from .schemas import *

cfg = settings()
redis = Redis.from_url(cfg.redis_url)
crypto = Fernet(cfg.encryption_key.encode())
DB = Annotated[AsyncSession, Depends(session)]


@asynccontextmanager
async def lifespan(app):
    Path(cfg.attachment_dir).mkdir(parents=True, exist_ok=True)
    yield
    await redis.aclose()
    await engine.dispose()


app = FastAPI(title='TaskMaster', version='1.0.0', docs_url='/api/docs', openapi_url='/api/openapi.json', lifespan=lifespan)


@app.middleware('http')
async def limits(request, call_next):
    if request.url.path.startswith('/api/') and request.url.path not in ('/api/docs', '/api/openapi.json'):
        key = hashlib.sha256((request.headers.get('authorization') or (request.client.host if request.client else 'unknown')).encode()).hexdigest()
        try:
            count = await redis.incr(f'rate:{key}')
            if count == 1:
                await redis.expire(f'rate:{key}', 60)
            if count > 180:
                return JSONResponse({'detail': 'Слишком много запросов. Подождите минуту.'}, status_code=429)
        except Exception:
            return JSONResponse({'detail': 'Сервис временно недоступен'}, status_code=503)
    response = await call_next(request)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'no-referrer'
    response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self' https://telegram.org; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob: https:; font-src 'self'; connect-src 'self'; frame-ancestors https://web.telegram.org https://*.telegram.org https://telegram.org; object-src 'none'; base-uri 'self'"
    if request.url.path.startswith('/api/'):
        response.headers['Cache-Control'] = 'no-store'
    return response


@app.exception_handler(IntegrityError)
async def integrity_error(request, error):
    return JSONResponse({'detail': 'Запись уже существует или данные конфликтуют. Обновите экран.'}, status_code=409)


async def current_user(db: DB, authorization: Annotated[str | None, Header()] = None):
    if not authorization or not authorization.startswith('tma '):
        raise HTTPException(401, 'Откройте приложение через Telegram')
    return await svc.register(db, validate_init_data(authorization[4:], cfg.bot_token))


Auth = Annotated[User, Depends(current_user)]


@app.get('/health')
async def health(db: DB):
    await db.execute(text('SELECT 1'))
    await redis.ping()
    return {'status': 'ok'}


@app.get('/api/me')
async def me(user: Auth):
    return svc.row(user)


@app.get('/api/projects')
async def project_list(db: DB, user: Auth):
    return [svc.row(p) for p in await svc.projects(db, user.id)]


@app.post('/api/projects', status_code=201)
async def project_create(payload: ProjectCreate, db: DB, user: Auth):
    return svc.row(await svc.create_project(db, user, payload))


@app.get('/api/projects/{project_id}')
async def project_get(project_id: int, db: DB, user: Auth):
    project = await svc.access(db, user.id, project_id)
    user.active_project_id = project_id
    return {**svc.row(project), 'statuses': [svc.row(s) for s in await svc.statuses(db, project_id)],
            'members': await svc.members(db, user.id, project_id)}


@app.patch('/api/projects/{project_id}')
async def project_edit(project_id: int, payload: ProjectEdit, db: DB, user: Auth):
    project = await svc.access(db, user.id, project_id, lock=True)
    for name, value in payload.model_dump(exclude_unset=True).items():
        if value is None or (name == 'name' and not value.strip()):
            raise HTTPException(422, 'Некорректное название или описание')
        setattr(project, name, value)
    await svc.activity(db, project_id, user.id, 'project_updated', payload.model_dump(exclude_unset=True))
    return svc.row(project)


@app.delete('/api/projects/{project_id}')
async def project_delete(project_id: int, payload: DeleteProject, db: DB, user: Auth):
    project = await svc.access(db, user.id, project_id, owner=True, lock=True)
    if payload.confirm_key != project.key:
        raise HTTPException(422, 'Введите ключ проекта для подтверждения')
    project.deleted_at = utcnow()
    await svc.activity(db, project_id, user.id, 'project_deleted', {})
    return {'ok': True}


@app.post('/api/projects/{project_id}/owner')
async def transfer_owner(project_id: int, payload: TransferOwner, db: DB, user: Auth):
    project = await svc.access(db, user.id, project_id, owner=True, lock=True)
    if payload.confirm_key != project.key or not await db.scalar(select(ProjectMember.id).where(ProjectMember.project_id == project_id, ProjectMember.user_id == payload.user_id)):
        raise HTTPException(422, 'Проверьте ключ и нового владельца')
    project.owner_id = payload.user_id
    await svc.activity(db, project_id, user.id, 'owner_changed', {'owner_id': payload.user_id})
    return svc.row(project)


@app.get('/api/dashboard')
async def dashboard(db: DB, user: Auth):
    tasks = await svc.list_tasks(db, user.id)
    done = set((await db.scalars(select(ProjectStatus.id).where(ProjectStatus.is_done.is_(True)))).all())
    active = [t for t in tasks if t.status_id not in done]
    day = svc.today()
    return {'projects': [dict(svc.row(p), active_tasks=sum(t.project_id == p.id for t in active)) for p in await svc.projects(db, user.id)],
            'today': [svc.row(t) for t in active if t.deadline == day],
            'overdue': [svc.row(t) for t in active if t.deadline and t.deadline < day],
            'upcoming': [svc.row(t) for t in sorted(active, key=lambda t: t.deadline or date.max) if t.deadline and t.deadline > day][:20],
            'active_tasks': len(active)}


@app.get('/api/projects/{project_id}/tasks')
async def tasks_list(project_id: int, db: DB, user: Auth, q: str = '', deleted: bool = False, mine: bool = False):
    if len(q) > 300:
        raise HTTPException(422, 'Слишком длинный поиск')
    return [svc.row(t) for t in await svc.list_tasks(db, user.id, project_id, deleted, q, mine)]


@app.post('/api/projects/{project_id}/tasks', status_code=201)
async def task_create(project_id: int, payload: TaskCreate, db: DB, user: Auth):
    return svc.row(await svc.create_task(db, user.id, project_id, payload))


@app.get('/api/tasks/{task_id}')
async def task_details(task_id: int, db: DB, user: Auth):
    return await svc.task_details(db, user.id, task_id)


@app.patch('/api/tasks/{task_id}')
async def task_update(task_id: int, payload: TaskEdit, db: DB, user: Auth):
    return svc.row(await svc.update_task(db, user.id, task_id, payload))


@app.post('/api/tasks/{task_id}/comments', status_code=201)
async def comment(task_id: int, payload: TextInput, db: DB, user: Auth):
    return svc.row(await svc.add_comment(db, user.id, task_id, payload.text))


@app.post('/api/tasks/{task_id}/checklist', status_code=201)
async def checklist_add(task_id: int, payload: TextInput, db: DB, user: Auth):
    task = await svc.get_task(db, user.id, task_id)
    if len(payload.text) > 500:
        raise HTTPException(422, 'Пункт не длиннее 500 символов')
    item = TaskChecklistItem(task_id=task_id, text=payload.text)
    db.add(item)
    await db.flush()
    await svc.activity(db, task.project_id, user.id, 'checklist_added', {'text': item.text}, task.id)
    return svc.row(item)


@app.patch('/api/tasks/{task_id}/checklist/{item_id}')
async def checklist_edit(task_id: int, item_id: int, payload: ChecklistEdit, db: DB, user: Auth):
    task = await svc.get_task(db, user.id, task_id)
    item = await db.get(TaskChecklistItem, item_id)
    if not item or item.task_id != task.id:
        raise HTTPException(404, 'Пункт не найден')
    item.done = payload.done
    await svc.activity(db, task.project_id, user.id, 'checklist_updated', {'item': item.id, 'done': item.done}, task.id)
    return svc.row(item)


@app.delete('/api/tasks/{task_id}/checklist/{item_id}')
async def checklist_delete(task_id: int, item_id: int, db: DB, user: Auth):
    task = await svc.get_task(db, user.id, task_id)
    item = await db.get(TaskChecklistItem, item_id)
    if not item or item.task_id != task.id:
        raise HTTPException(404, 'Пункт не найден')
    await db.delete(item)
    await svc.activity(db, task.project_id, user.id, 'checklist_deleted', {'item': item_id}, task.id)
    return {'ok': True}


def file_type(name, content):
    extension = Path(name).suffix.lower()
    formats = {'.png': ('image/png', b'\x89PNG\r\n\x1a\n'), '.jpg': ('image/jpeg', b'\xff\xd8\xff'),
               '.jpeg': ('image/jpeg', b'\xff\xd8\xff'), '.pdf': ('application/pdf', b'%PDF-'),
               '.webp': ('image/webp', b'RIFF')}
    if extension == '.txt':
        try:
            content.decode('utf-8')
        except UnicodeDecodeError:
            raise HTTPException(422, 'Текстовый файл должен быть UTF-8') from None
        return 'text/plain'
    if extension not in formats or not content.startswith(formats[extension][1]):
        raise HTTPException(422, 'Разрешены PNG, JPG, WEBP, PDF и TXT с корректным содержимым')
    if extension == '.webp' and content[8:12] != b'WEBP':
        raise HTTPException(422, 'Некорректный WEBP')
    return formats[extension][0]


@app.post('/api/tasks/{task_id}/attachments', status_code=201)
async def attachment_upload(task_id: int, db: DB, user: Auth, file: UploadFile = File()):
    task = await svc.get_task(db, user.id, task_id)
    content = await file.read(10 * 1024 * 1024 + 1)
    if not content or len(content) > 10 * 1024 * 1024:
        raise HTTPException(413, 'Размер файла должен быть от 1 байта до 10 МБ')
    name = (file.filename or 'file').replace('\\', '/').split('/')[-1][:255]
    name = ''.join(c for c in name if c.isprintable()) or 'file'
    mime = file_type(name, content)
    key = secrets.token_hex(24)
    path = Path(cfg.attachment_dir) / key
    await asyncio.to_thread(path.write_bytes, content)
    item = TaskAttachment(task_id=task_id, author_id=user.id, name=name, storage_key=key, content_type=mime, size=len(content))
    db.add(item)
    await db.flush()
    await svc.activity(db, task.project_id, user.id, 'attachment_added', {'name': name}, task.id)
    result = svc.row(item)
    result.pop('storage_key')
    return result


@app.get('/api/attachments/{attachment_id}')
async def attachment_download(attachment_id: int, db: DB, user: Auth):
    item = await db.get(TaskAttachment, attachment_id)
    if not item:
        raise HTTPException(404, 'Файл не найден')
    await svc.get_task(db, user.id, item.task_id)
    path = Path(cfg.attachment_dir) / item.storage_key
    if not path.is_file():
        raise HTTPException(404, 'Файл отсутствует в хранилище')
    return FileResponse(path, media_type='application/octet-stream', filename=item.name)


@app.get('/api/projects/{project_id}/members')
async def member_list(project_id: int, db: DB, user: Auth):
    return await svc.members(db, user.id, project_id)


@app.delete('/api/projects/{project_id}/members/{member_id}')
async def member_remove(project_id: int, member_id: int, db: DB, user: Auth):
    project = await svc.access(db, user.id, project_id, lock=True)
    if member_id == project.owner_id:
        raise HTTPException(422, 'Сначала передайте владение проектом')
    member = await db.scalar(select(ProjectMember).where(ProjectMember.project_id == project_id, ProjectMember.user_id == member_id))
    if not member:
        raise HTTPException(404, 'Участник не найден')
    await db.delete(member)
    await svc.activity(db, project_id, user.id, 'member_removed', {'user_id': member_id})
    return {'ok': True}


@app.post('/api/projects/{project_id}/invites')
async def invite_create(project_id: int, payload: InviteInput, db: DB, user: Auth):
    return await svc.create_invite(db, user.id, project_id, payload.target)


@app.post('/api/invites/{token}/accept')
async def invite_accept(token: str, db: DB, user: Auth):
    return svc.row(await svc.accept_invite(db, user, token))


@app.get('/api/projects/{project_id}/statuses')
async def status_list(project_id: int, db: DB, user: Auth):
    await svc.access(db, user.id, project_id)
    return [svc.row(s) for s in await svc.statuses(db, project_id)]


@app.post('/api/projects/{project_id}/statuses', status_code=201)
async def status_add(project_id: int, payload: StatusInput, db: DB, user: Auth):
    await svc.access(db, user.id, project_id, lock=True)
    existing = await svc.statuses(db, project_id)
    if len(existing) >= 100 or not payload.name.strip():
        raise HTTPException(422, 'Проверьте название и количество статусов')
    status = ProjectStatus(project_id=project_id, position=len(existing), **payload.model_dump())
    db.add(status)
    await db.flush()
    await svc.activity(db, project_id, user.id, 'status_created', {'name': status.name})
    return svc.row(status)


@app.patch('/api/projects/{project_id}/statuses/{status_id}')
async def status_edit(project_id: int, status_id: int, payload: StatusInput, db: DB, user: Auth):
    await svc.access(db, user.id, project_id, lock=True)
    status = await db.get(ProjectStatus, status_id)
    if not status or status.project_id != project_id:
        raise HTTPException(404, 'Статус не найден')
    if not payload.name.strip():
        raise HTTPException(422, 'Название не может быть пустым')
    status.name, status.is_done = payload.name, payload.is_done
    await svc.activity(db, project_id, user.id, 'status_updated', payload.model_dump())
    return svc.row(status)


@app.put('/api/projects/{project_id}/status-order')
async def status_order(project_id: int, payload: StatusOrder, db: DB, user: Auth):
    await svc.access(db, user.id, project_id, lock=True)
    existing = await svc.statuses(db, project_id)
    if len(payload.ids) != len(existing) or set(payload.ids) != {s.id for s in existing}:
        raise HTTPException(422, 'Передайте все статусы проекта без повторов')
    for status in existing:
        status.position = payload.ids.index(status.id)
    await svc.activity(db, project_id, user.id, 'statuses_reordered', {'ids': payload.ids})
    return {'ok': True}


@app.get('/api/projects/{project_id}/sprints')
async def sprint_list(project_id: int, db: DB, user: Auth):
    await svc.access(db, user.id, project_id)
    return [svc.row(s) for s in (await db.scalars(select(Sprint).where(Sprint.project_id == project_id).order_by(Sprint.start_date.desc()))).all()]


@app.post('/api/projects/{project_id}/sprints', status_code=201)
async def sprint_add(project_id: int, payload: SprintInput, db: DB, user: Auth):
    await svc.access(db, user.id, project_id)
    if payload.end_date < payload.start_date or not payload.name.strip():
        raise HTTPException(422, 'Проверьте даты и название спринта')
    sprint = Sprint(project_id=project_id, **payload.model_dump())
    db.add(sprint)
    await db.flush()
    await svc.activity(db, project_id, user.id, 'sprint_created', {'name': sprint.name})
    return svc.row(sprint)


@app.patch('/api/projects/{project_id}/sprints/{sprint_id}')
async def sprint_edit(project_id: int, sprint_id: int, payload: SprintEdit, db: DB, user: Auth):
    await svc.access(db, user.id, project_id)
    sprint = await db.scalar(select(Sprint).where(Sprint.id == sprint_id).with_for_update())
    if not sprint or sprint.project_id != project_id:
        raise HTTPException(404, 'Спринт не найден')
    if sprint.state == 'completed' or (payload.state == 'completed' and sprint.state != 'active'):
        raise HTTPException(409, 'Некорректный переход состояния спринта')
    sprint.state = payload.state
    tasks = (await db.scalars(select(Task).where(Task.sprint_id == sprint_id, Task.deleted_at.is_(None)))).all()
    done_ids = {s.id for s in await svc.statuses(db, project_id) if s.is_done}
    if sprint.state == 'completed':
        sprint.result = {'total': len(tasks), 'done': sum(t.status_id in done_ids for t in tasks), 'finished_at': utcnow().isoformat()}
    event = await svc.activity(db, project_id, user.id, f'sprint_{sprint.state}', {'name': sprint.name, 'result': sprint.result})
    await svc.notify(db, project_id, user.id, 'sprint', f'Спринт {sprint.name}: {sprint.state}', f'sprint:{event.id}')
    return svc.row(sprint)


@app.get('/api/timer')
async def timer_get(db: DB, user: Auth):
    entry = await svc.timer(db, user.id)
    return svc.timer_row(entry) if entry else None


@app.post('/api/timer/{task_id}/start')
async def timer_start(task_id: int, db: DB, user: Auth):
    return await svc.timer_action(db, user.id, 'start', task_id)


@app.post('/api/timer/pause')
async def timer_pause(db: DB, user: Auth):
    return await svc.timer_action(db, user.id, 'pause')


@app.post('/api/timer/stop')
async def timer_stop(db: DB, user: Auth):
    return await svc.timer_action(db, user.id, 'stop')


@app.post('/api/tasks/{task_id}/time')
async def time_add(task_id: int, payload: TimeInput, db: DB, user: Auth):
    return svc.row(await svc.manual_time(db, user.id, task_id, payload))


@app.get('/api/time')
async def time_list(db: DB, user: Auth):
    entries = (await db.execute(select(TimeEntry, Task.key, Task.title, Project.name).join(Task, Task.id == TimeEntry.task_id).join(Project, Project.id == Task.project_id).join(ProjectMember, ProjectMember.project_id == Project.id).where(
        ProjectMember.user_id == user.id, Project.deleted_at.is_(None)).order_by(TimeEntry.created_at.desc()).limit(1000))).all()
    return [dict(svc.timer_row(e), task_key=k, task_title=t, project_name=p) for e, k, t, p in entries]


@app.get('/api/projects/{project_id}/activity')
async def activity_list(project_id: int, db: DB, user: Auth):
    await svc.access(db, user.id, project_id)
    return [svc.row(e) for e in (await db.scalars(select(TaskActivity).where(TaskActivity.project_id == project_id).order_by(TaskActivity.created_at.desc()).limit(300))).all()]


@app.get('/api/projects/{project_id}/notifications')
async def notification_get(project_id: int, db: DB, user: Auth):
    await svc.access(db, user.id, project_id)
    pref = await db.scalar(select(NotificationSettings).where(NotificationSettings.project_id == project_id, NotificationSettings.user_id == user.id))
    return {event: pref.events.get(event, True) if pref else True for event in svc.EVENTS}


@app.put('/api/projects/{project_id}/notifications')
async def notification_set(project_id: int, payload: NotificationInput, db: DB, user: Auth):
    await svc.access(db, user.id, project_id)
    if set(payload.events) - set(svc.EVENTS):
        raise HTTPException(422, 'Неизвестный тип уведомления')
    from sqlalchemy.dialects.postgresql import insert
    await db.execute(insert(NotificationSettings).values(project_id=project_id, user_id=user.id, events=payload.events).on_conflict_do_update(
        index_elements=['project_id', 'user_id'], set_={'events': payload.events}))
    return payload.events


@app.get('/api/projects/{project_id}/repositories')
async def repositories(project_id: int, db: DB, user: Auth):
    await svc.access(db, user.id, project_id)
    repos = (await db.scalars(select(GitHubRepository).where(GitHubRepository.project_id == project_id))).all()
    return [{'id': r.id, 'full_name': r.full_name, 'webhook_url': cfg.app_url.rstrip('/') + f'/api/github/webhook/{r.id}'} for r in repos]


@app.post('/api/projects/{project_id}/repositories', status_code=201)
async def repository_add(project_id: int, payload: RepositoryInput, db: DB, user: Auth):
    await svc.access(db, user.id, project_id)
    headers = {'Accept': 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28'}
    if payload.token:
        headers['Authorization'] = f'Bearer {payload.token}'
    async with httpx.AsyncClient(timeout=15) as client:
        try:
            response = await client.get(f'https://api.github.com/repos/{payload.full_name}', headers=headers)
        except httpx.HTTPError:
            raise HTTPException(502, 'GitHub временно недоступен') from None
    if response.status_code != 200:
        raise HTTPException(422, 'Репозиторий недоступен. Проверьте имя и права токена.')
    canonical = response.json()['full_name']
    connection = GitHubConnection(project_id=project_id, user_id=user.id, encrypted_token=crypto.encrypt(payload.token.encode()).decode() if payload.token else None)
    db.add(connection)
    await db.flush()
    secret = secrets.token_urlsafe(32)
    repo = GitHubRepository(project_id=project_id, connection_id=connection.id, full_name=canonical, encrypted_secret=crypto.encrypt(secret.encode()).decode())
    db.add(repo)
    await db.flush()
    await svc.activity(db, project_id, user.id, 'repository_connected', {'full_name': canonical})
    return {'id': repo.id, 'full_name': repo.full_name, 'webhook_secret': secret,
            'webhook_url': cfg.app_url.rstrip('/') + f'/api/github/webhook/{repo.id}'}


@app.delete('/api/projects/{project_id}/repositories/{repo_id}')
async def repository_remove(project_id: int, repo_id: int, db: DB, user: Auth):
    await svc.access(db, user.id, project_id)
    repo = await db.get(GitHubRepository, repo_id)
    if not repo or repo.project_id != project_id:
        raise HTTPException(404, 'Репозиторий не найден')
    connection = await db.get(GitHubConnection, repo.connection_id)
    connection.encrypted_token = None
    repo.encrypted_secret = crypto.encrypt(secrets.token_urlsafe(48).encode()).decode()
    repo.full_name = f'disconnected/{repo.id}'
    await svc.activity(db, project_id, user.id, 'repository_disconnected', {'id': repo.id})
    return {'ok': True}


@app.post('/api/tasks/{task_id}/github')
async def github_link(task_id: int, payload: GitHubLinkInput, db: DB, user: Auth):
    task = await svc.get_task(db, user.id, task_id)
    kind = 'pr' if '/pull/' in payload.url else 'commit'
    link = GitHubTaskLink(task_id=task_id, kind=kind, title=payload.url, url=payload.url)
    db.add(link)
    await db.flush()
    await svc.activity(db, task.project_id, user.id, 'github_linked', {'url': payload.url}, task.id)
    return svc.row(link)


@app.post('/api/github/webhook/{repo_id}')
async def github_webhook(repo_id: int, request: Request, db: DB):
    repo = await db.scalar(select(GitHubRepository).where(GitHubRepository.id == repo_id).with_for_update())
    if not repo or repo.full_name.startswith('disconnected/'):
        raise HTTPException(404, 'Репозиторий не найден')
    raw = await request.body()
    if len(raw) > 2 * 1024 * 1024:
        raise HTTPException(413, 'Событие слишком большое')
    verify_github(raw, request.headers.get('x-hub-signature-256', ''), crypto.decrypt(repo.encrypted_secret.encode()).decode())
    delivery = request.headers.get('x-github-delivery', '')
    if not delivery or len(delivery) > 100:
        raise HTTPException(422, 'Не указан идентификатор доставки')
    if await db.scalar(select(WebhookDelivery.id).where(WebhookDelivery.repository_id == repo_id, WebhookDelivery.delivery_id == delivery)):
        return {'duplicate': True}
    try:
        data = json.loads(raw)
    except ValueError:
        raise HTTPException(422, 'Некорректный JSON') from None
    if data.get('repository', {}).get('full_name', '').lower() != repo.full_name.lower():
        raise HTTPException(422, 'Событие относится к другому репозиторию')
    project = await db.get(Project, repo.project_id)
    if project.deleted_at:
        raise HTTPException(404, 'Проект недоступен')
    db.add(WebhookDelivery(repository_id=repo_id, delivery_id=delivery))
    event_type = request.headers.get('x-github-event')
    items = []
    if event_type == 'push':
        items = [('commit', c.get('message', ''), c.get('url', ''), None) for c in data.get('commits', [])]
    elif event_type == 'pull_request':
        pr = data.get('pull_request', {})
        body = pr.get('body') or ''
        items = [('pr', (pr.get('title', '') + '\n' + body), pr.get('html_url', ''), 'merged' if pr.get('merged') else pr.get('state'))]
    linked = 0
    for kind, content, url, state in items:
        if not url.startswith(f'https://github.com/{repo.full_name}/'):
            continue
        keys = set(re.findall(r'\b[A-Z][A-Z0-9]{1,11}-\d+\b', content, re.I))
        for key in keys:
            task = await db.scalar(select(Task).where(Task.key == key.upper(), Task.project_id == repo.project_id, Task.deleted_at.is_(None)))
            if not task:
                continue
            link = await db.scalar(select(GitHubTaskLink).where(GitHubTaskLink.task_id == task.id, GitHubTaskLink.url == url))
            if link:
                link.state, link.title = state, content[:500]
            else:
                db.add(GitHubTaskLink(task_id=task.id, repository_id=repo_id, kind=kind, title=content[:500], url=url, state=state))
            await svc.activity(db, repo.project_id, None, 'github', {'url': url, 'kind': kind, 'state': state}, task.id)
            await svc.notify(db, repo.project_id, None, 'github', f'{task.key}: GitHub {kind}\n{content[:500]}\n{url}', f'github:{repo_id}:{delivery}:{task.id}:{hashlib.sha256(url.encode()).hexdigest()[:16]}')
            linked += 1
    return {'linked': linked}


static_dir = Path('/app/static')
if static_dir.is_dir():
    app.mount('/assets', StaticFiles(directory=static_dir / 'assets'), name='assets')

    @app.get('/')
    @app.get('/{path:path}', include_in_schema=False)
    async def frontend(path: str = ''):
        if path.startswith(('api/', 'health')):
            raise HTTPException(404, 'Не найдено')
        return FileResponse(static_dir / 'index.html', headers={'Cache-Control': 'no-cache'})
