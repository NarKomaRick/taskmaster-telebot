import asyncio
import logging
from datetime import datetime, timedelta
from html import escape
from contextvars import ContextVar
from zoneinfo import ZoneInfo
from aiogram import Bot, Dispatcher, F, Router, BaseMiddleware
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.redis import RedisStorage, RedisEventIsolation
from aiogram.types import BotCommand, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, MenuButtonWebApp, Message, WebAppInfo
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from .config import settings
from .db import Session
from .models import PersonalTask, PersonalCapture, utcnow
from . import service as svc, personal as pd
from .personal_parser import local_now, parse_note, parse_reminder_time

cfg = settings()
router = Router()
router.message.filter(F.chat.type == 'private')
current_database = ContextVar('bot_database', default=None)


class Flow(StatesGroup):
    when = State()
    due = State()
    voice_edit = State()
    title = State()


def keyboard(rows):
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=label, callback_data=value) for label, value in row] for row in rows])


def home_keyboard():
    result = keyboard([[('Сегодня', 'plist:today:0'), ('Все дела', 'plist:all:0')]])
    if cfg.app_url:
        result.inline_keyboard.append([InlineKeyboardButton(text='Открыть список', web_app=WebAppInfo(url=cfg.app_url))])
    return result


class DatabaseMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        sender = data.get('event_from_user')
        if not sender or sender.is_bot:
            return
        try:
            async with Session() as db:
                token = current_database.set(db)
                try:
                    user = await svc.register(db, sender.model_dump())
                    data.update(db=db, user=user)
                    result = await handler(event, data)
                    await db.commit()
                    return result
                finally:
                    current_database.reset(token)
        except (HTTPException, ValidationError, ValueError) as error:
            text = str(error.detail) if isinstance(error, HTTPException) else 'Проверьте текст, дату и время.'
            inner = event.callback_query or event.message
            if isinstance(inner, CallbackQuery):
                await inner.answer(text[:180], show_alert=True)
            elif inner:
                await inner.answer(text)
        except Exception:
            logging.exception('Bot update failed')
            inner = event.callback_query or event.message
            if isinstance(inner, CallbackQuery):
                await inner.answer('Не удалось завершить действие. Проверьте список дел.', show_alert=True)
            elif inner:
                await inner.answer('Не удалось завершить действие. Проверьте список дел.')


async def send(event, text, markup=None):
    db = current_database.get()
    if db is not None:
        await db.commit()
    if isinstance(event, CallbackQuery):
        await event.answer()
        await event.message.answer(text, reply_markup=markup)
    else:
        await event.answer(text, reply_markup=markup)


async def task_card(event, task, db):
    schedule = await pd.reminder(db, task.id)
    extra = ''
    if schedule and schedule.enabled:
        when = schedule.next_at.astimezone(ZoneInfo(cfg.timezone)).strftime('%d.%m в %H:%M')
        extra = f'\nНапомню: {when}\n{pd.FREQUENCIES[schedule.interval_seconds]}'
    rows = [[('Вернуть в дела' if task.done_at else 'Готово', f'ptoggle:{task.id}:{int(not bool(task.done_at))}')],
            [('Напоминание', f'premind:{task.id}'), ('Срок', f'pdue:{task.id}')],
            [('Исправить текст', f'ptitle:{task.id}'), ('Удалить', f'pdelete:{task.id}')]]
    await send(event, f'<b>{escape(task.title)}</b>\nСрок: {pd.due_label(task)}{extra}', keyboard(rows))


async def ask_when(event, task):
    rows = [[('Через час', f'pwhen:{task.id}:hour'), ('Завтра в 09:00', f'pwhen:{task.id}:tomorrow')]]
    if task.due_at:
        rows += [[('За день до срока, 09:00', f'pwhen:{task.id}:before1')],
                 [('За 3 дня, 09:00', f'pwhen:{task.id}:before3'), ('В день срока, 09:00', f'pwhen:{task.id}:due')]]
    rows += [[('Своя дата и время', f'pwhen:{task.id}:custom')], [('Без напоминания', f'pmute:{task.id}')]]
    await send(event, f'<b>{escape(task.title)}</b>\nСрок: {pd.due_label(task)}\n\nКогда напомнить? Время московское.', keyboard(rows))


