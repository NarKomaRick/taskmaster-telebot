import asyncio
import logging
from datetime import date
from html import escape
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
from .config import settings
from .db import Session
from . import service as svc
from .models import Task
from .parser import parse_command
from .schemas import ProjectCreate, TaskCreate, TaskEdit

cfg = settings()
router = Router()


class Flow(StatesGroup):
    project = State()
    title = State()
    deadline = State()
    confirm = State()


def keyboard(rows):
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=label, callback_data=value) for label, value in row] for row in rows])


def home_keyboard():
    rows = [[('Проекты', 'projects'), ('Мои задачи', 'tasks:0')], [('Сегодня', 'today'), ('Новая задача', 'new')], [('Таймер', 'timer'), ('Помощь', 'help')]]
    result = keyboard(rows)
    if cfg.app_url:
        result.inline_keyboard.append([InlineKeyboardButton(text='Открыть TaskMaster', web_app=WebAppInfo(url=cfg.app_url))])
    return result


class DatabaseMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        user = data.get('event_from_user')
        if not user or user.is_bot:
            return
        try:
            async with Session() as db:
                async with db.begin():
                    registered = await svc.register(db, user.model_dump())
                    data.update(db=db, user=registered)
                    return await handler(event, data)
        except (HTTPException, ValidationError) as error:
            message = str(error.detail) if isinstance(error, HTTPException) else 'Проверьте данные: название, ключ проекта и дату.'
            inner = getattr(event, 'message', None) or getattr(event, 'callback_query', None)
            if isinstance(inner, CallbackQuery):
                await inner.answer(message[:180], show_alert=True)
            elif isinstance(inner, Message):
                await inner.answer(message)
        except Exception:
            logging.exception('Bot update failed')
            inner = getattr(event, 'message', None) or getattr(event, 'callback_query', None)
            if isinstance(inner, CallbackQuery):
                await inner.answer('Не удалось сохранить. Повторите действие.', show_alert=True)
            elif isinstance(inner, Message):
                await inner.answer('Не удалось сохранить. Повторите действие.')


async def send(event, text, reply_markup=None):
    if isinstance(event, CallbackQuery):
        await event.answer()
        await event.message.answer(text, reply_markup=reply_markup)
    else:
        await event.answer(text, reply_markup=reply_markup)


HELP = ('<b>TaskMaster</b>\n\n/projects - проекты и создание проекта\n/tasks - задачи выбранного проекта\n/today - дедлайны сегодня\n/new - новая задача\n/timer - учёт времени\n/cancel - отменить ввод\n\n'
        '<b>Текстовые команды</b>\nСоздай задачу исправить авторизацию завтра\nДобавь задачу обновить API в проект Altair\nПокажи мои задачи на сегодня\nЗаверши ALT-12\nНачни работу над KDR-8\n\n'
        'Даты: сегодня, завтра, послезавтра, ГГГГ-ММ-ДД, ДД.ММ.ГГГГ. Дата стоит в конце заголовка, перед «в проект ...». Перед сохранением показываю подтверждение.')


@router.message(CommandStart())
async def start(message: Message, state: FSMContext, db, user):
    await state.clear()
    value = (message.text or '').split(maxsplit=1)
    if len(value) == 2 and value[1].startswith('invite_'):
        project = await svc.accept_invite(db, user, value[1][7:])
        await message.answer(f'Вы присоединились к проекту {escape(project.name)}.')
    await message.answer('Задачи, проекты и время в одном месте. Начните с создания проекта.', reply_markup=home_keyboard())


@router.message(Command('cancel'))
async def cancel(message: Message, state: FSMContext):
    await state.clear()
    await message.answer('Ввод отменён.', reply_markup=home_keyboard())


async def show_projects(event, db, user):
    items = await svc.projects(db, user.id)
    rows = [[(f'{p.key} · {p.name}', f'project:{p.id}')] for p in items[:80]]
    rows += [[('Создать проект', 'project_new')], [('Главная', 'home')]]
    await send(event, 'Выберите проект:' if items else 'Пока нет проектов. Создайте первый.', keyboard(rows))


