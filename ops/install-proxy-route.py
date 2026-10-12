"""Add the TaskMaster route to the existing home server Caddy configuration."""
from datetime import datetime, timezone
from pathlib import Path
import shutil
import subprocess

path = Path('/home/komarrick/snabdesk/Caddyfile')
source = path.read_text()
if 'handle_path /taskmaster/*' in source:
    print('TaskMaster proxy route is already configured')
    raise SystemExit(0)
target = '    reverse_proxy app:4173'
if target not in source:
    raise SystemExit('Expected original route was not found; configuration left unchanged')
replacement = '''    redir /taskmaster /taskmaster/ 308
    handle_path /taskmaster/* {
        request_body {
            max_size 11MB
        }
        reverse_proxy taskmaster-api:8000
    }
    handle {
        reverse_proxy app:4173
    }'''
proposed = Path('/home/komarrick/taskmaster/Caddyfile.proposed')
proposed.write_text(source.replace(target, replacement, 1))
subprocess.run(['docker', 'run', '--rm', '--network', 'snabdesk_default', '-e', 'SITE_DOMAIN=snabdesk.ru',
                '-v', f'{proposed}:/etc/caddy/Caddyfile:ro', 'caddy:2-alpine', 'caddy', 'validate',
                '--config', '/etc/caddy/Caddyfile', '--adapter', 'caddyfile'], check=True)
stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
backup = path.with_name(f'Caddyfile.taskmaster-backup-{stamp}')
shutil.copy2(path, backup)
path.write_text(proposed.read_text())
try:
    subprocess.run(['docker', 'exec', 'snabdesk-proxy-1', 'caddy', 'reload', '--config', '/etc/caddy/Caddyfile', '--adapter', 'caddyfile'], check=True)
except subprocess.CalledProcessError:
    path.write_text(source)
    subprocess.run(['docker', 'exec', 'snabdesk-proxy-1', 'caddy', 'reload', '--config', '/etc/caddy/Caddyfile', '--adapter', 'caddyfile'], check=True)
    raise
print(f'Proxy route installed. Original configuration: {backup}')
