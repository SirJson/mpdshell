#!/usr/bin/env python3
from datetime import datetime
from pathlib import Path
from threading import Lock
from prompt_toolkit.completion import WordCompleter
from prompt_toolkit.contrib.regular_languages.compiler import compile
from prompt_toolkit.contrib.regular_languages.completion import GrammarCompleter
from prompt_toolkit.contrib.regular_languages.lexer import GrammarLexer
from prompt_toolkit.lexers import SimpleLexer
from prompt_toolkit.styles import Style
from prompt_toolkit import print_formatted_text as print
from prompt_toolkit import HTML
from prompt_toolkit.application import Application
from prompt_toolkit.output import ColorDepth
from prompt_toolkit.document import Document
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout.controls import FormattedTextControl, BufferControl, Buffer
from prompt_toolkit.layout.layout import Layout
from prompt_toolkit.widgets import SearchToolbar, TextArea
from prompt_toolkit.layout.containers import Float, FloatContainer, HSplit, Window
from prompt_toolkit.layout.menus import CompletionsMenu

import socket
import asyncio
import selectors
import glob
import sys
import threading
import time
import argparse

# See http://chriskempson.com/projects/base16/ for a description of the role
# of the different colors in the base16 palette.

base00 = '#000000'
base01 = '#202020'
base02 = '#303030'
base03 = '#505050'
base04 = '#b0b0b0'
base05 = '#d0d0d0'
base06 = '#e0e0e0'
base07 = '#ffffff'
base08 = '#eb008a'
base09 = '#f29333'
base0A = '#f8ca12'
base0B = '#37b349'
base0C = '#00aabb'
base0D = '#0e5a94'
base0E = '#b31e8d'
base0F = '#7a2d00'

selector = selectors.SelectSelector()
loop = asyncio.SelectorEventLoop(selector)
asyncio.set_event_loop(loop)

RECV_BUFFER_SIZE = 4096
SCRIPT_HOME = Path.home() / 'mpdscripts'

mpdcmds = [
    "add",
    "addid",
    "addtagid",
    "albumart",
    "channels",
    "clear",
    "clearerror",
    "cleartagid",
    "close",
    "commands",
    "config",
    "consume",
    "count",
    "crossfade",
    "currentsong",
    "decoders",
    "delete",
    "deleteid",
    "delpartition",
    "disableoutput",
    "enableoutput",
    "find",
    "findadd",
    "getfingerprint",
    "idle",
    "kill",
    "list",
    "listall",
    "listallinfo",
    "listfiles",
    "listmounts",
    "listneighbors",
    "listpartitions",
    "listplaylist",
    "listplaylistinfo",
    "listplaylists",
    "load",
    "lsinfo",
    "mixrampdb",
    "mixrampdelay",
    "mount",
    "move",
    "moveid",
    "moveoutput",
    "newpartition",
    "next",
    "notcommands",
    "outputs",
    "outputset",
    "partition",
    "password",
    "pause",
    "ping",
    "play",
    "playid",
    "playlist",
    "playlistadd",
    "playlistclear",
    "playlistdelete",
    "playlistfind",
    "playlistid",
    "playlistinfo",
    "playlistmove",
    "playlistsearch",
    "plchanges",
    "plchangesposid",
    "previous",
    "prio",
    "prioid",
    "random",
    "rangeid",
    "readcomments",
    "readmessages",
    "readpicture",
    "rename",
    "repeat",
    "replay_gain_mode",
    "replay_gain_status",
    "rescan",
    "rm",
    "save",
    "search",
    "searchadd",
    "searchaddpl",
    "seek",
    "seekcur",
    "seekid",
    "sendmessage",
    "setvol",
    "shuffle",
    "single",
    "stats",
    "status",
    "sticker",
    "stop",
    "subscribe",
    "swap",
    "swapid",
    "tagtypes",
    "toggleoutput",
    "unmount",
    "unsubscribe",
    "update",
    "urlhandlers",
    "volume"]

internalcmds = {
    "exec": lambda s, x: s.runscript(x),
    "scripts": lambda s, x: listscripts(s, x),
    "help": lambda s, x: apphelp(s, x),
    "mpchelp": lambda s, x: mpchelp(s, x)
}


