# -*- coding: utf-8 -*-
from __future__ import unicode_literals
import os.path
import re
import pipes
import tempfile
from six import text_type

from keystoneclient.exceptions import ClientException, HttpError

from pygments.token import Token

from prompt_toolkit import prompt
from prompt_toolkit.history import FileHistory
from prompt_toolkit.key_binding.manager import KeyBindingManager

from ..resource import RootCollection
from ..completer import ShellCompleter
from ..exceptions import CommandError, CollectionNotFound, \
    ResourceNotFound, CommandNotFound, NotFound
from ..command import Command, Arg
from ..client import ContrailAPISession
from ..utils import CONFIG_DIR, printo
from ..style import default as default_style
from ..manager import CommandManager
from ..context import Context


class ShellAliases(object):

    def __init__(self):
        self._aliases = {}

    def set(self, alias):
        if '=' not in alias:
            raise CommandError('Alias %s is incorrect' % alias)
        alias, cmd = alias.split('=')
        self._aliases[alias.strip()] = cmd.strip()

    def get(self, alias):
        return self._aliases.get(alias, alias)

    def apply(self, cmd):
        cmd = re.split(r'(\s+)', cmd)
        cmd = [self.get(c) for c in cmd]
        return ' '.join(cmd)


class Shell(Command):
    description = "Run an interactive shell"

    def __call__(self):

        def get_prompt_tokens(cli):
            return [
                (Token.Username, ContrailAPISession.user or ''),
                (Token.At, '@' if ContrailAPISession.user else ''),
                (Token.Host, ContrailAPISession.host),
                (Token.Colon, ':'),
                (Token.Path, text_type(Context().shell.current_path)),
                (Token.Pound, '> ')
            ]

        key_bindings_registry = KeyBindingManager.for_prompt().registry
        manager = CommandManager()
        manager.load_namespace('contrail_api_cli.shell_command')
        completer = ShellCompleter()
        history = FileHistory(os.path.join(CONFIG_DIR, 'history'))
        cmd_aliases = ShellAliases()
        for cmd_name, cmd in manager.list:
            map(cmd_aliases.set, cmd.aliases)
        # load home resources to have them in cache
        # also build shortcut list for resource types
        # typing vmi/ will be expanded to virtual-machine-interface/
        # automagically
        res_aliases = ShellAliases()
        try:
            for c in RootCollection(fetch=True):
                short_name = "".join([p if p == "ip" else p[0].lower()
                                      for p in c.type.split('-')])
                res_aliases.set("%s = %s" % (short_name, c.type))
        except ClientException as e:
            return text_type(e)

        def _(event, aliases, char):
            b = event.cli.current_buffer
            w = b.document.get_word_before_cursor()
            if w is not None:
                if not w == aliases.get(w):
                    b.delete_before_cursor(count=len(w))
                    b.insert_text(aliases.get(w))
            b.insert_text(char)

        @key_bindings_registry.add_binding(' ')
        def _sa(event):
            _(event, cmd_aliases, ' ')

        @key_bindings_registry.add_binding('/')
        def _ra(event):
            _(event, res_aliases, '/')

        while True:
            try:
                action = prompt(get_prompt_tokens=get_prompt_tokens,
                                history=history,
                                completer=completer,
                                style=default_style,
                                key_bindings_registry=key_bindings_registry)
                action = cmd_aliases.apply(action)
            except (EOFError, KeyboardInterrupt):
                break
            try:
                action = action.split('|')
                pipe_cmds = action[1:]
                action = action[0].split()
                cmd = manager.get(action[0])
                args = action[1:]
                if pipe_cmds:
                    p = pipes.Template()
                    for pipe_cmd in pipe_cmds:
                        p.append(str(pipe_cmd.strip()), '--')
                    cmd.is_piped = True
                else:
                    cmd.is_piped = False
            except IndexError:
                continue
            except CommandNotFound as e:
                printo(text_type(e))
                continue
            try:
                result = cmd.parse_and_call(*args)
            except (HttpError, ClientException, CommandError,
                    ResourceNotFound, CollectionNotFound, NotFound) as e:
                printo(text_type(e))
                continue
            except KeyboardInterrupt:
                continue
            except EOFError:
                break
            else:
                if not result:
                    continue
                elif pipe_cmds:
                    t = tempfile.NamedTemporaryFile('r')
                    with p.open(t.name, 'w') as f:
                        f.write(result)
                    printo(t.read().strip())
                else:
                    printo(result)


class Cd(Command):
    description = "Change resource context"
    path = Arg(nargs="?", help="Resource path", default='',
               metavar='path', complete="collections::path")

    def __call__(self, path=''):
        Context().shell.current_path = Context().shell.current_path / path


class Exit(Command):
    description = "Exit from shell"

    def __call__(self):
        raise EOFError


class Help(Command):
    description = "List all available commands"

    def __call__(self):
        commands = CommandManager()
        return "Available commands: %s" % " ".join(
            [name for name, cmd in commands.list])
