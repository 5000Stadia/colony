"""Different running Codex versions must not roll account discovery backwards."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from colony import board, providers, codex_remote


class CatalogTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.source = self.base / 'source'
        self.source.mkdir()
        self.source.joinpath('auth.json').write_text('{}')
        env = patch.dict(os.environ, COLONY_BOARD_HOME=str(self.base / 'board'),
                         COLONY_CODEX_SOURCE_HOME=str(self.source), CODEX_HOME=str(self.base / 'unrelated'))
        env.start()
        self.addCleanup(env.stop)
        self.codex = providers.get('codex')
        for obj, name, value in ((providers, 'usable', lambda p: p is self.codex),
                                 (self.codex, 'version', lambda: 'codex-cli 0.159.3')):
            p = patch.object(obj, name, value)
            p.start()
            self.addCleanup(p.stop)

    def catalog(self, home, version, models, date='2026-10-01T12:00:00Z'):
        home.mkdir(parents=True, exist_ok=True)
        (home / 'models_cache.json').write_text(json.dumps(dict(client_version=version, fetched_at=date,
            models=[dict(slug=m, visibility='list', supported_reasoning_levels=[{'effort':'max'}]) for m in models])))

    def test_older_writer_cannot_replace_newer_discovery_but_current_removals_can(self):
        self.catalog(self.source, '0.159.3', ['gpt-6-astra', 'gpt-6.1-sol'])
        providers.discover(calls=False)
        self.catalog(self.source, '0.154.0', ['gpt-6-astra'], '2026-10-02T12:00:00Z')
        providers.discover(calls=False)
        self.assertIn('gpt-6.1-sol', dict(providers.available(self.codex)))
        self.catalog(self.source, '0.159.3', ['gpt-6-astra'], '2026-10-03T12:00:00Z')
        providers.discover(calls=False)
        self.assertNotIn('gpt-6.1-sol', dict(providers.available(self.codex)))

    def test_newer_owned_catalog_requires_same_account_and_does_not_union(self):
        host = board.home() / 'codex-remote' / 'owned'
        self.catalog(self.source, '0.154.0', ['old'])
        self.catalog(host, '0.159.3', ['new'])
        (host / 'auth.json').symlink_to(self.source / 'auth.json')
        other = host.parent / 'another-account'
        self.catalog(other, '0.200.0', ['not-ours'])
        (other / 'auth.json').symlink_to(self.base / 'someone-else-auth.json')
        providers.discover(calls=False)
        self.assertEqual(list(dict(providers.available(self.codex))), ['new'])
        self.assertEqual(providers.efforts_of(self.codex, 'new'), ['max'])
        self.assertEqual(providers._discovered()['codex']['catalog']['path'], str(host / 'models_cache.json'))

    def test_prepare_separates_legacy_catalog_symlink_and_retains_local_refresh(self):
        self.catalog(self.source, '0.159.3', ['new'])
        host = self.base / 'owned'
        host.mkdir()
        (host / 'models_cache.json').symlink_to(self.source / 'models_cache.json')
        codex_remote.prepare_home(self.base, host, self.source)
        self.assertFalse((host / 'models_cache.json').is_symlink())
        self.assertTrue((host / 'auth.json').is_symlink())
        self.catalog(host, '0.154.0', ['old'])
        self.assertEqual(json.loads((self.source / 'models_cache.json').read_text())['client_version'], '0.159.3')
        codex_remote.prepare_home(self.base, host, self.source)
        self.assertEqual(json.loads((host / 'models_cache.json').read_text())['client_version'], '0.154.0')

    def test_account_change_does_not_reuse_old_identity_catalog(self):
        host = board.home() / 'codex-remote' / 'owned'
        self.catalog(self.source, '0.160.0', ['previous-account'])
        path = self.source / 'models_cache.json'
        data = json.loads(path.read_text())
        data['identity'] = 'old-account'
        path.write_text(json.dumps(data))
        providers.discover(calls=False)
        self.catalog(host, '0.160.0', ['previous-account'])
        (host / 'auth.json').symlink_to(self.source / 'auth.json')
        (host / 'models_cache.json').write_text(json.dumps(data))
        self.catalog(self.source, '0.159.3', ['new-account'])
        data = json.loads(path.read_text())
        data['identity'] = 'new-account'
        path.write_text(json.dumps(data))
        providers.discover(calls=False)
        self.assertEqual(list(dict(providers.available(self.codex))), ['new-account'])


if __name__ == '__main__':
    unittest.main()
