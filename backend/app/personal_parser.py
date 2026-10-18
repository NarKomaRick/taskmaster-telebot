import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from .config import settings

MONTHS = {'января': 1, 'февраля': 2, 'марта': 3, 'апреля': 4, 'мая': 5, 'июня': 6, 'июля': 7, 'августа': 8, 'сентября': 9, 'октября': 10, 'ноября': 11, 'декабря': 12}
WEEKDAYS = {'понедельник': 0, 'вторник': 1, 'среду': 2, 'среда': 2, 'четверг': 3, 'пятницу': 4, 'пятница': 4, 'субботу': 5, 'суббота': 5, 'воскресенье': 6}
NUMBERS = {'один': 1, 'одну': 1, 'одна': 1, 'два': 2, 'две': 2, 'три': 3, 'четыре': 4, 'пять': 5, 'шесть': 6, 'семь': 7, 'восемь': 8, 'девять': 9, 'десять': 10, 'одиннадцать': 11, 'двенадцать': 12, 'тринадцать': 13, 'четырнадцать': 14, 'пятнадцать': 15, 'шестнадцать': 16, 'семнадцать': 17, 'восемнадцать': 18, 'девятнадцать': 19, 'двадцать': 20, 'тридцать': 30}


def local_now():
    return datetime.now(ZoneInfo(settings().timezone))


def normalize_numbers(text):
    ordinals = {'первого': 1, 'второго': 2, 'третьего': 3, 'четвертого': 4, 'четвёртого': 4, 'пятого': 5, 'шестого': 6, 'седьмого': 7, 'восьмого': 8, 'девятого': 9, 'десятого': 10, 'одиннадцатого': 11, 'двенадцатого': 12, 'тринадцатого': 13, 'четырнадцатого': 14, 'пятнадцатого': 15, 'шестнадцатого': 16, 'семнадцатого': 17, 'восемнадцатого': 18, 'девятнадцатого': 19, 'двадцатого': 20, 'тридцатого': 30}
    numbers = {**NUMBERS, **ordinals}
    result = re.sub(r'\b(?:' + '|'.join(numbers) + r')\b', lambda m: str(numbers[m[0].lower()]), text, flags=re.I)
    return re.sub(r'\b(20|30)\s+([1-9])\b', lambda m: str(int(m[1])+int(m[2])), result)