async def ask_frequency(event, task, when):
    if when <= local_now():
        await send(event, 'Это время уже прошло. Выберите другое напоминание.')
        return await ask_when(event.message if isinstance(event, CallbackQuery) else event, task)
    stamp = int(when.timestamp())
    rows = [[(label, f'pfreq:{task.id}:{task.version}:{stamp}:{seconds}')] for seconds, label in pd.FREQUENCIES.items()]
    await send(event, f'Первое напоминание: {when.strftime("%d.%m.%Y в %H:%M")}\nКак часто повторять?', keyboard(rows))


@router.message(CommandStart())
@router.message(Command('help', 'cancel'))
async def start(message: Message, state: FSMContext):
    await state.clear()
    await message.answer('Просто напишите или отправьте голосовое, что нужно сделать.\n\nНапример: «Через неделю сдать проект по вышмату». Я запишу дело и помогу выбрать напоминания.\n\nВремя: Москва. Голосовые до 2 минут.', reply_markup=home_keyboard())


@router.message(Command('new'))
async def new(message: Message, state: FSMContext):
    await state.clear()
    await message.answer('Напишите дело или отправьте голосовое. Можно сразу сказать срок.')


async def show_list(event, db, user, group='all', page=0):
    tasks = await pd.listing(db, user.id)
    day = local_now().date()
    tasks = [t for t in tasks if not t['done_at'] and (group != 'today' or t['bucket'] == 'today' or (t['due_at'] and datetime.fromisoformat(t['due_at']).astimezone(ZoneInfo(cfg.timezone)).date() <= day))]
    rows = [[(t['title'][:60], f'popen:{t["id"]}')] for t in tasks[page*12:(page+1)*12]]
    nav = []
    if page: nav.append(('Назад', f'plist:{group}:{page-1}'))
    if len(tasks) > (page+1)*12: nav.append(('Дальше', f'plist:{group}:{page+1}'))
    if nav: rows.append(nav)
    await send(event, ('Сегодня' if group == 'today' else 'Все дела') + (f' · {len(tasks)}' if tasks else '\nПока пусто. Отправьте дело текстом или голосом.'), keyboard(rows) if rows else home_keyboard())


@router.message(Command('tasks', 'today', 'projects', 'timer'))
async def list_command(message: Message, db, user, state: FSMContext):
    await state.clear()
    await show_list(message, db, user, 'today' if message.text.startswith('/today') else 'all')


@router.callback_query(F.data.startswith('plist:'))
async def list_callback(callback: CallbackQuery, db, user, state: FSMContext):
    await state.clear()
    _, group, page = callback.data.split(':')
    await show_list(callback, db, user, group, max(0, int(page)))


@router.callback_query(F.data.startswith('popen:'))
async def open_callback(callback: CallbackQuery, db, user):
    await task_card(callback, await pd.get(db, user.id, int(callback.data.split(':')[1])), db)


@router.callback_query(F.data.startswith('premind:'))
async def remind(callback: CallbackQuery, db, user, state: FSMContext):
    await state.clear()
    task = await pd.get(db, user.id, int(callback.data.split(':')[1]))
    if task.done_at or task.deleted_at:
        raise HTTPException(409, 'Дело уже выполнено или удалено')
    await ask_when(callback, task)


