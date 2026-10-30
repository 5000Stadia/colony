"""Pending console ownership, entirely against private native-input stand-ins."""
from contextlib import ExitStack
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

from colony import board, console, context
from tests.test_send import WrappedClaudeInputBox


class LedgerBox(WrappedClaudeInputBox):
    def __init__(self):
        super().__init__()
        self.hidden_prefix = 0
        self.clears = 0
        self.menu = False

    def screen(self, name):
        if self.menu:
            return 'Approve an edit?\n❯ 1. Yes\n  2. No\nEnter to select · Esc to cancel'
        if self.hidden_prefix and len(self.text) > self.hidden_prefix:
            full = self.text
            try:
                self.text = full[self.hidden_prefix:]
                return super().screen(name)
            finally:
                self.text = full
        return super().screen(name)

    def run(self, args, **kwargs):
        if args[:2] == ['tmux', 'send-keys'] and args[-2:] == ['C-e', 'C-u']:
            self.clears += 1
            self.text = ''
            self.hidden_prefix = 0
        return super().run(args, **kwargs)


class InputLedgerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'project'
        (self.root / '.board').mkdir(parents=True)
        self.stack = ExitStack(); self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.dict(os.environ, COLONY_BOARD_HOME=str(Path(self.tmp.name) / 'board')))
        board.save_registry({'roots': [], 'settings': {'messaging': False, 'active_hours': 'off'}})
        self.incarnation = 'server-start/session-created/pane-1'
        self.stack.enter_context(patch.object(console, '_input_root', return_value=str(self.root)))
        self.stack.enter_context(patch.object(console, 'attached', return_value=False))
        self.stack.enter_context(patch.object(console, 'busy', return_value=None))
        self.box = LedgerBox()
        self.stack.enter_context(self.box.on())
        self.stack.enter_context(patch.object(console, '_input_identity', side_effect=lambda name: self.incarnation))
        self.text = ('[colony] Context carry-over (90bf5b9fb7b24adf). Write the unfinished intent, '
                     'authorized work, actions already completed, constraints and rejected approaches. '
                     'Preserve the current conversation and do not repeat completed work. ' * 3)[:472]

    def seed(self, text=None, purpose='system', age=0, accepted=False):
        text = self.text if text is None else text
        with console._typing('box'):
            record = console._record_input('box', text, purpose)
            record['at'] -= age
            record['state'] = 'accepted' if accepted else 'unsent'
            console._private_input(console._input_paths('box')[0], record)
        self.box.text = text
        return record

    def pending(self):
        return console._input_json(console._input_paths('box')[0])

    def test_expired_wrapped_suffix_is_retracted_and_person_lands_in_the_same_attempt(self):
        self.seed(age=console.INPUT_TTL + 1)
        self.box.hidden_prefix = 80
        context.atomic_json(context.file(self.root), {'job': {'id': '90bf5b9fb7b24adf', 'phase': 'requested'}})
        self.assertTrue(console.paste_into('box', 'Please continue with my new request.'))
        self.assertEqual(self.box.clears, 1)
        self.assertEqual(self.box.enters, 1)
        self.assertEqual(self.box.accepted, ['Please continue with my new request.'])
        self.assertEqual(context.read(context.file(self.root))['job']['phase'], 'deferred')
        self.assertEqual(self.pending()['purpose'], 'person')
        self.assertEqual(self.pending()['state'], 'accepted')

    def test_live_system_suffix_is_submitted_once_before_the_persons_message(self):
        self.seed()
        self.box.hidden_prefix = 80
        self.assertTrue(console.paste_into('box', 'My next message.'))
        self.assertEqual(self.box.accepted, [self.text, 'My next message.'])
        self.assertEqual(self.box.enters, 2)
        self.assertEqual(self.box.clears, 0)

    def test_person_origin_foreign_prefix_and_same_size_personal_text_are_preserved(self):
        for purpose, replacement in [('person', self.text), ('system', 'h' * len(self.text)),
                                     ('system', '[Colony] ' + 'different human draft ' * 23),
                                     ('system', self.text[:360])]:
            with self.subTest(purpose=purpose, replacement=replacement[:12]):
                self.seed(purpose=purpose, age=console.INPUT_TTL + 1)
                self.box.text = replacement
                self.assertFalse(console.paste_into('box', 'New message.'))
                self.assertEqual(self.box.text, replacement)
                self.assertEqual(self.box.clears, 0)
                self.assertEqual(self.box.enters, 0)
        console._input_paths('box')[0].unlink()
        self.box.text = '[colony] A human wrote this draft.'
        self.assertFalse(console.paste_into('box', 'Another message.'))
        self.assertEqual(self.box.text, '[colony] A human wrote this draft.')

    def test_failed_write_remains_owned_and_can_be_recovered_without_typing_twice(self):
        self.box.swallow = 100
        self.assertFalse(console.type_into('box', self.text))
        self.assertEqual(self.pending()['state'], 'unsent')
        self.box.swallow = 0
        self.assertTrue(console.type_into('box', self.text))
        self.assertEqual(self.box.typed, [self.text])
        self.assertEqual(self.box.accepted, [self.text])
        self.assertEqual(self.pending()['state'], 'accepted')

    def test_accepted_unconfirmed_input_is_never_resubmitted_or_expired(self):
        self.seed(age=console.INPUT_TTL + 1, accepted=True)
        console.expire_inputs()
        self.assertFalse(console.paste_into('box', 'New message.'))
        self.assertFalse(console._sent('box', self.text))
        self.assertEqual(self.box.enters, 0)
        self.assertEqual(self.box.clears, 0)
        self.box.text = ''
        self.assertTrue(console._sent('box', self.text))
        self.assertEqual(self.box.enters, 0)
        self.assertTrue(console.confirm_input('box', self.text))
        self.assertFalse(console._input_paths('box')[0].exists())

    def test_independent_identical_person_sends_and_system_notifications_both_arrive(self):
        for send in (console.paste_into, console.type_into):
            with self.subTest(sender=send.__name__):
                count = len(self.box.accepted)
                self.assertTrue(send('box', 'ok'))
                self.assertTrue(send('box', 'ok'))
                self.assertEqual(self.box.accepted[count:], ['ok', 'ok'])

    def test_new_blank_incarnation_is_not_acknowledgement_of_the_recorded_write(self):
        original = self.box.after_write
        def replace_console():
            original()
            self.incarnation = 'new-server/new-session/new-pane'
            self.box.text = ''
        with patch.object(self.box, 'after_write', side_effect=replace_console):
            self.assertFalse(console.type_into('box', self.text))
        self.assertEqual(self.box.enters, 0)
        self.assertEqual(self.pending()['state'], 'unsent')

    def test_unknown_incarnation_receives_no_input(self):
        self.incarnation = None
        self.assertFalse(console.type_into('box', self.text))
        self.assertEqual(self.box.typed, [])
        self.assertEqual(self.box.enters, 0)

    def test_old_receipt_cannot_confirm_a_newer_identical_unsent_record(self):
        old = self.seed(accepted=True)
        with console._typing('box'):
            self.assertTrue(console.confirm_input('box', self.text), 'the hook must not wait for this lock')
            newer = console._record_input('box', self.text)
        self.assertNotEqual(old['id'], newer['id'])
        with console._typing('box'):
            self.assertEqual(console._pending_input('box')['id'], newer['id'])
        self.assertFalse(console.confirm_input('box', self.text), 'an old identical hook cannot confirm unsent input')
        self.assertEqual(self.pending()['state'], 'unsent')

    def test_deferred_hook_receipt_is_consumed_without_resubmitting_input(self):
        self.seed(accepted=True)
        with console._typing('box'):
            self.assertTrue(console.confirm_input('box', self.text))
            self.assertTrue(console._input_paths('box')[0].exists())
        console.expire_inputs()
        self.assertFalse(console._input_paths('box')[0].exists())
        self.assertEqual(self.box.enters, 0)
        self.assertEqual(self.box.clears, 0)

    def test_event_log_failure_after_native_acceptance_does_not_report_unsent(self):
        event = console._input_event
        def failing_log(name, record, kind):
            if kind == 'accepted':
                raise OSError('fixture metadata log unavailable')
            return event(name, record, kind)
        with patch.object(console, '_input_event', side_effect=failing_log):
            self.assertTrue(console.type_into('box', 'accepted native input'))
        self.assertEqual(self.pending()['state'], 'accepted')
        self.assertEqual(self.box.accepted, ['accepted native input'])

    def test_expiry_skips_menu_busy_attached_unknown_foreign_and_new_incarnation(self):
        for obstacle in ('menu', 'busy', 'attached', 'unknown', 'foreign', 'new-incarnation'):
            with self.subTest(obstacle=obstacle):
                self.seed(age=console.INPUT_TTL + 1)
                self.box.menu = obstacle == 'menu'
                if obstacle == 'foreign':
                    self.box.text = 'An unrelated personal draft.'
                if obstacle == 'new-incarnation':
                    self.incarnation = 'different-session'
                with patch.object(console, 'busy', return_value='active work' if obstacle == 'busy' else None), \
                        patch.object(console, 'attached', return_value=obstacle == 'attached'), \
                        patch.object(console, 'draft', side_effect=(lambda name: None) if obstacle == 'unknown' else
                                     lambda name: self.box.provider.draft(self.box.screen(name))):
                    console.expire_inputs()
                self.assertEqual(self.box.enters, 0)
                self.assertEqual(self.box.clears, 0)
                self.box.menu = False
                self.incarnation = 'server-start/session-created/pane-1'

    def test_expiry_is_logged_without_text_and_private_payload_is_bounded(self):
        self.seed(age=console.INPUT_TTL + 1)
        payload, receipt, events = console._input_paths('box')
        self.assertEqual(stat.S_IMODE(payload.stat().st_mode), 0o600)
        console.expire_inputs()
        self.assertEqual(self.box.text, '')
        self.assertFalse(payload.exists())
        rows = json.loads(events.read_text())
        self.assertEqual(rows[-1]['event'], 'retracted')
        self.assertNotIn(self.text, events.read_text())
        self.assertNotIn('unfinished intent', events.read_text())
        self.assertEqual(stat.S_IMODE(events.stat().st_mode), 0o600)
        for n in range(40):
            self.seed(text='system record ' + str(n))
        self.assertEqual(len(json.loads(events.read_text())), 32)
        self.box.text = ''
        self.assertFalse(console.type_into('box', 'x' * (console.INPUT_BYTES + 1)))
        self.assertEqual(self.box.typed, [], 'oversized unrecordable input must never be typed')


if __name__ == '__main__':
    unittest.main()
