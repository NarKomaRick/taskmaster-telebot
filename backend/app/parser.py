import re
from datetime import date, timedelta


def parse_command(text: str, today: date) -> dict | None:
    value = text.strip()
    if re.fullmatch(r'покажи (?:мои )?задачи на сегодня', value, re.I):
        return {'action': 'today', 'mine': 'мои' in value.lower()}
    match = re.fullmatch(r'(заверши|начни работу над)\s+([A-Z][A-Z0-9]{1,11}-\d+)', value, re.I)
    if match:
        return {'action': 'done' if match[1].lower() == 'заверши' else 'start', 'key': match[2].upper()}
    match = re.fullmatch(r'(?:создай|добавь) задачу\s+(.+)', value, re.I)
    if not match:
        return None
    title = match[1].strip()
    project = None
    project_match = re.search(r'\s+в проект(?:е)?\s+(.+)$', title, re.I)
    if project_match:
        project = project_match[1].strip()
        title = title[:project_match.start()]
    deadline = None
    date_match = re.search(r'\s+(сегодня|завтра|послезавтра|\d{4}-\d{2}-\d{2}|\d{2}\.\d{2}\.\d{4})$', title, re.I)
    if date_match:
        token = date_match[1].lower()
        if token in ('сегодня', 'завтра', 'послезавтра'):
            deadline = today + timedelta(days={'сегодня': 0, 'завтра': 1, 'послезавтра': 2}[token])
        else:
            try:
                deadline = date.fromisoformat(token) if '-' in token else date(*map(int, token.split('.')[::-1]))
            except ValueError:
                return {'action': 'error', 'message': 'Некорректная дата. Используйте ГГГГ-ММ-ДД.'}
        title = title[:date_match.start()]
    if not title.strip() or len(title) > 300:
        return {'action': 'error', 'message': 'Заголовок должен содержать от 1 до 300 символов.'}
    return {'action': 'create', 'title': title.strip(), 'project': project, 'deadline': deadline.isoformat() if deadline else None}
