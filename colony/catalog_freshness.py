"""Native model-catalog freshness for Settings, without model calls."""
import datetime
import shutil
import time


def timestamp(value):
    try:
        if isinstance(value, (int, float)):
            return value / 1000 if value > 100_000_000_000 else value
        parsed = datetime.datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return parsed.replace(tzinfo=parsed.tzinfo or datetime.timezone.utc).timestamp()
    except (TypeError, ValueError, OverflowError):
        return None


def display_time(value):
    stamp = timestamp(value)
    if stamp is None:
        return 'unknown'
    try:
        return datetime.datetime.fromtimestamp(stamp, datetime.timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
    except (ValueError, OverflowError, OSError):
        return 'unknown'


def info(provider, now=None):
    import json
    import re
    from pathlib import Path
    from . import providers
    now = time.time() if now is None else now
    fields, warnings = [], []
    if providers.key(provider) == 'codex':
        _, metadata = provider.catalog_snapshot()
        if not metadata:
            return dict(fields=[], warnings=['No readable model catalog yet'])
        fields = [('Catalog client', metadata.get('client_version') or 'unknown'),
                  ('Fetched', display_time(metadata.get('fetched_at'))),
                  ('Identity', metadata.get('identity') or 'unknown')]
        version = lambda value: tuple(map(int, re.findall(r'\d+', value or '')[:3]))
        client, installed = version(metadata.get('client_version')), version(provider.version())
        if client and installed and client < installed:
            warnings.append('Catalog predates the installed Codex version')
    else:
        files = sorted((provider.config_home() / 'cache' / 'model-catalog').glob('*-cc.json'),
                       key=lambda p: p.stat().st_mtime, reverse=True)
        data = None
        for path in files:
            try:
                candidate = json.loads(path.read_text())
                if isinstance(candidate.get('catalog', {}).get('config', {}).get('models'), list):
                    data = candidate
                    break
            except (OSError, ValueError, TypeError, AttributeError):
                continue
        if data is None:
            return dict(fields=[], warnings=['No readable model catalog yet'])
        fields = [('Fetched', display_time(data.get('fetchedAt'))), ('Stale after', display_time(data.get('staleAt')))]
        stale, fetched = timestamp(data.get('staleAt')), timestamp(data.get('fetchedAt'))
        if stale is not None and stale <= now:
            warnings.append('Model catalog is past staleAt')
        program = shutil.which(provider.program)
        if program and fetched is not None:
            try:
                if fetched < Path(program).resolve().stat().st_mtime:
                    warnings.append('Catalog predates the installed program update')
            except OSError:
                pass
    return dict(fields=fields, warnings=warnings)
