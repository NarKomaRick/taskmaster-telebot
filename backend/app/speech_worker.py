import asyncio
import json
import logging
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
import httpx
from sqlalchemy import select, or_, and_
from .config import settings
from .db import Session
from .models import PersonalCapture, User, utcnow
from .personal_parser import parse_note
from .worker import TelegramDelivery

_model = None


def transcribe_file(path):
    from vosk import Model, KaldiRecognizer, SetLogLevel
    global _model
    SetLogLevel(-1)
    if _model is None:
        _model = Model('/opt/vosk-model-small-ru-0.22')
    result = subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-i', str(path), '-t', '121', '-ac', '1', '-ar', '16000', '-f', 's16le', 'pipe:1'],
                            capture_output=True, timeout=60, check=True)
    if not result.stdout or len(result.stdout) > 120 * 32000:
        raise ValueError('Audio duration limit')
    recognizer = KaldiRecognizer(_model, 16000)
    parts = []
    for offset in range(0, len(result.stdout), 4000):
        if recognizer.AcceptWaveform(result.stdout[offset:offset + 4000]):
            parts.append(json.loads(recognizer.Result()).get('text', ''))
    parts.append(json.loads(recognizer.FinalResult()).get('text', ''))
    transcript = ' '.join(p for p in parts if p).strip()
    if not transcript:
        raise ValueError('No speech detected')
    return transcript[:4000]


async def download_voice(client, file_id, target):
    root = 'https://api.telegram.org'
    response = await client.post(f'{root}/bot{settings().bot_token}/getFile', json={'file_id': file_id})
    data = response.json()
    if not data.get('ok'):
        raise ValueError('Telegram download unavailable')
    path = data['result']['file_path']
    if '..' in path or path.startswith('/') or len(path) > 500:
        raise ValueError('Invalid Telegram file path')
    size = 0
    async with client.stream('GET', f'{root}/file/bot{settings().bot_token}/{path}') as stream:
        if stream.status_code != 200:
            raise ValueError('Download failed')
        with Path(target).open('wb') as output:
            async for chunk in stream.aiter_bytes():
                size += len(chunk)
                if size > 5 * 1024 * 1024:
                    raise ValueError('Audio size limit')
                output.write(chunk)


async def process_one(client, transcriber=transcribe_file):
    async with Session.begin() as db:
        capture = await db.scalar(select(PersonalCapture).where(PersonalCapture.file_id.is_not(None),
            or_(PersonalCapture.state == 'queued', and_(PersonalCapture.state == 'processing', PersonalCapture.available_at < utcnow())),
            PersonalCapture.available_at <= utcnow()).order_by(PersonalCapture.id).with_for_update(skip_locked=True).limit(1))
        if not capture:
            return False
        capture.state = 'processing'
        capture.available_at = utcnow() + timedelta(minutes=5)
        capture.attempts += 1
        identity, file_id, received = capture.id, capture.file_id, capture.created_at
    try:
        with TemporaryDirectory(prefix='taskmaster-voice-') as temp:
            path = Path(temp) / 'voice.ogg'
            await download_voice(client, file_id, path)
            transcript = await asyncio.to_thread(transcriber, path)
        # Relative dates use receipt time, including recovery across midnight.
        draft = parse_note(transcript, received)
        async with Session.begin() as db:
            capture = await db.get(PersonalCapture, identity)
            capture.transcript, capture.draft, capture.state = transcript, draft, 'draft'
            capture.notified, capture.available_at = False, utcnow()
    except Exception:
        logging.warning('Voice capture %s failed', identity)
        async with Session.begin() as db:
            capture = await db.get(PersonalCapture, identity)
            capture.state = 'failed' if capture.attempts >= 3 else 'queued'
            capture.available_at = utcnow() + timedelta(seconds=30 * capture.attempts)
    return True


async def notify_one(bot):
    async with Session.begin() as db:
        capture = await db.scalar(select(PersonalCapture).where(PersonalCapture.state.in_(['draft', 'failed']),
            PersonalCapture.notified.is_(False), PersonalCapture.available_at <= utcnow()).order_by(PersonalCapture.id).with_for_update(skip_locked=True).limit(1))
        if not capture:
            return False
        user = await db.get(User, capture.user_id)
        if capture.state == 'failed':
            text = 'Не получилось разобрать голосовое. Пришлите запись ещё раз или напишите дело текстом.'
            markup = None
        else:
            draft = capture.draft
            due = draft.get('due_at')
            label = datetime.fromisoformat(due).strftime('%d.%m.%Y %H:%M' if draft.get('due_has_time') else '%d.%m.%Y') if due else 'нужно уточнить' if draft.get('needs_date') else 'без срока'
            text = f'Я услышал:\n\n{draft["title"]}\nСрок: {label}\n\nСохранить дело?'
            markup = {'inline_keyboard': [[{'text': 'Сохранить', 'callback_data': f'vsave:{capture.id}'}, {'text': 'Исправить', 'callback_data': f'vedit:{capture.id}'}],
                                           [{'text': 'Отменить', 'callback_data': f'vcancel:{capture.id}'}]]}
        try:
            await bot.send_message(user.telegram_id, text, reply_markup=markup)
            capture.notified = True
        except Exception:
            capture.available_at = utcnow() + timedelta(minutes=1)
        return True


async def main():
    logging.basicConfig(level=logging.INFO)
    logging.getLogger('httpx').setLevel(logging.WARNING)
    logging.getLogger('httpcore').setLevel(logging.WARNING)
    bot = TelegramDelivery()
    client = httpx.AsyncClient(timeout=60, proxy=settings().bot_proxy or None)
    try:
        while True:
            try:
                await notify_one(bot)
                if not await process_one(client):
                    await asyncio.sleep(2)
            except Exception:
                logging.exception('Voice queue unavailable')
                await asyncio.sleep(10)
    finally:
        await client.aclose()
        await bot.close()


if __name__ == '__main__':
    asyncio.run(main())
