# Running: in a window, at logon, or 24/7

🇷🇺 [Русская версия](../ru/running.md) · [← README](../../README.md)

The bot works **only while the program runs**. There is no cloud service behind it: it runs on your machine and talks to Telegram by long polling, so no public IP, domain or open port is needed.

| Option | The pet answers... | Good for |
|---|---|---|
| [A window](#in-a-window) | while the window is open | trying it out |
| [Windows, at logon](#windows-start-hidden-at-logon) | while you're logged in and the PC is awake | a PC that's on most of the day |
| [Raspberry Pi / server](#raspberry-pi--linux-server-247) | always | the real thing; a Raspberry Pi 3/4/5 is plenty |

**Run only ONE copy per bot token.** A second copy on the same folder refuses to start. A copy in another folder or on another machine can't be detected, and the two would fight over messages.

## In a window

`run.bat` on Windows, or `sh run.sh`. Stop it with **Ctrl+C**: the bot finishes the answers in progress and closes the database cleanly. Closing the window, sleep or shutdown stops the bot.

## Windows: start hidden at logon

Start the bot once with `run.bat` first, so that `.venv` exists and the owner is claimed. Then double-click **`deploy\windows\autostart.bat`**, or:

```powershell
powershell -ExecutionPolicy Bypass -File deploy\windows\autostart.ps1           # enable
powershell -ExecutionPolicy Bypass -File deploy\windows\autostart.ps1 -Remove   # disable
```

- The bot starts hidden at every logon, without a window, and restarts up to 3 times after a crash.
- The log goes to `logs\bot.log`.
- Stop it manually in *Task Manager* (`pythonw.exe`) or with `-Remove`.

## Raspberry Pi / Linux server (24/7)

Any Raspberry Pi with Raspberry Pi OS Lite works. The bot needs ~60–80 MB of RAM.

```bash
sudo apt update && sudo apt install -y git python3-venv
git clone https://github.com/setsun-ai/family-pet-bot.git ~/family-pet-bot
cd ~/family-pet-bot
sh run.sh --setup                      # creates .venv, installs libraries, runs the setup wizard
bash deploy/linux/install-service.sh   # run it as a service from now on
```

`install-service.sh`:
- installs the libraries and **automatic security updates** (`unattended-upgrades`);
- checks `.env`;
- installs a systemd service that starts at boot and restarts after a crash;
- adds a **daily database backup** (`backups/`, the last 10 are kept).

On the first start, read the one-time owner link in the log:

```bash
journalctl -u family-pet-bot -n 30 --no-pager
```

Headless tip: open the link on your phone, or send `/claim CODE` to the bot in Telegram.

| What | Command |
|---|---|
| Live log | `journalctl -u family-pet-bot -f` |
| Restart (after editing `.env` or the persona) | `sudo systemctl restart family-pet-bot` |
| Stop / start | `sudo systemctl stop family-pet-bot` / `start` |
| Backup now | `.venv/bin/python -m petbot backup` |
| Update the bot | `git pull && bash deploy/linux/install-service.sh` |
| Remove the service | `bash deploy/linux/install-service.sh --remove` |

## Moving to another machine

1. **Stop the old copy.**
2. Copy these files to the new machine:
   - `.env`,
   - your persona (`personas/*.local.md`),
   - the database `data/bot.sqlite3`.

   Better: make a fresh `python -m petbot backup` and copy that file, renamed to `data/bot.sqlite3`.
3. Start the new copy. The owner, the family chat, the memory and the nicknames come with the database.

Without the database the bot works too, but you claim ownership and run `/setup_chat` again.

## Checks

| Command | What it does |
|---|---|
| `python -m petbot check-config` | validates `.env` offline, free |
| `python -m petbot check` | tests Telegram, the AI (one small request), RSS and sports, then exits (codes: 0 ok, 1 connection problem, 2 configuration) |
| `/status` in Telegram | what the running bot is doing, free |
| `/check` in Telegram | the same connection test as `check` |
