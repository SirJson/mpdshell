# mpdshell
A shell-like application for mpd. Lets you control your Music Player Daemon instance [with the raw protocol commands](https://www.musicpd.org/doc/html/protocol.html). 

Includes protocol autocomplete, a basic lexer, a history, and an basic batch script interpreter.

![image-20200810085052793](README.assets/image-20200810085052793.png)

## Dependencies

-   Python 3.8 (might work with 3.7 or 3.6 as well)
-   prompt_toolkit

## Usage

```
usage: mpdshell.py [-h] [-p PORT] host

positional arguments:
  host                  The host of your MPD instance

optional arguments:
  -h, --help            show this help message and exit
  -p PORT, --port PORT  The port on which MPD is running
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