@router.message(Command('projects'))
async def projects_command(message: Message, db, user, state: FSMContext):
    await state.clear()
    await show_projects(message, db, user)


@router.callback_query(F.data == 'projects')
async def projects_callback(callback: CallbackQuery, db, user, state: FSMContext):
    await state.clear()
    await show_projects(callback, db, user)


@router.callback_query(F.data == 'project_new')
async def project_prompt(callback: CallbackQuery, state: FSMContext):
    await state.set_state(Flow.project)
    await send(callback, 'Введите ключ и название проекта, например:\nALT Altair\n\nКлюч: 2-12 латинских букв или цифр, первая буква. /cancel отменяет ввод.')


@router.message(Flow.project)
async def project_save(message: Message, state: FSMContext, db, user):
    values = (message.text or '').split(maxsplit=1)
    if len(values) != 2:
        await message.answer('Введите ключ и название: ALT Altair')
        return
    project = await svc.create_project(db, user, ProjectCreate(key=values[0].upper(), name=values[1]))
    await state.clear()
    await message.answer(f'Создан приватный проект <b>{escape(project.name)}</b> ({project.key}).', reply_markup=home_keyboard())


@router.callback_query(F.data.startswith('project:'))
async def select_project(callback: CallbackQuery, db, user):
    project = await svc.access(db, user.id, int(callback.data.split(':')[1]))
    user.active_project_id = project.id
    await send(callback, f'<b>{escape(project.name)}</b> · {project.key}', keyboard([
        [('Задачи', 'tasks:0'), ('Новая задача', 'new')], [('Пригласить по ссылке', f'invite:{project.id}')], [('Все проекты', 'projects')]]))


@router.callback_query(F.data.startswith('invite:'))
async def invite(callback: CallbackQuery, db, user):
    value = await svc.create_invite(db, user.id, int(callback.data.split(':')[1]))
    await send(callback, 'Одноразовая ссылка, действует 7 дней:\n' + value['url'])


async def show_tasks(event, db, user, page=0, today_only=False, mine=False):
    project_id = None if today_only else user.active_project_id
    tasks = await svc.list_tasks(db, user.id, project_id=project_id, mine=mine)
    if today_only:
        done = set()
        for project in await svc.projects(db, user.id):
            done.update(s.id for s in await svc.statuses(db, project.id) if s.is_done)
        tasks = [t for t in tasks if t.deadline == svc.today() and t.status_id not in done]
    visible = tasks[page * 15:(page + 1) * 15]
    rows = [[(f'{t.key} · {t.title[:55]}', f'task:{t.id}')] for t in visible]
    if not today_only:
        nav = []
        if page:
            nav.append(('Назад', f'tasks:{page-1}'))
        if len(tasks) > (page + 1) * 15:
            nav.append(('Далее', f'tasks:{page+1}'))
        if nav:
            rows.append(nav)
    rows += [[('Новая задача', 'new'), ('Главная', 'home')]]
    await send(event, 'Задачи на сегодня:' if today_only and tasks else ('Ваши задачи:' if tasks else 'Задач пока нет.'), keyboard(rows))


@router.message(Command('tasks', 'today'))
async def tasks_command(message: Message, db, user, state: FSMContext):
    await state.clear()
    await show_tasks(message, db, user, today_only=message.text.startswith('/today'))


@router.callback_query(F.data.startswith('tasks:'))
async def tasks_callback(callback: CallbackQuery, db, user, state: FSMContext):
    await state.clear()
    await show_tasks(callback, db, user, page=max(0, int(callback.data.split(':')[1])))


@router.callback_query(F.data == 'today')
async def today_callback(callback: CallbackQuery, db, user):
    await show_tasks(callback, db, user, today_only=True)


