# Samba Configuration Manager

A small PySide6 GUI for editing `/etc/samba/smb.conf` safely.

The interface is currently in German.

## Features

- **Guided share wizard** for the common Samba settings:
  share name, comment, path, read/write access, valid users,
  `force user` / `force group`, guest access, network visibility and
  file/directory permission masks.
- **Advanced parameter editor** for any other option in `global` or a share.
- **Safe saves**: every configuration is validated with `testparm -s`
  before being written, a timestamped backup is created, file permissions
  are preserved, and the new file is moved into place atomically.
- **Root handling**: if the app is not started as root, it asks for the
  root password and relaunches itself via `sudo`. If it is started with
  `sudo python3 smb-ui.py` directly, it restores the desktop session
  environment that `sudo` strips, so the window still appears.
- **Native look and feel**: uses the system Qt style and palette; no custom
  stylesheet is forced on the widgets.
- **Portability workaround**: on systems where PySide6 expects the legacy
  `libgthread-2.0.so.0` soname, the app creates a local compatibility
  symlink automatically at startup.

## Requirements

- Python 3.9 or newer
  - Debian/Ubuntu: also install `python3-venv`
  - openSUSE: `sudo zypper install python3-venv` if `python3 -m venv` is missing
- PySide6 6.6 or newer
- `testparm` from the Samba suite
  - Debian/Ubuntu: `sudo apt install samba-common-bin`
  - Fedora/RHEL: `sudo dnf install samba-common-tools`
  - openSUSE: `sudo zypper install samba`
- A graphical session (X11 or Wayland)

## Installation

```bash
git clone <your-repository-url>
cd smb-ui

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### System-wide installation

For a system-wide install (the script will ask for root privileges):

```bash
./install.sh
```

This creates `/opt/smb-ui` with a dedicated virtual environment, installs
its dependencies, and adds a launcher at `/usr/local/bin/smb-ui`. It also
warns you if `testparm` is missing. The install is idempotent, so it can be
run again after updating the source files.

To remove it later:

```bash
./uninstall.sh
```

Neither script touches your Samba configuration files.

## Usage

Run the application with Python:

```bash
python3 smb-ui.py
```

You will be prompted for the root password and the app restarts with
administrator privileges. Alternatively, start it directly as root:

```bash
sudo python3 smb-ui.py
```

After a system-wide install, just run:

```bash
smb-ui
```

The configuration file can be changed by editing `CONFIG_FILE` near the top
of `smb-ui.py` (default: `/etc/samba/smb.conf`).

## Notes on privileges

- The root password is read with a masked Qt input dialog and passed to
  `sudo` through standard input only. It is never written to the command
  line, an environment variable, disk, or logs.
- Use only on machines where you are authorized to administer Samba.

## License

[MIT](LICENSE) — see the `LICENSE` file for details.
