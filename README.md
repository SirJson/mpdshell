# mpdshell
A shell-like application for mpd. Lets you control your Music Player Daemon instance [with the raw protocol commands](https://www.musicpd.org/doc/html/protocol.html). 

Includes protocol autocomplete, a basic lexer, a history, and an basic batch script interpreter.

![image-20200810085052793](README.assets/image-20200810085052793.png)

## Dependencies

-   Python 3.9 or Python 3.8 (might work with 3.7 or 3.6 as well)
-   prompt_toolkit
-   A mpd instance to connect to

## Usage

```
usage: mpdshell.py [-h] [-p PORT] [-s SECRET] [-d DEBUG] [-a ALIVE_TICK] [-n NO_ECHO] [-b BUFFER_SIZE] host

positional arguments:
  host                  The host of your MPD instance

optional arguments:
  -h, --help            show this help message and exit
  -p PORT, --port PORT  The port on which MPD is running (default: 6600)
  -s SECRET, --secret SECRET
                        Initialize connection with this password (default: None)
  -d DEBUG, --debug DEBUG
                        Show internal debug info (default: 0)
  -a ALIVE_TICK, --alive-tick ALIVE_TICK
                        How many seconds between a keep a live should be waited. (default: 3)
  -n NO_ECHO, --no-echo NO_ECHO
                        Own commands don't get written into the output view (default: 0)
  -b BUFFER_SIZE, --buffer-size BUFFER_SIZE
                        The size of one TCP buffer. A message might get broken into multiple buffer if the size isn't big enough or your network can't support it. For optimal performance choose a size with the power of two. (default: 4096)
```

### Batch scripts

To use mpd batch scripts create a folder with the name `mpdscripts` in your home directory.

Inside of it you can store your scripts. They must have the file extension `ncs`

#### Example script

```basic
ping
password hunter1
command_list_begin
commands
notcommands
urlhandlers
decoders
outputs
status
stats
command_list_end
close
```

