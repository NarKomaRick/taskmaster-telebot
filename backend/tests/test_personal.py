from datetime import datetime, timedelta
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo
import pytest
from sqlalchemy import select
from app.models import PersonalTask, PersonalReminder, PersonalCapture, User, utcnow
from app.personal_parser import parse_note, parse_reminder_time
from app.personal_delivery import deliver_personal
from app import speech_worker
from conftest import signed_identity

NOW = datetime(2026, 10, 18, 12, 0, tzinfo=ZoneInfo('Europe/Moscow'))


@pytest.mark.parametrize('text,title,date,time', [
    ('Через неделю нужно сдать проект по вышмату', 'сдать проект по вышмату', '2026-10-25', False),
    ('Нужно сдать проект через две недели', 'сдать проект', '2026-11-01', False),
    ('Купить молоко завтра', 'Купить молоко', '2026-10-19', False),
    ('Позвонить сегодня в 18:30', 'Позвонить', '2026-10-18', True),
    ('В пятницу сдать проект', 'сдать проект', '2026-10-23', False),
    ('Оплатить до 25 октября', 'Оплатить', '2026-10-25', False),
    ('Сдать 01.11.2026', 'Сдать', '2026-11-01', False),
    ('Через три дня купить фильтр', 'купить фильтр', '2026-10-21', False),
    ('Позвонить через час', 'Позвонить', '2026-10-18', True),
    ('Сдать двадцать пятого октября', 'Сдать', '2026-10-25', False),
    ('К 25 октября в 18:00 сдать проект', 'сдать проект', '2026-10-25', True),
    ('Позвонить через полчаса', 'Позвонить', '2026-10-18', True),
    ('Купить фильтр', 'Купить фильтр', None, False),
])
def test_personal_parser(text, title, date, time):
    result = parse_note(text, NOW)
    assert result['title'] == title
    assert (result['due_at'][:10] if result['due_at'] else None) == date
    assert result['due_has_time'] == time


@pytest.mark.parametrize('text', ['Сдать 31.02.2026', 'Сдать завтра или послезавтра', 'Сдать на следующей неделе', 'Сдать завтра в 25:00', 'Сдать 25 октября или 27 октября', 'Сдать в пятницу или в субботу', 'Сдать 25.10.2026 или 27.10.2026'])
def test_ambiguous_dates_require_clarification(text):
    result = parse_note(text, NOW)
    assert result['needs_date'] and result['due_at'] is None


def test_reminder_timezone_and_future():
    assert parse_reminder_time('завтра в восемнадцать часов', NOW).hour == 18
    assert parse_reminder_time('25 октября', NOW).hour == 9
    with pytest.raises(ValueError):
        parse_reminder_time('сегодня в 09:00', NOW)


async def test_personal_privacy_and_atomic_edit(client):
    created = await client.post('/api/personal/tasks', json={'text': 'Через неделю сдать проект по вышмату'})
    assert created.status_code == 201, created.text
    task = created.json()
    assert task['due_at'] and not task['reminder']
    foreign = {'Authorization': signed_identity(900000004, 'other')}
    assert (await client.get('/api/personal/tasks', headers=foreign)).json() == []
    assert (await client.patch(f'/api/personal/tasks/{task["id"]}', headers=foreign, json={'version': 1, 'done': True})).status_code == 404
    response = await client.patch(f'/api/personal/tasks/{task["id"]}', json={'version': 1, 'title': 'Should roll back', 'reminder_enabled': True, 'reminder_at': '2020-01-01T09:00:00+03:00'})
    assert response.status_code == 422
    current = (await client.get('/api/personal/tasks')).json()[0]
    assert current['title'] == task['title'] and current['version'] == 1
    valid = await client.patch(f'/api/personal/tasks/{task["id"]}', json={'version': 1, 'title': 'Проект', 'reminder_enabled': True, 'reminder_at': (utcnow()+timedelta(days=1)).isoformat(), 'reminder_interval': 86400})
    assert valid.status_code == 200, valid.text
    assert valid.json()['reminder']['enabled']
    assert (await client.patch(f'/api/personal/tasks/{task["id"]}', json={'version': 1, 'title': 'Stale'})).status_code == 409


@pytest.mark.parametrize('interval', [0, 3600, 86400, 259200, 604800])
async def test_reminder_once_repeats_and_completion(client, database, interval):
    task = (await client.post('/api/personal/tasks', json={'text': 'Проверить проект'})).json()
    scheduled = await client.put(f'/api/personal/tasks/{task["id"]}/reminder', json={'version': 1, 'next_at': (utcnow()+timedelta(hours=1)).isoformat(), 'interval_seconds': interval})
    assert scheduled.status_code == 200, scheduled.text
    async with database.begin() as db:
        reminder = await db.scalar(select(PersonalReminder))
        reminder.next_at = utcnow() - timedelta(days=3, seconds=1)
    bot = AsyncMock()
    assert await deliver_personal(bot)
    assert bot.send_message.await_count == 1
    assert not await deliver_personal(bot)
    async with database() as db:
        reminder = await db.scalar(select(PersonalReminder))
        assert reminder.enabled == bool(interval)
        if interval: assert reminder.next_at > utcnow()
    current = (await client.get('/api/personal/tasks')).json()[0]
    response = await client.patch(f'/api/personal/tasks/{task["id"]}', json={'version': current['version'], 'done': True})
    assert response.status_code == 200
    assert not response.json()['reminder']['enabled']
    assert not await deliver_personal(bot)


async def test_personal_reminder_failure_persists(client, database):
    task = (await client.post('/api/personal/tasks', json={'text': 'Дело'})).json()
    await client.put(f'/api/personal/tasks/{task["id"]}/reminder', json={'version': 1, 'next_at': (utcnow()+timedelta(hours=1)).isoformat()})
    async with database.begin() as db:
        reminder = await db.scalar(select(PersonalReminder)); reminder.next_at = utcnow()-timedelta(seconds=1)
    bot = AsyncMock(); bot.send_message.side_effect = OSError('offline')
    assert await deliver_personal(bot)
    async with database.begin() as db:
        reminder = await db.scalar(select(PersonalReminder))
        assert reminder.attempts == 1 and reminder.enabled
        reminder.retry_at = utcnow()-timedelta(seconds=1)
    bot.send_message.side_effect = None
    assert await deliver_personal(bot)
    assert bot.send_message.await_count == 2


async def test_voice_queue_recovery_and_draft_confirmation(client, database, monkeypatch):
    user = (await client.get('/api/me')).json()
    async with database.begin() as db:
        capture = PersonalCapture(user_id=user['id'], message_id=123, file_id='test-file', state='processing',
                                  created_at=NOW, available_at=utcnow()-timedelta(minutes=1))
        db.add(capture)
    async def fake_download(client, file_id, path):
        path.write_bytes(b'test audio')
    monkeypatch.setattr(speech_worker, 'download_voice', fake_download)
    assert await speech_worker.process_one(None, transcriber=lambda path: 'Через неделю сдать проект по вышмату')
    async with database() as db:
        capture = await db.scalar(select(PersonalCapture))
        assert capture.state == 'draft' and capture.draft['due_at'].startswith('2026-10-25')
        assert not (await db.scalars(select(PersonalTask))).all()
    bot = AsyncMock()
    assert await speech_worker.notify_one(bot)
    assert not await speech_worker.notify_one(bot)
    assert 'Сохранить дело?' in bot.send_message.call_args.args[1]
