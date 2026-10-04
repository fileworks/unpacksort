# Install UnpackSort

Public source works on Windows, macOS and Linux with Python 3.12+.
No GitHub login, private notes or private downloads are needed.

## Prerequisites

| Platform | Setup |
|---|---|
| Windows | Install [Python](https://www.python.org/downloads/windows/) 3.12+ and [Git](https://git-scm.com/downloads). In PowerShell run `py -m pip install --user pipx`, then `py -m pipx ensurepath`; reopen the terminal. |
| macOS | With [Homebrew](https://brew.sh/) installed: `brew install python@3.12 pipx git`, then `pipx ensurepath`; reopen the terminal. |
| Linux | Install Python 3.12+, Git and pipx using your supported distro packages, then `pipx ensurepath`. If the distro Python is older, install a supported Python first. |

Confirm `pipx --version`, `git --version` and a Python 3.12+ interpreter.
[pipx setup](https://github.com/pypa/pipx#install-pipx) has platform alternatives.
If pipx selects an older interpreter, add `--python /path/to/python3.12` to install.

## Install and check

Choose a stable release from [public tags](https://github.com/fileworks/unpacksort/tags).
Replace every `RELEASE_TAG` below with that chosen tag, including its `v` prefix.
Run in PowerShell, Terminal or Linux:

```console
pipx install git+https://github.com/fileworks/unpacksort.git@RELEASE_TAG
unpacksort --version
unpacksort --help
```

Then follow the [quick start](../README.md#quick-start) on a small disposable
sample. `--version` must match the chosen tag without its `v` prefix. Source
installation builds a wheel locally; dependencies use their public sources.

For a later release, replace the tag and use `pipx install --force` with the
versioned Git URL. To remove it: `pipx uninstall unpacksort`. Do not use
`pipx install unpacksort`; our PyPI publication is retired.

If you already use [uv](https://docs.astral.sh/uv/guides/tools/), the alternative
is `uv tool install --python 3.12 git+https://github.com/fileworks/unpacksort.git@RELEASE_TAG`,
then `uv tool update-shell` and reopen the terminal. Pick one tool manager.

## Optional private downloads

Authorized accounts can use [private wheels](https://github.com/fileworks/private-releases#installation)
or the [private macOS tap](https://github.com/fileworks/homebrew-tap#install).
Each repository grants access independently. A 404 usually means missing access
or the wrong account; use the public source route when you do not have access.
