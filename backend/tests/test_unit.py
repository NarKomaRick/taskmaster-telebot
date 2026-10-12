import hashlib
import hmac
from datetime import date
from urllib.parse import parse_qsl, urlencode
import pytest
from fastapi import HTTPException
from app.auth import validate_init_data, verify_github
from app.config import settings
from app.parser import parse_command
from app.api import file_type
from conftest import signed_identity


@pytest.mark.parametrize('text,expected', [
    ('Создай задачу исправить авторизацию завтра', {'action': 'create', 'title': 'исправить авторизацию', 'project': None, 'deadline': '2026-10-13'}),
    ('Добавь задачу обновить API в проект Altair', {'action': 'create', 'title': 'обновить API', 'project': 'Altair', 'deadline': None}),
    ('Создай задачу релиз 20.10.2026 в проект ALT', {'action': 'create', 'title': 'релиз', 'project': 'ALT', 'deadline': '2026-10-20'}),
    ('Покажи мои задачи на сегодня', {'action': 'today', 'mine': True}),
    ('Заверши ALT-12', {'action': 'done', 'key': 'ALT-12'}),
    ('Начни работу над KDR-8', {'action': 'start', 'key': 'KDR-8'}),
    ('Неизвестная фраза', None),
])
def test_parser(text, expected):
    assert parse_command(text, date(2026, 10, 12)) == expected


def test_invalid_date():
    assert parse_command('Создай задачу релиз 2026-02-30', date(2026, 10, 12))['action'] == 'error'


def test_init_data_valid():
    assert validate_init_data(signed_identity()[4:], settings().bot_token)['id'] == 900000001


@pytest.mark.parametrize('kind', ['tamper', 'expired', 'future', 'duplicate', 'unsigned'])
def test_init_data_rejects(kind):
    raw = signed_identity(age=7200 if kind == 'expired' else -120 if kind == 'future' else 0)[4:]
    if kind == 'tamper':
        values = dict(parse_qsl(raw)); values['user'] = '{"id":1}'; raw = urlencode(values)
    elif kind == 'duplicate':
        raw += '&auth_date=1'
    elif kind == 'unsigned':
        raw = 'user=%7B%22id%22%3A1%7D'
    with pytest.raises(HTTPException) as error:
        validate_init_data(raw, settings().bot_token)
    assert error.value.status_code == 401


def test_github_signature():
    verify_github(b'Hello, World!', 'sha256=757107ea0eb2509fc211221cce984b8a37570b6d7586c22c46f4379c8b043e17', "It's a Secret to Everybody")
    with pytest.raises(HTTPException):
        verify_github(b'changed', 'sha256=bad', 'secret')


def test_attachment_validation():
    assert file_type('test.png', b'\x89PNG\r\n\x1a\n' + b'data') == 'image/png'
    assert file_type('test.txt', 'текст'.encode()) == 'text/plain'
    for name, data in [('bad.svg', b'<script>'), ('fake.png', b'HTML'), ('bad.txt', b'\xff')]:
        with pytest.raises(HTTPException):
            file_type(name, data)
