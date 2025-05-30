
<p align="center">
  <img src="https://github.com/user-attachments/assets/d9b9d313-0919-45b8-be41-d42ac89bcd08" alt="DriveCord banner" width="800">
</p>

<h1 align="center">DriveCord</h1>

DriveCord turns any Discord text channel into a personal storage backend.  
It automatically splits large files into 5-25 MB chunks (Discord’s upload limit), distributes those chunks across any number of bot accounts, and later reassembles them on demand—everything handled transparently behind a text-based user interface.

---

## How DriveCord Works

1. **Chunking**  
   - On upload, each file is split into equal-sized parts (`chunk_size_mb`, adjustable 5–25 MB).  
   - Chunks are named `FILEID:<id> CHUNK:<n>` and sent as message attachments.

2. **Parallel Transfer**  
   - Provide multiple bot tokens; DriveCord assigns chunks round-robin, saturating your bandwidth and Discord’s rate limits.

3. **Persistent File Tree**  
   - The program keeps a local directory structure (`drivecord_config.json -> directories`).  
   - Every session starts exactly where you left off; interrupted uploads are purged automatically.

4. **TUI Control**  
   - A `curses` interface shows an expandable tree, active transfers, and settings.  
   - Dynamic progress bars refresh with **R**.

5. **Reconstruction**  
   - To download, DriveCord scans recent channel messages, pulls the required chunks (from any bot), and re-creates the original file byte-perfectly in `Drivecord Downloads/`.



## Requirements

* Python 3.8 or newer  
* `requests` (networking)  
* `windows-curses` on Windows (installed automatically)

---

## Setup

```bash
# Enter the project folder
cd DriveCord

# (Optional) virtual environment
python -m venv .venv
.\.venv\Scripts\activate            # Windows
# source .venv/bin/activate         # macOS / Linux

# Install dependencies
pip install -r requirements.txt
````

---

## Running

```bash
python DriveCord.py
```

### Default Controls

| Key         | Action                        |
| ----------- | ----------------------------- |
| ↑ / ↓       | Navigate menus and lists      |
| ← / →       | Collapse / expand directories |
| PgUp / PgDn | Scroll long lists             |
| Enter       | Select / download file        |
| R           | Refresh progress display      |
| D / X       | Delete file / directory       |
| M           | Move selected file            |
| Esc         | Back / main menu              |

On first launch open **Settings** and enter:

* **Server ID** – Discord server (guild) ID
* **Channel ID** – Text channel where files will be stored
* **Bot Tokens** – One or more bot tokens (pattern `xxxxx.xxxxx.xxxxxxxxxxxxxxxxxxxxxxxxxxx`) with permission to post in the channel

---

## Configuration File

```jsonc
{
  "server_id": "123456789012345678",
  "channel_id": "987654321098765432",
  "bot_tokens": ["AAA..."],
  "chunk_size_mb": 15,          // 5 ≤ size ≤ 25
  "directories": { ... }        // local file tree, auto-managed
}
```

The file lives next to the executable and updates automatically.

---

## Building a Stand-Alone EXE (Windows)

```bash
pip install pyinstaller
pyinstaller --onefile DriveCord.py   # omit --noconsole to keep a terminal window
```

The finished `DriveCord.exe` appears in `dist/`.
Run it from a terminal or create a small batch file:

```bat
@echo off
start cmd /k DriveCord.exe
```



