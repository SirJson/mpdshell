#!/usr/bin/env python3
import argparse
import asyncio
from re import DEBUG
import selectors
import socket
import sys
import threading
import time
import selectors
from datetime import datetime
from pathlib import Path
from threading import Lock
from typing import List

from prompt_toolkit import HTML
from prompt_toolkit import print_formatted_text as print
from prompt_toolkit.application import Application
from prompt_toolkit.completion import WordCompleter
from prompt_toolkit.contrib.regular_languages.compiler import compile
from prompt_toolkit.contrib.regular_languages.completion import \
    GrammarCompleter
from prompt_toolkit.contrib.regular_languages.lexer import GrammarLexer
from prompt_toolkit.document import Document
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout.containers import (Float, FloatContainer, HSplit,
                                              Window)
from prompt_toolkit.layout.controls import (Buffer, BufferControl,
                                            FormattedTextControl)
from prompt_toolkit.layout.layout import Layout
from prompt_toolkit.layout.menus import CompletionsMenu
from prompt_toolkit.lexers import SimpleLexer
from prompt_toolkit.output import ColorDepth
from prompt_toolkit.styles import Style
from prompt_toolkit.widgets import SearchToolbar, TextArea


selector = selectors.SelectSelector()
loop = asyncio.SelectorEventLoop(selector)
asyncio.set_event_loop(loop)

RECV_BUFFER_SIZE = 4096
SCRIPT_HOME = Path.home() / 'mpdscripts'
DEBUGAPP = False
NOECHO = False

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
        self.selector = selectors.DefaultSelector()
        self._inbuffer = []
        self._outbuffer = []
        self._echobuffer = []
        self.server = hostname
        self.port = port
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

        self.socket.connect((hostname, port))

        data = self.socket.recv(RECV_BUFFER_SIZE)
        self.initmsg = str(data, 'utf-8')
        self.socket_lock = Lock()
        self.state_lock = Lock()
        self._io_lock = Lock()
        self.socket.setblocking(False)
        self.selector.register(
            self.socket, selectors.EVENT_READ | selectors.EVENT_WRITE, self.onsocketready)
        self._remote_closed = False
        self.dbg_lastmask = 0x0

    def data_available(self) -> bool:
        with self._io_lock:
            return len(self._inbuffer) > 0

    def echo_available(self) -> bool:
        with self._io_lock:
            return len(self._echobuffer) > 0

    def peek_inbuffer(self) -> int:
        return len(self._inbuffer)

    def peek_outbuffer(self) -> int:
        return len(self._outbuffer)

    def peek_echobuffer(self) -> int:
        return len(self._echobuffer)

    def pop_message(self) -> str:
        with self._io_lock:
            msg = self._inbuffer.pop()
            if str(msg).strip() == 'OK':
                return None
            else:
                return msg

    def pop_echo(self) -> str:
        with self._io_lock:
            return self._echobuffer.pop()

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

    def ping(self) -> bool:
        self.send('ping')

    def ping_unchecked(self):
        try:
            self.send('ping')
        except BaseException:
            with self.state_lock:
                self._remote_closed = True

    def force_closed(self):
        with self.state_lock:
            return self._remote_closed

    def poll(self):
        events = self.selector.select()
        for key, mask in events:
            callback = key.data
            callback(key.fileobj, mask)

    def send(self, message: str):
        with self._io_lock:
            self._outbuffer.append(message)

    def onsocketready(self, connection, mask):
        self.dbg_lastmask = mask
        if mask & selectors.EVENT_READ:
            self._receive(connection)
        if mask & selectors.EVENT_WRITE:
            self._transmit(connection)

    def _receive(self, connection):
        chunks = []
        with self._io_lock:
            data = connection.recv(RECV_BUFFER_SIZE)
            if data:
                chunks.append(data)
        self._inbuffer.append(str(b''.join(chunks), 'utf-8'))

    def _transmit(self, connection):
        with self._io_lock:
            while len(self._outbuffer) > 0:
                msg = self._outbuffer.pop()
                command = str(msg + '\n')
                connection.sendall(bytes(command, 'utf-8'))

    def local_echo(self, message):
        with self._io_lock:
            self._echobuffer.append(message)

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