@router.callback_query(F.data.startswith('pwhen:'))
async def when_choice(callback: CallbackQuery, state: FSMContext, db, user):
    _, task_id, choice = callback.data.split(':')
    task = await pd.get(db, user.id, int(task_id))
    if choice == 'custom':
        await state.set_state(Flow.when)
        await state.set_data({'task_id': task.id, 'version': task.version})
        return await send(callback, 'Когда напомнить? Например: «завтра в 18:00» или «25 октября в 09:00». /cancel отменяет ввод.')
    await state.clear()
    now = local_now()
    if choice == 'hour': when = now + timedelta(hours=1)
    elif choice == 'tomorrow': when = (now + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
    elif task.due_at and choice in ('before1', 'before3', 'due'):
        when = task.due_at.astimezone(ZoneInfo(cfg.timezone)).replace(hour=9, minute=0, second=0, microsecond=0) - timedelta(days={'before1':1,'before3':3,'due':0}[choice])
    else: raise HTTPException(422, 'Выберите время заново')
    await ask_frequency(callback, task, when)


@router.message(Flow.when, F.text)
async def custom_when(message: Message, state: FSMContext, db, user):
    try: when = parse_reminder_time(message.text or '')
    except ValueError as error: return await message.answer(str(error))
    data = await state.get_data()
    task = await pd.get(db, user.id, data['task_id'])
    await state.clear()
    await ask_frequency(message, task, when)


@router.callback_query(F.data.startswith('pfreq:'))
async def frequency(callback: CallbackQuery, db, user, state: FSMContext):
    _, task_id, version, stamp, seconds = callback.data.split(':')
    await pd.set_reminder(db, user.id, int(task_id), pd.ReminderInput(version=int(version), next_at=datetime.fromtimestamp(int(stamp), ZoneInfo(cfg.timezone)), interval_seconds=int(seconds)))
    await state.clear()
    await send(callback, 'Готово, напоминание настроено. Повторы закончатся, когда отметите дело выполненным.', home_keyboard())


@router.callback_query(F.data.startswith('pmute:') | F.data.startswith('psnooze:') | F.data.startswith('pdone:') | F.data.startswith('ptoggle:'))
async def quick_action(callback: CallbackQuery, db, user, state: FSMContext):
    parts = callback.data.split(':')
    action, task_id = parts[0], int(parts[1])
    task = await pd.get(db, user.id, task_id, lock=True)
    if action == 'pmute': await pd.cancel_reminder(db, user.id, task_id)
    elif action == 'psnooze':
        old = await pd.reminder(db, task_id)
        await pd.set_reminder(db, user.id, task_id, pd.ReminderInput(version=task.version, next_at=utcnow()+timedelta(hours=1), interval_seconds=old.interval_seconds if old else 0))
    else: await pd.edit(db, user.id, task_id, pd.PersonalEdit(version=task.version, done=bool(int(parts[2])) if action == 'ptoggle' else True))
    await state.clear()
    await send(callback, {'pmute':'Напоминания отключены.', 'psnooze':'Напомню через час.'}.get(action, 'Сохранено.'), home_keyboard())


@router.callback_query(F.data.startswith('pdelete:'))
async def delete_prompt(callback: CallbackQuery):
    task_id = int(callback.data.split(':')[1])
    await send(callback, 'Удалить это дело?', keyboard([[('Удалить', f'pdeleted:{task_id}'), ('Оставить', f'popen:{task_id}')]]))


@router.callback_query(F.data.startswith('pdeleted:'))
async def delete_task(callback: CallbackQuery, db, user):
    task = await pd.get(db, user.id, int(callback.data.split(':')[1]))
    await pd.edit(db, user.id, task.id, pd.PersonalEdit(version=task.version, deleted=True))
    await send(callback, 'Дело удалено, напоминания остановлены.', home_keyboard())


@router.callback_query(F.data.startswith('pdue:') | F.data.startswith('ptitle:'))
async def edit_prompt(callback: CallbackQuery, db, user, state: FSMContext):
    action, task_id = callback.data.split(':'); task = await pd.get(db, user.id, int(task_id))
    await state.set_state(Flow.due if action == 'pdue' else Flow.title)
    await state.set_data({'task_id': task.id, 'version': task.version})
    await send(callback, 'Введите срок, например: «через неделю», «25 октября в 18:00». «Нет» убирает срок.' if action == 'pdue' else 'Напишите новое название дела.')


@router.message(Flow.due, F.text)
@router.message(Flow.title, F.text)
async def edit_input(message: Message, state: FSMContext, db, user):
    data = await state.get_data()
    if await state.get_state() == Flow.due.state:
        parsed = parse_note(message.text or '')
        if (message.text or '').strip().lower() != 'нет' and (not parsed['due_at'] or parsed['needs_date']):
            return await message.answer('Не удалось понять срок. Напишите, например: «25 октября в 18:00».')
        payload = pd.PersonalEdit(version=data['version'], due_at=parsed['due_at'], due_has_time=parsed['due_has_time'])
    else: payload = pd.PersonalEdit(version=data['version'], title=(message.text or '').strip())
    task = await pd.edit(db, user.id, data['task_id'], payload)
    await state.clear()
    await task_card(message, task, db)


@router.message(F.voice)
async def voice(message: Message, db, user, state: FSMContext):
    await state.clear()
    if message.voice.duration > 120 or (message.voice.file_size or 0) > 5*1024*1024:
        return await message.answer('Пришлите голосовое до 2 минут и 5 МБ.')
    await db.execute(insert(PersonalCapture).values(user_id=user.id, message_id=message.message_id, file_id=message.voice.file_id, state='queued', created_at=message.date).on_conflict_do_nothing())
    await db.commit()
    await message.answer('Слушаю голосовое. Покажу текст и срок для проверки.')


@router.callback_query(F.data.startswith('vsave:') | F.data.startswith('vedit:') | F.data.startswith('vcancel:'))
async def voice_choice(callback: CallbackQuery, state: FSMContext, db, user):
    action, capture_id = callback.data.split(':')
    capture = await db.scalar(select(PersonalCapture).where(PersonalCapture.id == int(capture_id), PersonalCapture.user_id == user.id).with_for_update())
    if not capture: raise HTTPException(404, 'Голосовое не найдено')
    if capture.task_id: return await task_card(callback, await pd.get(db, user.id, capture.task_id), db)
    if capture.state != 'draft': raise HTTPException(409, 'Это голосовое уже закрыто или ещё обрабатывается')
    if action == 'vcancel':
        capture.state = 'cancelled'
        await state.clear()
        return await send(callback, 'Отменено. Можно прислать другое дело.')
    if action == 'vedit':
        await state.set_state(Flow.voice_edit); await state.set_data({'capture_id': capture.id})
        return await send(callback, 'Напишите правильный текст дела со сроком.')
    task = await pd.create(db, user.id, capture.transcript, capture.draft)
    capture.task_id, capture.state = task.id, 'saved'
    await state.clear()
    await ask_when(callback, task)


@router.message(Flow.voice_edit, F.text)
async def correct_voice(message: Message, state: FSMContext, db, user):
    data = await state.get_data()
    capture = await db.scalar(select(PersonalCapture).where(PersonalCapture.id == data['capture_id'], PersonalCapture.user_id == user.id).with_for_update())
    if not capture or capture.state != 'draft': raise HTTPException(409, 'Голосовое уже закрыто')
    parsed = parse_note(message.text or '')
    task = await pd.create(db, user.id, message.text or '', parsed)
    capture.transcript, capture.draft, capture.task_id, capture.state = message.text, parsed, task.id, 'saved'
    await state.clear()
    await ask_when(message, task)


@router.message(F.text)
async def quick_text(message: Message, state: FSMContext, db, user):
    await state.clear()
    text = message.text.strip()
    if text.startswith('/'):
        return await message.answer('Просто отправьте дело текстом или голосом.', reply_markup=home_keyboard())
    task = await pd.capture_text(db, user.id, message.message_id, text, message.date)
    if parse_note(text)['needs_date']:
        await message.answer('Дело записано, но срок неоднозначен. Его можно уточнить кнопкой «Срок».')
        return await task_card(message, task, db)
    await ask_when(message, task)


@router.callback_query()
async def old_button(callback: CallbackQuery):
    await send(callback, 'Теперь это личный список дел. Напишите задачу или отправьте голосовое.', home_keyboard())


async def main():
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s %(message)s')
    bot = Bot(cfg.bot_token, session=AiohttpSession(proxy=cfg.bot_proxy or None, timeout=20), default=DefaultBotProperties(parse_mode='HTML'))
    storage = RedisStorage.from_url(cfg.redis_url, state_ttl=86400, data_ttl=86400)
    dp = Dispatcher(storage=storage, events_isolation=RedisEventIsolation.from_url(cfg.redis_url))
    dp.update.outer_middleware(DatabaseMiddleware()); dp.include_router(router)
    try:
        await bot.set_my_commands([BotCommand(command=n, description=d) for n,d in [('start','Как пользоваться'),('today','На сегодня'),('tasks','Все дела'),('new','Добавить дело'),('cancel','Отменить ввод')]])
        if cfg.app_url: await bot.set_chat_menu_button(menu_button=MenuButtonWebApp(text='Мои дела', web_app=WebAppInfo(url=cfg.app_url)))
        await dp.start_polling(bot, allowed_updates=['message','callback_query'], handle_as_tasks=False)
    finally:
        await bot.session.close(); await storage.close()


if __name__ == '__main__':
    asyncio.run(main())