async def new_prompt(event, state, db, user):
    items = await svc.projects(db, user.id)
    if not items:
        await show_projects(event, db, user)
        return
    await state.clear()
    await state.set_state(Flow.title)
    await send(event, 'Введите заголовок задачи. Проект выберем перед сохранением. /cancel отменяет ввод.')


@router.message(Command('new'))
async def new_command(message: Message, state: FSMContext, db, user):
    await new_prompt(message, state, db, user)


@router.callback_query(F.data == 'new')
async def new_callback(callback: CallbackQuery, state: FSMContext, db, user):
    await new_prompt(callback, state, db, user)


async def prepare_create(event, parsed, state, db, user):
    items = await svc.projects(db, user.id)
    if parsed.get('project'):
        query = parsed['project'].lower()
        items = [p for p in items if p.key.lower() == query or p.name.lower() == query]
    if not items:
        await send(event, 'Проект не найден. Сначала создайте или выберите его.', keyboard([[('Проекты', 'projects')]]))
        return
    await state.set_state(Flow.confirm)
    await state.update_data(pending=parsed)
    rows = [[(f'Создать в {p.key} · {p.name}', f'confirm_create:{p.id}')] for p in items[:80]]
    rows.append([('Отменить', 'cancel')])
    await send(event, f'<b>Проверьте задачу</b>\n{escape(parsed["title"])}\nДедлайн: {parsed.get("deadline") or "не задан"}\n\nВыберите проект и подтвердите:', keyboard(rows))


@router.message(Flow.title)
async def new_title(message: Message, state: FSMContext, db, user):
    payload = TaskCreate(title=message.text or '')
    await prepare_create(message, {'action': 'create', 'title': payload.title, 'deadline': None}, state, db, user)


@router.callback_query(F.data.startswith('confirm_create:'))
async def create_confirm(callback: CallbackQuery, state: FSMContext, db, user):
    value = (await state.get_data()).get('pending')
    if not value or await state.get_state() != Flow.confirm.state:
        await callback.answer('Подтверждение уже использовано', show_alert=True)
        return
    task = await svc.create_task(db, user.id, int(callback.data.split(':')[1]), TaskCreate(title=value['title'], deadline=value.get('deadline')))
    await state.clear()
    await show_task(callback, db, user, task.id)


async def show_task(event, db, user, task_id):
    task = await svc.get_task(db, user.id, task_id)
    status = next(s for s in await svc.statuses(db, task.project_id) if s.id == task.status_id)
    text = f'<b>{task.key} · {escape(task.title)}</b>\nСтатус: {escape(status.name)}\nПриоритет: {task.priority}\nДедлайн: {task.deadline or "не задан"}\n'
    if task.description:
        text += '\n' + escape(task.description[:1800])
    rows = [[('Статус', f'pick_status:{task.id}'), ('Приоритет', f'pick_priority:{task.id}')],
            [('Исполнитель', f'pick_assignee:{task.id}'), ('Дедлайн', f'deadline:{task.id}')],
            [('Запустить таймер', f'timer_start:{task.id}'), ('Таймер', 'timer')],
            [('Восстановить' if task.deleted_at else 'Удалить', f'restore:{task.id}:{task.version}' if task.deleted_at else f'delete_prompt:{task.id}:{task.version}')], [('Главная', 'home')]]
    markup = keyboard(rows)
    if cfg.app_url:
        markup.inline_keyboard.insert(0, [InlineKeyboardButton(text='Открыть задачу', web_app=WebAppInfo(url=f'{cfg.app_url}?task={task.id}'))])
    await send(event, text, markup)


@router.callback_query(F.data.startswith('task:'))
async def task_callback(callback: CallbackQuery, db, user):
    await show_task(callback, db, user, int(callback.data.split(':')[1]))