def mpchelp(mpd, _param):
    output = ''
    output += "=== MPC Commands ===\n"
    for c in mpdcmds:
        output += str(c) + "\n"
    mpd.local_echo(output)


def apphelp(mpd, _param):
    output = ''
    output += "=== Shell Commands ===\n"
    for c in internalcmds.keys():
        output += str(c) + "\n"
    mpd.local_echo(output)


def listscripts(mpd, _param):
    output = f'=== Available mpd shell scripts in "{SCRIPT_HOME}" ==='
    files = list(SCRIPT_HOME.glob("*.ncs"))
    for file in files:
        output += '   - ' + file.name + "\n"
    output += f'\n\n----\n=> Total: {len(files)}'
    mpd.local_echo(output)


def gen_style() -> Style:
    base00 = '#000000'
    base01 = '#202020'
    base02 = '#303030'
    base03 = '#505050'
    base04 = '#909090'
    base05 = '#bfbfbf'
    base06 = '#e0e0e0'
    base07 = '#ffffff'
    base08 = '#eb008a'
    base09 = '#f29333'
    base0A = '#f8ca12'
    base0B = '#FF6236'
    base0C = '#00aabb'
    base0D = '#0e5a94'
    base0E = '#b31e8d'
    base0F = '#7a2d00'
    baseA0 = '#242424'
    baseA1 = '#06A191'
    return Style.from_dict(
        {
            "function": base0D,
            "parameter": base08,
            "exec": base0E,
            "execparam": base09,
            "trailing-input": base0F,
            "output": base0B,
            "debug": f"bg:{base01} {base0A}",
            "input": f"bg:{base01} {base04}",
            "linetoken": base0C,
            "line": base03,
            "base": f"bg:{baseA0} {base05}",
            "toolbar": f"bg:{base01} {baseA1}",
            "title": f"bg:{base02} #90A4AE",
            "c1": "#FF5722",
            "c2": "#D4E157",
            "c3": "#9575CD",
            "c4": "#4CAF50",
            "c5": "#9C27B0"
        })


def invalid_input(msg="Invalid command"):
    return msg


def get_line_prefix(lineno, wrap_count):
    return HTML('<linetoken><b>»</b></linetoken> ')


def get_netdbg_prefix(lineno, wrap_count):
    return HTML('<linetoken>NETTICK: </linetoken> ')


def get_socketdbg_prefix(lineno, wrap_count):
    return HTML('<linetoken>SOCKET:</linetoken> ')


def get_echodbg_prefix(lineno, wrap_count):
    return HTML('<linetoken>SYSECHO:</linetoken> ')