def parse_note(raw: str, now: datetime | None = None) -> dict:
    now = (now or local_now()).astimezone(ZoneInfo(settings().timezone))
    value = normalize_numbers(raw.strip())
    spans = []
    candidates = []
    has_time = False
    half = re.search(r'\bчерез\s+полчаса\b', value, re.I)
    if half:
        candidates.append(now + timedelta(minutes=30)); spans.append(half.span()); has_time = True
    relative = re.search(r'\bчерез\s+(?:(\d+)\s+)?(минут\w*|час\w*|день|дня|дней|недел\w*)\b', value, re.I)
    if relative:
        count = int(relative[1] or 1)
        if count < 1 or count > 365:
            return {'title': raw.strip()[:500], 'due_at': None, 'due_has_time': False, 'needs_date': True}
        unit = relative[2].lower()
        if unit.startswith('минут'):
            target = now + timedelta(minutes=count); has_time = True
        elif unit.startswith('час'):
            target = now + timedelta(hours=count); has_time = True
        else:
            target = now + timedelta(days=count * (7 if unit.startswith('недел') else 1))
        candidates.append(target); spans.append(relative.span())
    for word in re.finditer(r'\b(послезавтра|завтра|сегодня)\b', value, re.I):
        candidates.append(now + timedelta(days={'сегодня': 0, 'завтра': 1, 'послезавтра': 2}[word[1].lower()]))
        spans.append(word.span())
    numeric_pattern = r'\b(?:(?:до|к|на|в)\s+)?(\d{4}-\d{2}-\d{2}|\d{1,2}\.\d{1,2}(?:\.\d{4})?)\b'
    numeric = re.search(numeric_pattern, value, re.I)
    invalid = False
    if numeric:
        try:
            token = numeric[1]
            if '-' in token:
                day = datetime.fromisoformat(token)
            else:
                parts = list(map(int, token.split('.')))
                day = datetime(parts[2] if len(parts) == 3 else now.year, parts[1], parts[0])
                if len(parts) == 2 and day.date() < now.date():
                    day = day.replace(year=now.year + 1)
            candidates.append(day.replace(tzinfo=now.tzinfo)); spans.append(numeric.span())
        except ValueError:
            invalid = True
    month_pattern = r'\b(?:(?:до|к|на|в)\s+)?(\d{1,2})\s+(' + '|'.join(MONTHS) + r')(?:\s+(20\d{2}))?\b'
    month = re.search(month_pattern, value, re.I)
    if month:
        try:
            target = datetime(int(month[3] or now.year), MONTHS[month[2].lower()], int(month[1]), tzinfo=now.tzinfo)
            if not month[3] and target.date() < now.date():
                target = target.replace(year=now.year + 1)
            candidates.append(target); spans.append(month.span())
        except ValueError:
            invalid = True
    weekday_pattern = r'\b(?:(?:в|к|до|на)\s+)?(?:(следующ\w*)\s+)?(' + '|'.join(WEEKDAYS) + r')\b'
    weekday = re.search(weekday_pattern, value, re.I)
    if weekday:
        offset = (WEEKDAYS[weekday[2].lower()] - now.weekday()) % 7
        if weekday[1] and offset == 0:
            offset = 7
        candidates.append(now + timedelta(days=offset)); spans.append(weekday.span())
    clocks = [c for c in re.finditer(r'\b(?:в|к)\s+(\d{1,2})(?:(?:[:.]|\s+)(\d{2}))?(?:\s*час(?:а|ов)?)?\b', value, re.I)
              if not any(start <= c.start(1) < end for start, end in spans)]
    clock = clocks[0] if clocks else None
    if len(clocks) > 1:
        invalid = True
    if clock:
        hour, minute = int(clock[1]), int(clock[2] or 0)
        if hour > 23 or minute > 59:
            invalid = True
        else:
            if not candidates:
                candidates.append(now if now.replace(hour=hour, minute=minute, second=0, microsecond=0) > now else now + timedelta(days=1))
            candidates = [t.replace(hour=hour, minute=minute, second=0, microsecond=0) for t in candidates]
            has_time = True; spans.append(clock.span())
    # Multiple dates are never silently collapsed into one deadline.
    repeated_relative = len(re.findall(r'\bчерез\s+(?:\d+\s+)?(?:минут\w*|час\w*|день|дня|дней|недел\w*)\b', value, re.I)) > 1
    repeated_dates = any(len(re.findall(pattern, value, re.I)) > 1 for pattern in (numeric_pattern, month_pattern, weekday_pattern))
    unknown_relative = bool(re.search(r'\bчерез\s', value, re.I)) and not relative and not half
    ambiguous = invalid or len(candidates) > 1 or repeated_relative or repeated_dates or unknown_relative or bool(re.search(r'следующей неделе|конц\w* недели|через месяц', value, re.I))
    due = candidates[0] if len(candidates) == 1 and not ambiguous else None
    if due and not has_time:
        due = due.replace(hour=23, minute=59, second=0, microsecond=0)
    title = value
    if not ambiguous:
        for start, end in sorted(spans, reverse=True):
            title = title[:start] + ' ' + title[end:]
    title = re.sub(r'^\s*(?:(?:мне\s+)?(?:нужно|надо)\s+|(?:создай|добавь|запиши)\s+(?:(?:задачу|дело)\s+)?|напомни\s+(?:мне\s+)?)', '', title, flags=re.I)
    title = re.sub(r'\s+', ' ', title).strip(' .,;:-')
    title = re.sub(r'\s+(?:до|к|на|в)$', '', title, flags=re.I).strip()
    return {'title': (title or raw.strip())[:500], 'due_at': due.isoformat() if due else None,
            'due_has_time': bool(due and has_time), 'needs_date': ambiguous}


def parse_reminder_time(raw, now=None):
    result = parse_note(raw, now)
    if result['needs_date'] or not result['due_at']:
        raise ValueError('Напишите дату и время, например: завтра в 18:00 или 25 октября в 09:00')
    value = datetime.fromisoformat(result['due_at'])
    if not result['due_has_time']:
        value = value.replace(hour=9, minute=0)
    if value <= (now or local_now()):
        raise ValueError('Это время уже прошло. Выберите будущее время.')
    return value
