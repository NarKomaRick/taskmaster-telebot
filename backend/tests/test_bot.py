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


async def test_personal_bot_capture_reminders_voice_and_replay(database, monkeypatch):
    from datetime import timedelta
    from app.models import PersonalTask, PersonalCapture, PersonalReminder, utcnow
    from app.personal_parser import parse_note
    from aiogram.types import Voice
    monkeypatch.setattr(bot_module, 'Session', database)
    transport = FakeTelegram()
    bot = Bot('123456:TESTONLY', session=transport)
    dispatcher = Dispatcher(storage=MemoryStorage())
    dispatcher.update.outer_middleware(bot_module.DatabaseMiddleware())
    dispatcher.include_router(bot_module.router)
    sender = TelegramUser(id=900000100, is_bot=False, first_name='Bot test')
    index = 0

    async def message(text=None, message_id=None, voice=False):
        nonlocal index
        index += 1
        msg = Message(message_id=message_id or index, date=datetime.now(timezone.utc), chat=Chat(id=sender.id, type='private'), from_user=sender,
                      text=text, voice=Voice(file_id='test-voice', file_unique_id='test-unique', duration=8, file_size=1000) if voice else None)
        await dispatcher.feed_update(bot, Update(update_id=index, message=msg))

    async def callback(data):
        nonlocal index
        index += 1
        msg = Message(message_id=100, date=datetime.now(timezone.utc), chat=Chat(id=sender.id, type='private'), text='Buttons')
        query = CallbackQuery(id=str(index), from_user=sender, chat_instance='test', message=msg, data=data)
        await dispatcher.feed_update(bot, Update(update_id=index, callback_query=query))

    await message('/start')
    await message('Через неделю сдать проект по вышмату', message_id=20)
    await message('Через неделю сдать проект по вышмату', message_id=20)
    async with database() as db:
        tasks = (await db.scalars(select(PersonalTask))).all()
        assert len(tasks) == 1 and tasks[0].due_at
        task_id, version = tasks[0].id, tasks[0].version
    assert any('Когда напомнить?' in text for text in transport.sent)
    await callback(f'pwhen:{task_id}:before1')
    assert any('Как часто повторять?' in text for text in transport.sent)
    stamp = int((utcnow()+timedelta(days=1)).timestamp())
    await callback(f'pfreq:{task_id}:{version}:{stamp}:86400')
    async with database() as db:
        reminder = await db.scalar(select(PersonalReminder))
        assert reminder.enabled and reminder.interval_seconds == 86400
    await callback(f'pdone:{task_id}')
    await callback(f'ptoggle:{task_id}:1')
    await callback(f'ptoggle:{task_id}:1')
    async with database() as db:
        assert not (await db.scalar(select(PersonalReminder))).enabled
        assert (await db.get(PersonalTask, task_id)).done_at
    await message(voice=True, message_id=30)
    await message(voice=True, message_id=30)
    async with database.begin() as db:
        captures = (await db.scalars(select(PersonalCapture).where(PersonalCapture.file_id.is_not(None)))).all()
        assert len(captures) == 1
        capture = captures[0]
        capture.state, capture.transcript = 'draft', 'Купить фильтр завтра'
        capture.draft = parse_note(capture.transcript)
        capture_id = capture.id
    await callback(f'vsave:{capture_id}')
    await callback(f'vsave:{capture_id}')
    async with database() as db:
        tasks = (await db.scalars(select(PersonalTask))).all()
        assert len(tasks) == 2
        assert tasks[1].title == 'Купить фильтр'
    original_request = transport.make_request
    async def fail_confirmation_once(bot, method, timeout=None):
        if isinstance(method, SendMessage) and 'Когда напомнить?' in method.text:
            transport.make_request = original_request
            raise OSError('Telegram temporarily offline')
        return await original_request(bot, method, timeout)
    transport.make_request = fail_confirmation_once
    await message('Дело должно сохраниться даже без ответа', message_id=60)
    async with database() as db:
        capture = await db.scalar(select(PersonalCapture).where(PersonalCapture.message_id == 60))
        assert capture.task_id
        assert (await db.get(PersonalTask, capture.task_id)).title == 'Дело должно сохраниться даже без ответа'
    await dispatcher.storage.close()
    await bot.session.close()