class RepeatedTimer(object):
    def __init__(self, interval, function, *args, **kwargs):
        self._timer = None
        self.interval = interval
        self.function = function
        self.args = args
        self.kwargs = kwargs
        self.is_running = False
        self.next_call = time.time()
        self.start()

    def _run(self):
        self.is_running = False
        self.start()
        self.function(*self.args, **self.kwargs)

    def start(self):
        if not self.is_running:
            self.next_call += self.interval
            self._timer = threading.Timer(
                self.next_call - time.time(), self._run)
            self._timer.start()
            self.is_running = True

    def stop(self):
        self._timer.cancel()
        self.is_running = False


class MPDClient(object):
    def __init__(self, hostname: str, port: int):
        self.server = hostname
        self.port = port
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.socket.connect((hostname, port))
        data = self.socket.recv(RECV_BUFFER_SIZE)
        self.initmsg = str(data, 'utf-8')
        self.socket_lock = Lock()
        self.state_lock = Lock()
        self._remote_closed = False

    def runscript(self, param):
        params = param.split(' ')
        file = params[0]
        with open(SCRIPT_HOME / file) as mpcscript:
            data = mpcscript.read()
            return self.send(data)

    def disconnect(self, *argv):
        with self.socket_lock:
            try:
                self.socket.sendall(bytes('close', 'utf-8'))
            except BaseException as ex:
                print("Connection closed by remote: {}".format(ex))
            finally:
                self.socket.shutdown(socket.SHUT_WR)
                self.socket.close()

    def send(self, command: str) -> str:
        with self.socket_lock:
            self.socket.sendall(bytes(command+'\n', 'utf-8'))
        return self.receive()

    def ping(self) -> bool:
        with self.socket_lock:
            self.socket.sendall(bytes('ping\n', 'utf-8'))
            data = self.socket.recv(3)
        return data == b'OK\n'

    def ping_unchecked(self):
        with self.socket_lock:
            try:
                self.socket.sendall(bytes('ping\n', 'utf-8'))
                self.socket.recv(RECV_BUFFER_SIZE)
            except BaseException:
                with self.state_lock:
                    self._remote_closed = True

    def force_closed(self):
        with self.state_lock:
            return self._remote_closed

    def receive(self):
        chunks = []
        with self.socket_lock:
            while 1:
                data = self.socket.recv(RECV_BUFFER_SIZE)
                chunks.append(data)
                if data.find(b'OK\n') or data.find(b'ACK') or data == b'\n':
                    break
        return str(b''.join(chunks), 'utf-8')

    def close(self):
        with self.socket_lock:
            self.socket.shutdown(socket.SHUT_WR)
            self.socket.close()


def create_grammar():
    return compile(
        r"""
        (?P<exec>\![a-z]+) |
        ((?P<exec>\![a-z]+)\s(?P<execparam>[a-zA-Z0-9.\/\\\-\_\s]+)\s*) |
        (?P<func>[a-z]+) |
        ((?P<func>[a-z]+)\s(?P<params>\+?[a-zA-Z0-9.\/\:\\\-\_\s]+)\s*)
        """
    )


def mpchelp(s, param):
    output = ''
    output += "=== MPC Commands ===\n"
    for c in mpdcmds:
        output += str(c) + "\n"
    return output


def apphelp(s, param):
    output = ''
    output += "=== Shell Commands ===\n"
    for c in internalcmds.keys():
        output += str(c) + "\n"
    return output


def listscripts(s, param):
    output = ''
    for file in SCRIPT_HOME.glob("*.ncs"):
        output += ' - ' + file.name + "\n"
    return output


app_style = Style.from_dict({"function": base0D,
                             "parameter": base08,
                             "exec": base0E,
                             "execparam": base09,
                             "trailing-input": base0F,
                             "output": base0B,
                             "input": f"bg:{base01} {base04}",
                             "linetoken": base0C,
                             "line": base03,
                             "base": f"bg:{base00} {base05}"
                             })


def invalid_input(msg="Invalid command"):
    return msg