@router.callback_query(F.data.startswith('pick_'))
async def pick_field(callback: CallbackQuery, db, user):
    action, task_id = callback.data.split(':')
    task = await svc.get_task(db, user.id, int(task_id))
    field = action[5:]
    if field == 'status':
        values = [(s.name, s.id) for s in await svc.statuses(db, task.project_id)]
    elif field == 'priority':
        values = [(p, p) for p in ('Low', 'Medium', 'High', 'Critical')]
    else:
        values = [('Не назначен', 0)] + [(u['name'], u['id']) for u in await svc.members(db, user.id, task.project_id)]
    await send(callback, 'Выберите значение:', keyboard([[(str(label), f'set:{field}:{task.id}:{task.version}:{value}')] for label, value in values]))


@router.callback_query(F.data.startswith('set:'))
async def set_field(callback: CallbackQuery, db, user):
    _, field, task_id, version, value = callback.data.split(':')
    data = {field + '_id' if field in ('status', 'assignee') else field: (int(value) or None) if field in ('status', 'assignee') else value}
    await svc.update_task(db, user.id, int(task_id), TaskEdit(version=int(version), **data))
    await show_task(callback, db, user, int(task_id))


@router.callback_query(F.data.startswith('deadline:'))
async def deadline_prompt(callback: CallbackQuery, state: FSMContext, db, user):
    task = await svc.get_task(db, user.id, int(callback.data.split(':')[1]))
    await state.set_state(Flow.deadline)
    await state.update_data(task_id=task.id, version=task.version)
    await send(callback, 'Введите дату ГГГГ-ММ-ДД, «сегодня», «завтра», «послезавтра» или «нет».')


@router.message(Flow.deadline)
async def deadline_save(message: Message, state: FSMContext, db, user):
    raw = (message.text or '').strip().lower()
    if raw == 'нет':
        deadline = None
    else:
        parsed = parse_command('Создай задачу дата ' + raw, svc.today())
        if not parsed or not parsed.get('deadline'):
            await message.answer('Не удалось распознать дату. Пример: 2026-10-20 или завтра.')
            return
        deadline = parsed['deadline']
    data = await state.get_data()
    await svc.update_task(db, user.id, data['task_id'], TaskEdit(version=data['version'], deadline=deadline))
    await state.clear()
    await show_task(message, db, user, data['task_id'])


@router.callback_query(F.data.startswith('delete_prompt:'))
async def delete_prompt(callback: CallbackQuery):
    _, task_id, version = callback.data.split(':')
    await send(callback, 'Удалить задачу? Её можно будет восстановить в Mini App.', keyboard([[('Подтвердить удаление', f'delete:{task_id}:{version}')], [('Отмена', f'task:{task_id}')]]))


@router.callback_query(F.data.startswith('delete:') | F.data.startswith('restore:'))
async def delete_restore(callback: CallbackQuery, db, user):
    action, task_id, version = callback.data.split(':')
    await svc.update_task(db, user.id, int(task_id), TaskEdit(version=int(version), deleted=action == 'delete'))
    await show_task(callback, db, user, int(task_id))


async def show_timer(event, db, user):
    entry = await svc.timer(db, user.id)
    if not entry:
        await send(event, 'Нет активного таймера. Откройте задачу и нажмите «Запустить таймер».', home_keyboard())
        return
    result = svc.timer_row(entry)
    task = await db.get(Task, entry.task_id)
    seconds = result['elapsed_seconds']
    await send(event, f'{task.key}: {seconds // 3600:02}:{seconds // 60 % 60:02}:{seconds % 60:02}\n' + ('Работает' if entry.running_since else 'На паузе'), keyboard([
        [('Пауза', 'timer_pause') if entry.running_since else ('Продолжить', f'timer_start:{task.id}'), ('Остановить', 'timer_stop')], [('Главная', 'home')]]))


@router.message(Command('timer'))
async def timer_command(message: Message, db, user):
    await show_timer(message, db, user)


@router.callback_query(F.data == 'timer')
async def timer_callback(callback: CallbackQuery, db, user):
    await show_timer(callback, db, user)


