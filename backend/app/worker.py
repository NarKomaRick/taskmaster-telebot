import asyncio
import logging
from datetime import timedelta
import httpx
from sqlalchemy import select
from .config import settings
from .db import Session
from .models import Notification, Project, ProjectStatus, ProjectMember, Task, User, utcnow
from . import service as svc


class DeliveryForbidden(Exception):
    pass


class DeliveryRetry(Exception):
    def __init__(self, seconds):
        self.retry_after = seconds


class TelegramDelivery:
    def __init__(self):
        self.client = httpx.AsyncClient(timeout=20, proxy=settings().bot_proxy or None)

    async def send_message(self, telegram_id, text, reply_markup=None):
        payload = {'chat_id': telegram_id, 'text': text}
        if reply_markup:
            payload['reply_markup'] = reply_markup
        response = await self.client.post(f'https://api.telegram.org/bot{settings().bot_token}/sendMessage',
                                          json=payload)
        if response.status_code == 403:
            raise DeliveryForbidden()
        if response.status_code == 429:
            raise DeliveryRetry(response.json().get('parameters', {}).get('retry_after', 60))
        if response.status_code != 200 or not response.json().get('ok'):
            raise RuntimeError('Telegram delivery failed')

    async def close(self):
        await self.client.aclose()


async def schedule_deadlines():
    day = svc.today()
    async with Session.begin() as db:
        tasks = (await db.scalars(select(Task).join(ProjectStatus, ProjectStatus.id == Task.status_id).join(Project, Project.id == Task.project_id).where(
            Task.deleted_at.is_(None), Project.deleted_at.is_(None), ProjectStatus.is_done.is_(False), Task.deadline <= day + timedelta(days=1)))).all()
        for task in tasks:
            kind = 'overdue' if task.deadline < day else 'deadline'
            await svc.notify(db, task.project_id, None, kind,
                f'{task.key} · {task.title}\n' + ('Просрочено' if kind == 'overdue' else 'Приближается дедлайн') + f': {task.deadline}',
                f'{kind}:{task.id}:{task.deadline}:{day}')


async def deliver_one(bot):
    async with Session.begin() as db:
        entry = await db.scalar(select(Notification).where(Notification.sent_at.is_(None), Notification.failed.is_(False),
            Notification.available_at <= utcnow()).order_by(Notification.id).with_for_update(skip_locked=True).limit(1))
        if not entry:
            return False
        user = await db.get(User, entry.user_id)
        member = await db.scalar(select(ProjectMember.id).where(ProjectMember.project_id == entry.project_id, ProjectMember.user_id == entry.user_id))
        project = await db.get(Project, entry.project_id)
        if not member or project.deleted_at:
            entry.failed = True
            return True
        try:
            await bot.send_message(user.telegram_id, entry.text)
            entry.sent_at = utcnow()
        except DeliveryForbidden:
            entry.failed = True
        except DeliveryRetry as error:
            entry.available_at = utcnow() + timedelta(seconds=error.retry_after + 1)
        except Exception:
            entry.attempts += 1
            entry.failed = entry.attempts >= 12
            entry.available_at = utcnow() + timedelta(seconds=min(3600, 2 ** entry.attempts * 10))
            logging.warning('Notification %s delivery failed, attempt %s', entry.id, entry.attempts)
        return True


async def main():
    from .personal_delivery import deliver_personal
    logging.basicConfig(level=logging.INFO)
    logging.getLogger('httpx').setLevel(logging.WARNING)
    logging.getLogger('httpcore').setLevel(logging.WARNING)
    bot = TelegramDelivery()
    try:
        while True:
            try:
                sent = await deliver_personal(bot)
                if not await deliver_one(bot) and not sent:
                    await asyncio.sleep(3)
            except Exception:
                logging.exception('Worker cycle failed')
                await asyncio.sleep(10)
    finally:
        await bot.close()


if __name__ == '__main__':
    asyncio.run(main())
