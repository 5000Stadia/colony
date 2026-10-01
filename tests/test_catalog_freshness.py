import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from colony import catalog_freshness, providers


class CatalogFreshnessTest(unittest.TestCase):
    def test_codex_shows_identity_and_flags_an_older_client(self):
        p=providers.get('codex')
        metadata=dict(client_version='0.159.3',fetched_at='2026-10-01T12:00:00Z',identity='fixture-account')
        with patch.object(p,'catalog_snapshot',return_value=([],metadata)),patch.object(p,'version',return_value='codex-cli 0.160.0'):
            result=catalog_freshness.info(p)
        self.assertIn(('Identity','fixture-account'),result['fields'])
        self.assertIn(('Fetched','2026-10-01 12:00 UTC'),result['fields'])
        self.assertEqual(result['warnings'],['Catalog predates the installed Codex version'])

    def test_claude_flags_expiry_and_installation_then_reads_a_refreshed_catalog(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);folder=root/'cache'/'model-catalog';folder.mkdir(parents=True)
            path=folder/'fixture-cc.json'
            program=root/'claude';program.write_text('fixture');os.utime(program,(2000,2000))
            def write(fetched,stale):
                path.write_text(json.dumps(dict(fetchedAt=fetched,staleAt=stale,catalog={'config':{'models':[]}})))
            write(1000,3000)
            p=providers.get('claude')
            with patch.object(p,'config_home',return_value=root),patch.object(catalog_freshness.shutil,'which',return_value=str(program)):
                result=catalog_freshness.info(p,now=4000)
                self.assertEqual(len(result['warnings']),2)
                write(4000,5000)
                self.assertFalse(catalog_freshness.info(p,now=4000)['warnings'])
            self.assertEqual(catalog_freshness.display_time(1790856000000), '2026-10-01 12:00 UTC')


from tests.test_board import BoardBase


class CatalogStartupTest(BoardBase):
    def test_session_start_reads_new_native_catalog_without_model_calls(self):
        from colony import board
        board.track(self.root)
        config=Path(self.tmp.name)/'claude-config'
        folder=config/'cache'/'model-catalog';folder.mkdir(parents=True)
        path=folder/'fixture-cc.json'
        path.write_text(json.dumps(dict(fetchedAt=1790856000000,staleAt=1790859600000,
                                       catalog={'config':{'models':[{'id':'claude-fixture','section':'main',
                                               'thinking':{'effort_options':[{'id':'high'}]}}]}})))
        with patch.dict(os.environ,CLAUDE_CONFIG_DIR=str(config)):
            result=self.cli('notes','--deliver','--session')
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(providers.available(providers.get('claude')),[('claude-fixture','Fixture')])
        with patch.object(catalog_freshness,'info',return_value={'fields':[('Identity','fixture-account')],
                                                              'warnings':['Catalog predates the installed Codex version']}):
            page=board.settings_page(board.registry())
        self.assertIn('Identity: fixture-account',page)
        self.assertIn('Catalog predates the installed Codex version',page)
        self.assertIn('next session start',page)
