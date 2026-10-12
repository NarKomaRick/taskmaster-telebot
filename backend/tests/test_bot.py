from datetime import datetime, timezone
from aiogram import Bot, Dispatcher
from aiogram.client.session.base import BaseSession
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.methods import SendMessage
from aiogram.types import CallbackQuery, Chat, Message, Update, User as TelegramUser
from sqlalchemy import select
from app.models import Project, Task
import app.bot as bot_module


class FakeTelegram(BaseSession):
    def __init__(self):
        super().__init__()
        self.sent = []

    async def close(self):
        pass

    async def make_request(self, bot, method, timeout=None):
        if isinstance(method, SendMessage):
            self.sent.append(method.text)
            return Message(message_id=len(self.sent), date=datetime.now(timezone.utc), chat=Chat(id=method.chat_id, type='private'), text=method.text)
        return True

    async def stream_content(self, url, headers=None, timeout=30, chunk_size=65536, raise_for_status=True):
        yield b''


async def test_bot_registration_project_create_confirmation_and_status(database, monkeypatch):
    monkeypatch.setattr(bot_module, 'Session', database)
    transport = FakeTelegram()
    bot = Bot('123456:TESTONLY', session=transport)
    dispatcher = Dispatcher(storage=MemoryStorage())
    dispatcher.update.outer_middleware(bot_module.DatabaseMiddleware())
    dispatcher.include_router(bot_module.router)
    sender = TelegramUser(id=900000100, is_bot=False, first_name='Bot test')
    index = 0

    async def message(text):
        nonlocal index
        index += 1
        msg = Message(message_id=index, date=datetime.now(timezone.utc), chat=Chat(id=sender.id, type='private'), from_user=sender, text=text)
        await dispatcher.feed_update(bot, Update(update_id=index, message=msg))

    async def callback(data):
        nonlocal index
        index += 1
        msg = Message(message_id=100, date=datetime.now(timezone.utc), chat=Chat(id=sender.id, type='private'), text='Buttons')
        query = CallbackQuery(id=str(index), from_user=sender, chat_instance='test', message=msg, data=data)
        await dispatcher.feed_update(bot, Update(update_id=index, callback_query=query))

    await message('/start')
    await callback('project_new')
    await message('BOT Bot project')
    async with database() as db:
        project = await db.scalar(select(Project))
        assert project is not None
        project_id = project.id
    await message('Создай задачу проверить бот завтра в проект BOT')
    async with database() as db:
        assert not (await db.scalars(select(Task))).all()
    assert any('Проверьте задачу' in text for text in transport.sent)
    await callback(f'confirm_create:{project_id}')
    await callback(f'confirm_create:{project_id}')
    async with database() as db:
        tasks = (await db.scalars(select(Task))).all()
        assert len(tasks) == 1
        task = tasks[0]
        assert task.key == 'BOT-1' and task.title == 'проверить бот'
        task_id = task.id
    await callback(f'set:priority:{task_id}:1:Critical')
    async with database() as db:
        task = await db.get(Task, task_id)
        assert task.priority == 'Critical' and task.version == 2
    await dispatcher.storage.close()
    await bot.session.close()