def get_line_prefix(lineno, wrap_count):
    return HTML('<linetoken><b>»</b></linetoken> ')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("host", help="The host of your MPD instance")
    parser.add_argument("-p", "--port", help="The port on which MPD is running",
                    type=int)
    args = parser.parse_args()
    port = args.port if args.port is not None else 12345
    print(f"Connecting to {args.host}@{port}...")
    mpd = MPDClient(args.host, port)
    grammar = create_grammar()
    intro_text = f"Connected to: {mpd.server}@{mpd.port} | {mpd.initmsg}"
    help_text = "Exit: [Control-C] | Scroll up: [PageUp] | Scroll down: [PageDown]"
    lexer = GrammarLexer(
        grammar,
        lexers={
            "func": SimpleLexer("class:function"),
            "params": SimpleLexer("class:parameter"),
            "exec": SimpleLexer("class:exec"),
            "execparam": SimpleLexer("class:execparam"),
        },
    )
    commands = []

    commands.extend(mpdcmds)
    words = WordCompleter(commands)
    words2 = WordCompleter(internalcmds.keys())

    completer = GrammarCompleter(
        grammar,
        {
            "func": words,
            "exec": words2
        },
    )

    search_field = SearchToolbar()  # For reverse search.

    output_field = Buffer()

    input_field = TextArea(
        height=1,
        lexer=lexer,
        completer=completer,
        prompt="❯ ",
        style="class:input",
        multiline=False,
        wrap_lines=False,
        search_field=search_field,
    )

    container = FloatContainer(
        content=HSplit(
            [
                Window(
                    FormattedTextControl(
                        intro_text
                    ),
                    height=1,
                    style="reverse",
                ),
                Window(
                    BufferControl(buffer=output_field),
                    get_line_prefix=get_line_prefix,
                    wrap_lines=False,
                    style="class:output"),
                Window(height=1, char="▁", style="class:line"),
                input_field,
                search_field,
                Window(height=1, char="▔", style="class:line"),
                Window(
                    FormattedTextControl(
                        help_text
                    ),
                    height=1,
                    style="reverse",
                ),
            ]
        ),
        floats=[
            Float(
                xcursor=True,
                ycursor=True,
                content=CompletionsMenu(max_height=32, scroll_offset=1),
            )
        ],
        style="class:base"
    )

    def indent(text: str, spaces=4):
        output = ''
        for l in text.splitlines():
            output += ' ' * 4 + l + '\n'
        return output

    def accept(buff):
        if mpd.force_closed():
            application.exit(result="Connection reset by peer")
        isonow = datetime.now().isoformat(timespec='seconds')
        output = f'\n[{isonow}]\n'
        try:
            match = grammar.match(buff.text)
            if match:
                params = match.variables()
                execcmd = params.get("exec")
                if execcmd is not None:
                    params = params.get("execparam")
                    funcptr = internalcmds.get(
                        execcmd[1:], lambda s, x:  indent(invalid_input("Unknown internal command")))
                    output += indent(funcptr(mpd, params))
                else:
                    cmd = params.get("func")
                    if cmd not in mpdcmds:
                        output += indent(invalid_input())
                    else:
                        output += indent(mpd.send(buff.text))
                        if buff.text == "close":
                            application.exit()
            else:
                output += indent(invalid_input())
        except BaseException as e:
            tb = sys.exc_info()[2]
            output = "\n\nError: {}\n\tFrame: {}\n\tInstruction: {}\n\tLine: {}".format(
                e, tb.tb_frame, tb.tb_lasti, tb.tb_lineno)
        new_text = output_field.text + output

        # Add text to output buffer.
        output_field.document = Document(
            text=new_text, cursor_position=len(new_text)
        )

    input_field.accept_handler = accept

    # The key bindings.
    kb = KeyBindings()

    @kb.add("pageup")
    def onpageup(event):
        output_field.cursor_position -= 500

    @kb.add("pagedown")
    def onpagedown(event):
        output_field.cursor_position += 500

    @kb.add("c-c")
    @kb.add("c-q")
    def _(event):
        " Pressing Ctrl-Q or Ctrl-C will exit the user interface. "
        event.app.exit()

    autoping = RepeatedTimer(3.0, lambda x: x.ping_unchecked(), mpd)
    # Run application.
    application = Application(
        layout=Layout(container, focused_element=input_field),
        key_bindings=kb,
        style=app_style,
        mouse_support=True,
        full_screen=True,
        enable_page_navigation_bindings=False,
        color_depth=ColorDepth.TRUE_COLOR
    )

    application.run()
    autoping.stop()
    mpd.disconnect()


if __name__ == '__main__':
    main()
