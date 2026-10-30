"""Delivery acknowledgements against styled native input boxes, without native agents."""
from contextlib import ExitStack
import os
from pathlib import Path
import subprocess
import tempfile
import textwrap
import threading
import time
import unittest
from unittest.mock import patch

from colony import console, providers

_REAL_SLEEP = time.sleep


class InputBox:
    """A Codex input box: long input becomes a preview and Enter may be swallowed."""
    def __init__(self, compressed=True, swallow=0, change=None):
        self.provider = providers.Codex()
        self.text = ''
        self.compressed = compressed
        self.swallow = swallow
        self.change = change
        self.alive = True
        self.visible = True
        self.choice = False
        self.buffer = ''
        self.enters = 0
        self.accepted = []
        self.typed = []
        self.guard = threading.Lock()
        self.slow = False
        self.redraws = 0

    def screen(self, name):
        if self.choice:
            return 'Needs approval\n› 1. Allow\n  2. Deny'
        if not self.visible:
            return 'Drawing the interface…'
        if self.redraws:
            self.redraws -= 1
            return 'Drawing the interface…'
        text = self.text
        if self.compressed and len(text) > 80:
            count = min(1024, len(text))
            # The real failure displayed a marker plus a normal suffix, wrapped
            # across a phone-width console; draft() reads its first prompt row.
            marker = f'[Pasted Content {count} chars]'
            suffix = text[count:]
            width = max(0, 43 - len(marker))
            text = ('\x1b[38;5;6m' + marker + '\x1b[39m' + suffix[:width]
                    + ('\n  ' + suffix[width:] if suffix[width:] else ''))
        elif '\n' in text:
            text = text.split('\n')[0]
        return '› ' + text + '\n  footer'

    def run(self, args, **kwargs):
        if args[:2] == ['tmux', 'capture-pane']:
            return subprocess.CompletedProcess(args, 0, self.screen('box'), '')
        if args[:2] == ['tmux', 'load-buffer']:
            self.buffer = kwargs.get('input', '')
        elif args[:2] == ['tmux', 'paste-buffer']:
            self.text += self.buffer
            self.typed.append(self.buffer)
            self.after_write()
        elif args[:2] == ['tmux', 'send-keys'] and '-X' not in args:
            if '-l' in args:
                self.typed.append(args[-1])
                for char in args[-1]:
                    self.text += char
                    if self.slow:
                        _REAL_SLEEP(.0001)
                self.after_write()
            elif args[-1] == 'Enter':
                self.enters += 1
                if self.change == 'human':
                    self.text = 'a different human draft'
                    self.change = None
                elif self.change == 'unknown':
                    self.visible = False
                elif self.swallow:
                    self.swallow -= 1
                elif self.text:
                    self.accepted.append(self.text)
                    self.text = ''
        return subprocess.CompletedProcess(args, 0, '', '')

    def after_write(self):
        if self.change == 'choice-before-enter':
            self.choice = True
        elif self.change == 'dead-before-enter':
            self.alive = False
        elif self.change == 'unknown-before-enter':
            self.visible = False
        elif self.change == 'human-before-enter':
            self.text = 'a different human draft'

    def on(self):
        stack = ExitStack()
        for target, name, value in [
                (console, 'provider', lambda _: self.provider),
                (console, 'running', lambda _: self.alive),
                (console, 'screen', self.screen),
                (console, 'unscroll', lambda _: None),
                (console, '_input_identity', lambda name: 'fixture-console:' + name),
                (console.subprocess, 'run', self.run),
                (console.time, 'sleep', lambda seconds: _REAL_SLEEP(min(seconds, .001)))]:
            stack.enter_context(patch.object(target, name, side_effect=value))
        return stack


class ClaudeInputBox(InputBox):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.provider = providers.ClaudeCode()

    def screen(self, name):
        text = self.text
        if len(text) > 800 or text.count('\n') > 2:
            lines = text.count('\n')
            text = '[Pasted text #3' + (f' +{lines} lines' if lines else '') + ']'
        return '──────────\n❯ ' + text + '\n──────────\n'


class WrappedClaudeInputBox(ClaudeInputBox):
    def screen(self, name):
        rows = textwrap.wrap(self.text, width=44) if self.text else ['']
        return ('Previous reply.\n' + '─' * 48 + '\n❯ ' + '\n  '.join(rows) + '\n' + '─' * 48
                + '\n  ⏵⏵ bypass permissions on (shift+tab to cycle)\n')


class SendTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        env = patch.dict(os.environ, COLONY_BOARD_HOME=str(Path(self.tmp.name) / 'board'))
        env.start()
        self.addCleanup(env.stop)

    def test_long_single_line_preview_retries_swallowed_enter_and_accepts_once(self):
        text = 'a' * 1024 + ' original suffix ' * 8
        box = InputBox(swallow=1)
        with box.on():
            self.assertTrue(console.paste_into('box', text))
        self.assertEqual(box.accepted, [text])
        self.assertEqual(box.enters, 2)
        self.assertEqual(box.typed, [text])

    def test_permanently_stuck_preview_is_not_reported_sent(self):
        text = 's' * 1155
        box = InputBox(swallow=100)
        with box.on():
            self.assertFalse(console.paste_into('box', text))
        self.assertEqual(box.accepted, [])
        self.assertEqual(box.text, text)
        self.assertEqual(box.enters, 4)

    def test_missing_prompt_is_unknown_and_receives_no_retry(self):
        box = InputBox(change='unknown')
        with box.on():
            self.assertFalse(console.paste_into('box', 'the original message'))
        self.assertEqual(box.enters, 1)
        self.assertEqual(box.accepted, [])
        self.assertEqual(box.text, 'the original message')

    def test_transient_redraw_can_finish_without_being_mistaken_for_delivery(self):
        box = InputBox()
        box.text = 'pending'
        box.redraws = 1
        with box.on():
            self.assertTrue(console._sent('box', 'pending'))
        self.assertEqual(box.accepted, ['pending'])
        self.assertEqual(box.enters, 1)

    def test_human_replacement_is_neither_submitted_nor_an_acknowledgement(self):
        box = InputBox(change='human')
        with box.on():
            self.assertFalse(console.paste_into('box', 'the original message'))
        self.assertEqual(box.text, 'a different human draft')
        self.assertEqual(box.enters, 1)
        self.assertEqual(box.accepted, [])

    def test_choice_dead_unknown_and_foreign_before_initial_enter_are_left_alone(self):
        for change in ('choice-before-enter', 'dead-before-enter', 'unknown-before-enter', 'human-before-enter'):
            with self.subTest(change=change):
                box = InputBox(change=change)
                with box.on():
                    self.assertFalse(console.paste_into('box', 'a message'))
                self.assertEqual(box.enters, 0)
                self.assertEqual(box.accepted, [])

    def test_retries_never_press_enter_in_a_choice_or_dead_console(self):
        for blocked in ('choice', 'dead'):
            with self.subTest(blocked=blocked):
                box = InputBox()
                box.text = 'pending'
                box.choice = blocked == 'choice'
                box.alive = blocked != 'dead'
                with box.on():
                    self.assertFalse(console._sent('box', 'pending'))
                self.assertEqual(box.enters, 0)

    def test_short_single_line_and_multiline_preserve_their_text(self):
        for text in ('A short message', 'first line\nsecond line', 'first line\n' + 'm' * 1200):
            with self.subTest(chars=len(text)):
                box = InputBox(swallow=1)
                with box.on():
                    self.assertTrue(console.paste_into('box', text))
                self.assertEqual(box.accepted, [text])
                self.assertEqual(box.typed, [text])

    def test_existing_human_draft_is_never_overwritten(self):
        box = InputBox()
        box.text = 'an existing draft'
        with box.on():
            self.assertFalse(console.paste_into('box', 'another message'))
        self.assertEqual(box.text, 'an existing draft')
        self.assertEqual(box.typed, [])
        self.assertEqual(box.enters, 0)

    def test_simultaneous_senders_arrive_as_two_whole_messages(self):
        box = InputBox()
        box.slow = True
        first = '[colony] ' + 'f' * 150
        second = 'the other message\nwith a second line'
        results = []
        with box.on():
            worker = threading.Thread(target=lambda: results.append(console.type_into('box', first)))
            worker.start()
            until = time.monotonic() + 2
            while not box.text and time.monotonic() < until:
                _REAL_SLEEP(.001)
            results.append(console.paste_into('box', second))
            worker.join(timeout=3)
        self.assertFalse(worker.is_alive())
        self.assertEqual(results, [True, True])
        self.assertEqual(box.accepted, [first, second])

    def test_codex_native_preview_matches_count_and_visible_suffix(self):
        provider = providers.Codex()
        text = 'p' * 1024 + ' expected suffix'
        styled = '› \x1b[38;5;6m[Pasted Content 1024 chars]\x1b[39m expected\n  footer'
        left = provider.draft(styled)
        self.assertTrue(provider.pending_input(text, left))
        self.assertTrue(provider.pending_input(text, '[Pasted Content 1024 chars]'))
        self.assertTrue(provider.pending_input(text, '[Pasted Content 1024 chars] #2 expected'))
        self.assertTrue(provider.pending_input('abcdef', 'ab[Pasted Content 2 chars]ef'))
        self.assertFalse(provider.pending_input(text, '[Pasted Content 1024 chars] foreign suffix'))
        self.assertFalse(provider.pending_input(text, '[Pasted Content 9999 chars]'))
        self.assertFalse(provider.pending_input(text, '[Pasted Content 1024 cha'))
        self.assertFalse(provider.pending_input(text, '[Pasted Content 0 chars]'))
        self.assertFalse(provider.pending_input(text, '[Pasted Content -1 chars]'))

    def test_plain_prefix_ownership_uses_the_whole_visible_draft(self):
        provider = providers.Provider()
        self.assertTrue(provider.pending_input('one message', 'one mess'))
        self.assertFalse(provider.pending_input('same prefix ' * 4 + 'ours', 'same prefix ' * 4 + 'human'))
        self.assertFalse(provider.pending_input('text', ''))
        self.assertFalse(provider.pending_input('text', None))

    def test_claude_physical_wraps_and_native_paste_pills(self):
        provider = providers.ClaudeCode()
        for text, visible in [
                ('some words wrap here', 'some words\n  wrap here'),
                ('abcdefghijk', 'abcde\n  fghijk'),
                ('line one\nline two', 'line one\n  line two'),
                ('long single line', '[Pasted text #1]'),
                ('first\nsecond\nthird', '[Pasted text #3 +2 lines]'),
                ('first\r\nsecond', '[Pasted text #3 +1 lines]'),
                ('abcde longwordabcdefghij ending', 'abcde\n  longwordabc\n  defghij ending'),
                ('caf' + 'e\u0301', 'café')]:
            with self.subTest(text_chars=len(text), visible_chars=len(visible)):
                self.assertTrue(provider.pending_input(text, visible))
        for text, visible in [
                ('first\nsecond', '[Pasted text #1 +2 lines]'),
                ('same prefix original', 'same prefix changed'),
                ('single line', '[Pasted text #1 +1 lines]'),
                ('first\nsecond expected', '[Pasted text #1 +1 lines] foreign'),
                ('prefix first\nsecond suffix', 'prefix [Pasted text #4 +1 lines] suffix'),
                ('same  words', 'same words')]:
            with self.subTest(text_chars=len(text), visible_chars=len(visible)):
                self.assertFalse(provider.pending_input(text, visible))

    def test_claude_collapsed_pastes_use_the_same_guarded_send_sequence(self):
        for text in ('c' * 1155, 'first\nsecond\nthird\nfourth'):
            with self.subTest(chars=len(text)):
                box = ClaudeInputBox(swallow=1)
                with box.on():
                    self.assertTrue(console.paste_into('box', text))
                self.assertEqual(box.accepted, [text])
                self.assertEqual(box.enters, 2)

    def test_claude_wrapped_carry_and_human_input_are_not_choices_and_send_once(self):
        carry = ('(90bf5b9fb7b24adf) [colony] Continue the agreed checkpoint. '
                 'Keep the current conversation and verify the remaining work before reporting it complete. ')
        carry = (carry * 4)[:472]
        human = ('Please review the change and explain what happens when the board is restarted. '
                 'Keep the active conversation available and check the result on a phone. ' * 2)
        for text in (carry, human):
            with self.subTest(kind='carry' if text == carry else 'human'):
                box = WrappedClaudeInputBox()
                box.text = text
                screen = box.screen('box')
                self.assertIsNone(box.provider.choice(screen), 'physical input wraps are not menu options')
                self.assertIsNone(box.provider.choice('Old question\n❯ 1. Yes\n  2. No\n' + screen),
                                  'an older numbered choice cannot override the current typing box')
                self.assertTrue(box.provider.pending_input(text, box.provider.draft(screen)))
                with box.on():
                    self.assertTrue(console._sent('box', text))
                self.assertEqual(box.enters, 1)
                self.assertEqual(box.accepted, [text])
                self.assertEqual(box.provider.draft(box.screen('box')), '', 'acknowledge only the empty native box')

    def test_claude_unnumbered_menus_below_rules_still_block_owned_sends(self):
        rule = '─' * 48
        for suffix in ('', '\n' + rule):
            screen = (rule + '\n❯ No, exit\n  Yes, I trust this folder' + suffix
                      + '\n\nEnter to confirm · ↑/↓ to navigate · Esc to cancel')
            with self.subTest(closing_rule=bool(suffix)):
                provider = providers.ClaudeCode()
                self.assertEqual(provider.choice(screen)[1:], (['No, exit', 'Yes, I trust this folder'], 0))
                box = WrappedClaudeInputBox()
                box.text = 'No, exit'
                with box.on(), patch.object(console, 'screen', return_value=screen):
                    self.assertFalse(console._sent('box', box.text))
                self.assertEqual(box.enters, 0)
