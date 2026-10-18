from datetime import datetime
from zoneinfo import ZoneInfo
from fastapi import HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from .config import settings
from .models import PersonalTask, PersonalReminder, PersonalCapture, utcnow
from .personal_parser import parse_note, local_now
from .service import row

FREQUENCIES = {0: 'Один раз', 3600: 'Каждый час', 86400: 'Каждый день', 259200: 'Каждые 3 дня', 604800: 'Каждую неделю'}


class CaptureInput(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


class PersonalEdit(BaseModel):
    version: int = Field(ge=1)
    title: str | None = Field(default=None, min_length=1, max_length=500)
    note: str | None = Field(default=None, max_length=10000)
    due_at: datetime | None = None
    due_has_time: bool | None = None
    bucket: str | None = None
    done: bool | None = None
    deleted: bool | None = None
    reminder_enabled: bool | None = None
    reminder_at: datetime | None = None
    reminder_interval: int = 0


class ReminderInput(BaseModel):
    version: int = Field(ge=1)
    next_at: datetime
    interval_seconds: int = 0


async def get(db, user_id, task_id, lock=False):
    query = select(PersonalTask).where(PersonalTask.id == task_id, PersonalTask.user_id == user_id)
    if lock:
        query = query.with_for_update()
    task = await db.scalar(query)
    if not task:
        raise HTTPException(404, 'Дело не найдено')
    return task


async def reminder(db, task_id):
    return await db.scalar(select(PersonalReminder).where(PersonalReminder.task_id == task_id))


async def details(db, user_id, task_id):
    task = await get(db, user_id, task_id)
    schedule = await reminder(db, task_id)
    return {**row(task), 'reminder': row(schedule) if schedule else None}


async def create(db, user_id, text, parsed=None):
    if not text.strip():
        raise HTTPException(422, 'Напишите, что нужно сделать')
    parsed = parsed or parse_note(text)
    due = datetime.fromisoformat(parsed['due_at']) if parsed.get('due_at') else None
    task = PersonalTask(user_id=user_id, title=parsed['title'], due_at=due, due_has_time=parsed.get('due_has_time', False),
                        bucket='later' if due and due.date() > local_now().date() else 'inbox', note=text if parsed.get('needs_date') or len(text) > 500 else '')
    db.add(task)
    await db.flush()
    return task


async def capture_text(db, user_id, message_id, text, received=None):
    await db.execute(insert(PersonalCapture).values(user_id=user_id, message_id=message_id, transcript=text, state='saved').on_conflict_do_nothing())
    capture = await db.scalar(select(PersonalCapture).where(PersonalCapture.user_id == user_id, PersonalCapture.message_id == message_id).with_for_update())
    if capture.task_id:
        return await get(db, user_id, capture.task_id)
    task = await create(db, user_id, text, parse_note(text, received))
    capture.task_id = task.id
    return task


async def edit(db, user_id, task_id, payload):
    task = await get(db, user_id, task_id, lock=True)
    if task.version != payload.version:
        raise HTTPException(409, 'Дело уже изменилось. Откройте его заново.')
    values = payload.model_dump(exclude_unset=True, exclude={'version', 'reminder_enabled', 'reminder_at', 'reminder_interval'})
    for name, value in values.items():
        if name in ('title', 'note') and value is None:
            raise HTTPException(422, 'Поле не может быть пустым')
        if name == 'title':
            value = value.strip()
            if not value:
                raise HTTPException(422, 'Напишите название дела')
        if name == 'bucket' and value not in ('inbox', 'today', 'later'):
            raise HTTPException(422, 'Неизвестный список')
        if name == 'due_at' and value is not None and value.tzinfo is None:
            raise HTTPException(422, 'Для даты нужен часовой пояс')
        if name == 'due_has_time' and value is None:
            raise HTTPException(422, 'Укажите, задано ли время')
        if name in ('done', 'deleted'):
            name = 'done_at' if name == 'done' else 'deleted_at'
            value = utcnow() if value else None
        setattr(task, name, value)
    task.version += 1
    task.updated_at = utcnow()
    if task.done_at or task.deleted_at:
        schedule = await reminder(db, task_id)
        if schedule:
            schedule.enabled = False
    await db.flush()
    if payload.reminder_enabled is True:
        if payload.reminder_at is None:
            raise HTTPException(422, 'Выберите время напоминания')
        await set_reminder(db, user_id, task_id, ReminderInput(version=task.version, next_at=payload.reminder_at, interval_seconds=payload.reminder_interval))
    elif payload.reminder_enabled is False:
        await cancel_reminder(db, user_id, task_id)
    return task


async def set_reminder(db, user_id, task_id, payload):
    task = await get(db, user_id, task_id, lock=True)
    if task.version != payload.version:
        raise HTTPException(409, 'Дело уже изменилось. Откройте его заново.')
    if task.done_at or task.deleted_at:
        raise HTTPException(409, 'Дело уже выполнено или удалено')
    if payload.next_at.tzinfo is None or payload.next_at <= utcnow():
        raise HTTPException(422, 'Выберите будущее время с часовым поясом')
    if payload.interval_seconds not in FREQUENCIES:
        raise HTTPException(422, 'Неизвестная частота повторения')
    schedule = await reminder(db, task_id)
    if not schedule:
        schedule = PersonalReminder(task_id=task.id, next_at=payload.next_at)
        db.add(schedule)
    schedule.next_at, schedule.interval_seconds = payload.next_at, payload.interval_seconds
    schedule.enabled, schedule.attempts, schedule.retry_at = True, 0, None
    task.version += 1
    task.updated_at = utcnow()
    await db.flush()
    return schedule


async def cancel_reminder(db, user_id, task_id):
    task = await get(db, user_id, task_id, lock=True)
    schedule = await reminder(db, task_id)
    if schedule:
        schedule.enabled = False
    task.version += 1
    return task


async def listing(db, user_id):
    pairs = (await db.execute(select(PersonalTask, PersonalReminder).outerjoin(PersonalReminder, PersonalReminder.task_id == PersonalTask.id).where(
        PersonalTask.user_id == user_id, PersonalTask.deleted_at.is_(None)).order_by(PersonalTask.created_at.desc()).limit(2000))).all()
    return [{**row(t), 'reminder': row(r) if r else None} for t, r in pairs]


def due_label(task):
    if not task.due_at:
        return 'Без срока'
    value = task.due_at.astimezone(ZoneInfo(settings().timezone))
    return value.strftime('%d.%m.%Y %H:%M' if task.due_has_time else '%d.%m.%Y')