@router.callback_query(F.data.startswith('timer_'))
async def timer_change(callback: CallbackQuery, db, user):
    action = callback.data.split(':')[0][6:]
    task_id = int(callback.data.split(':')[1]) if ':' in callback.data else None
    await svc.timer_action(db, user.id, action, task_id)
    await show_timer(callback, db, user)


@router.message(Command('help'))
async def help_command(message: Message):
    await message.answer(HELP, reply_markup=home_keyboard())


@router.callback_query(F.data.in_({'home', 'help', 'cancel'}))
async def home_callback(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await send(callback, HELP if callback.data == 'help' else 'Главная', home_keyboard())


@router.callback_query(F.data.startswith('confirm_state:'))
async def command_confirm(callback: CallbackQuery, state: FSMContext, db, user):
    _, task_id, version, status_id = callback.data.split(':')
    value = await state.get_data()
    if value.get('state_confirmation') != callback.data and callback.data not in value.get('state_options', []):
        await callback.answer('Подтверждение уже использовано', show_alert=True)
        return
    await svc.update_task(db, user.id, int(task_id), TaskEdit(version=int(version), status_id=int(status_id)))
    await state.clear()
    await show_task(callback, db, user, int(task_id))


@router.message(F.text)
async def text_command(message: Message, state: FSMContext, db, user):
    parsed = parse_command(message.text, svc.today())
    if not parsed:
        await message.answer('Не распознал команду. Используйте кнопки или /help.', reply_markup=home_keyboard())
    elif parsed['action'] == 'error':
        await message.answer(parsed['message'])
    elif parsed['action'] == 'today':
        await show_tasks(message, db, user, today_only=True, mine=parsed['mine'])
    elif parsed['action'] == 'create':
        await prepare_create(message, parsed, state, db, user)
    else:
        task = await svc.task_by_key(db, user.id, parsed['key'])
        statuses = await svc.statuses(db, task.project_id)
        if parsed['action'] == 'done':
            choices = [s for s in statuses if s.is_done]
        else:
            choices = [s for s in statuses if s.name.lower() == 'in progress']
            if not choices:
                choices = [s for s in statuses if not s.is_done]
        rows = []
        for status in choices:
            value = f'confirm_state:{task.id}:{task.version}:{status.id}'
            rows.append([(f'Подтвердить: {status.name}', value)])
        if len(choices) == 1:
            await state.update_data(state_confirmation=rows[0][0][1])
        else:
            await state.update_data(state_options=[row[0][1] for row in rows])
        rows.append([('Отменить', 'cancel')])
        await message.answer(f'{task.key} · {escape(task.title)}\nПодтвердите новый статус:', reply_markup=keyboard(rows))


async def main():
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s %(message)s')
    logging.getLogger('aiohttp').setLevel(logging.WARNING)
    bot = Bot(cfg.bot_token, session=AiohttpSession(proxy=cfg.bot_proxy or None, timeout=20), default=DefaultBotProperties(parse_mode='HTML'))
    storage = RedisStorage.from_url(cfg.redis_url, state_ttl=3600, data_ttl=3600)
    dp = Dispatcher(storage=storage, events_isolation=RedisEventIsolation.from_url(cfg.redis_url))
    dp.update.outer_middleware(DatabaseMiddleware())
    dp.include_router(router)
    try:
        await bot.set_my_commands([BotCommand(command=name, description=desc) for name, desc in (
            ('start', 'Главная'), ('projects', 'Проекты'), ('tasks', 'Задачи'), ('today', 'На сегодня'), ('new', 'Создать задачу'), ('timer', 'Таймер'), ('help', 'Помощь'), ('cancel', 'Отменить ввод'))])
        if cfg.app_url:
            await bot.set_chat_menu_button(menu_button=MenuButtonWebApp(text='TaskMaster', web_app=WebAppInfo(url=cfg.app_url)))
        await dp.start_polling(bot, allowed_updates=['message', 'callback_query'], handle_as_tasks=False)
    finally:
        await bot.session.close()
        await storage.close()


if __name__ == '__main__':
    asyncio.run(main())