def main():
    global DEBUGAPP, NOECHO
    parser = argparse.ArgumentParser()
    parser.add_argument("host", help="The host of your MPD instance")
    parser.add_argument("-p", "--port", help="The port on which MPD is running (default: 6600)",
                        type=int, default=6600, required=False)
    parser.add_argument("-s", "--secret", help="Initialize connection with this password (default: None)",
                        type=str, required=False)
    parser.add_argument("-d", "--debug", help="Show internal debug info (default: 0)",
                        type=bool, default=False, required=False)
    parser.add_argument("-a", "--alive-tick", help="How many seconds between a keep a live should be waited. (default: 3)",
                        type=int, default=3, required=False)
    parser.add_argument("-n", "--no-echo", help="Own commands don't get written into the output view (default: 0)",
                        type=bool, default=False, required=False)
    parser.add_argument("-b", "--buffer-size", help="The size of one TCP buffer. A message might get broken into multiple buffer if the size isn't big enough or your network can't support it. For optimal performance choose a size with the power of two. (default: 4096)",
                        type=int, default=4096, required=False)

    args = parser.parse_args()
    DEBUGAPP = args.debug
    alive_tick = args.alive_tick
    port = args.port
    print(f"Connecting to {args.host}@{port}...")
    mpd = MPDClient(args.host, port)

    grammar = create_grammar()
    intro_text = HTML(f"Connected to: <c1>{mpd.server}@{mpd.port}</c1> ❯ <c2>{mpd.initmsg}</c2>")
    client_settings =  HTML(f"Keep alive tick: <c4>{alive_tick}</c4> | TCP buffer: <c4>{RECV_BUFFER_SIZE}</c4> | Echo enabled: <c4>{str(not NOECHO)}</c4>")
    help_text =  HTML(f"Exit: <c4>[Control-C]</c4> | Scroll up: <c4>[PageUp]</c4> | Scroll down: <c4>[PageDown]</c4> | App command prefix: <c4>[!]</c4> <b>(try !help)</b>")
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
    keywords = WordCompleter(commands)
    intern_keywords = WordCompleter(internalcmds.keys())

    completer = GrammarCompleter(
        grammar,
        {
            "func": keywords,
            "exec": intern_keywords
        },
    )

    search_field = SearchToolbar()  # For reverse search.

    output_field = Buffer()

    netdbg_buffer = Buffer()
    socketdbg_buffer = Buffer()
    echodbg_buffer = Buffer()

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

    lineup = Window(height=1, char="▁", style="class:line")
    linedown = Window(height=1, char="▔", style="class:line")

    debugnotice = Window(
        FormattedTextControl(
            HTML("<b>== Debug Info ==</b>")
        ),
        height=1,
        style="class:title",
    )

    nettickwnd = Window(
        BufferControl(buffer=netdbg_buffer),
        height=1,
        get_line_prefix=get_netdbg_prefix,
        wrap_lines=False,
        style="class:debug")
    socketwnd = Window(
        BufferControl(buffer=socketdbg_buffer),
        height=1,
        get_line_prefix=get_socketdbg_prefix,
        wrap_lines=False,
        style="class:debug")
    echownd = Window(
        BufferControl(buffer=echodbg_buffer),
        height=1,
        get_line_prefix=get_echodbg_prefix,
        wrap_lines=False,
        style="class:debug")

    debugzone = HSplit([])

    if args.debug:
        debugzone = HSplit([
            lineup,
            debugnotice,
            lineup,
            nettickwnd,
            socketwnd,
            echownd,
            linedown])

    container = FloatContainer(
        content=HSplit(
            [
                
                Window(
                    FormattedTextControl(
                        intro_text
                    ),
                    height=1,
                    style="class:title",
                ),
                Window(
                    FormattedTextControl(
                        client_settings
                    ),
                    height=1,
                    style="class:title",
                ),
                linedown,
                debugzone,
                Window(
                    BufferControl(buffer=output_field),
                    get_line_prefix=get_line_prefix,
                    wrap_lines=False,
                    style="class:output"),
                lineup,
                input_field,
                search_field,
                linedown,
                lineup,
                Window(
                    FormattedTextControl(
                        help_text
                    ),
                    height=1,
                    style="class:toolbar",
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

    def netdebug_print(msg):
        if not DEBUGAPP:
            return
        netdbg_buffer.document = Document(
            text=msg, cursor_position=0
        )

    def sockdebug_print(msg):
        if not DEBUGAPP:
            return
        socketdbg_buffer.document = Document(
            text=msg, cursor_position=0
        )

    def echodbg_print(msg):
        if not DEBUGAPP:
            return
        echodbg_buffer.document = Document(
            text=msg, cursor_position=0
        )

    def indent(text: str, spaces=2):
        output = ''
        for l in text.splitlines():
            output += ' ' * spaces + l + '\n'
        return output

    def accept(buff):
        if mpd.force_closed():
            application.exit(result="Connection reset by peer")
        try:
            match = grammar.match(buff.text)
            if match:
                params = match.variables()
                execcmd = params.get("exec")
                if execcmd is not None:
                    params = params.get("execparam")
                    funcptr = internalcmds.get(
                        execcmd[1:], lambda s, x:  invalid_input("Unknown internal command"))
                    funcptr(mpd, params)
                else:
                    cmd = params.get("func")
                    if cmd not in mpdcmds:
                        mpd.local_echo(invalid_input())
                    else:
                        mpd.local_echo(buff.text)
                        mpd.send(buff.text)

                        if buff.text == "close":
                            application.exit()
            else:
                mpd.local_echo(invalid_input())
        except BaseException as e:
            tb = sys.exc_info()[2]
            mpd.local_echo("\n\nError: {}\n\tFrame: {}\n\tInstruction: {}\n\tLine: {}".format(
                e, tb.tb_frame, tb.tb_lasti, tb.tb_lineno))

    input_field.accept_handler = accept

    # The key bindings.
    kb = KeyBindings()

    @kb.add("pageup")
    def onpageup(_event):
        output_field.cursor_position -= 500

    @kb.add("pagedown")
    def onpagedown(_event):
        output_field.cursor_position += 500

    @kb.add("c-c")
    @kb.add("c-q")
    def _(event):
        """Pressing Ctrl-Q or Ctrl-C will exit the user interface."""
        event.app.exit()

    ####
    # Here happens the main loop sort of
    ####
    def netpoll():
        if not mpd:
            return

        sockdebug_print(
            f"[{ datetime.now().isoformat()}] mask: {mpd.dbg_lastmask}")
        mpd.poll()

        netdebug_print(
            f"[{ datetime.now().isoformat()}] netbuffer: input({mpd.peek_inbuffer()}) output({mpd.peek_outbuffer()})")
        echodbg_print(
            f"[{ datetime.now().isoformat()}] echobuffer: {mpd.peek_echobuffer()}")

        ####################################
        # SECTION: READBACK COLLECT
        ####################################
        sockdebug_print(
            f"[{ datetime.now().isoformat()}] mask: {mpd.dbg_lastmask}")

        recv_output = ''
        while mpd.data_available():
            message = mpd.pop_message()
            if message:
                isonow = datetime.now().isoformat(timespec='seconds')
                recv_output += indent(f'\n[{isonow}] {message}\n')
            netdebug_print(
                f"[{ datetime.now().isoformat()}] netbuffer (DRAIN): input({mpd.peek_inbuffer()}) output({mpd.peek_outbuffer()})")
        ####################################
        # SECTION: READBACK ECHOs
        ####################################
        sockdebug_print(
            f"[{ datetime.now().isoformat()}] mask: {mpd.dbg_lastmask}")

        local_output = ''
        while mpd.echo_available():
            echodbg_print(
                f"[{ datetime.now().isoformat()}] echobuffer (DRAIN): {mpd.peek_echobuffer()}")
            echomsg = mpd.pop_echo()
            if echomsg:
                isonow = datetime.now().isoformat(timespec='seconds')
                local_output += f'\n{echomsg}\n'
            echodbg_print(
                f"[{ datetime.now().isoformat()}] echobuffer (DRAIN): {mpd.peek_echobuffer()}")
        
        ####################################
        # SECTION WRITE TO TTY
        ####################################
        sockdebug_print(
            f"[{ datetime.now().isoformat()}] mask: {mpd.dbg_lastmask}")

        new_text = output_field.text
        if recv_output != '':
            netdebug_print(
                f"[{ datetime.now().isoformat()}] netbuffer (DRAW): input({mpd.peek_inbuffer()}) output({mpd.peek_outbuffer()})")
            echodbg_print(
                f"[{ datetime.now().isoformat()}] echobuffer (DRAW): {mpd.peek_echobuffer()}")
            new_text += recv_output

        if local_output != '':
            netdebug_print(
                f"[{ datetime.now().isoformat()}] netbuffer (DRAW): input({mpd.peek_inbuffer()}) output({mpd.peek_outbuffer()})")
            echodbg_print(
                f"[{ datetime.now().isoformat()}] echobuffer (DRAW): {mpd.peek_echobuffer()}")
            new_text += local_output

        if recv_output != '' or local_output != '':
            output_field.document = Document(
                text=new_text, cursor_position=len(new_text))
            application.invalidate()

        ####################################
        # netpoll() end
        ####################################

    autoping = RepeatedTimer(3.0, lambda x: x.ping_unchecked(), mpd)
    autopoll = RepeatedTimer(1.0, netpoll)
    # Run application.
    application = Application(
        layout=Layout(container, focused_element=input_field),
        key_bindings=kb,
        style=gen_style(),

        mouse_support=True,
        full_screen=True,
        enable_page_navigation_bindings=False,
        color_depth=ColorDepth.TRUE_COLOR
    )

    if args.secret is not None:
        mpd.send(f"password {args.secret}")

    application.run()
    autoping.stop()
    autopoll.stop()
    mpd.disconnect()


if __name__ == '__main__':
    main()
