# Installing the tower on the Remote Desktop server

One Windows machine runs everything: the automation (Edge, carriers, Hub), the
PO engine, ATLAS and the dashboard. Colleagues use it from their own PCs at
`http://<server-name>:8787` with the access key. Nobody else needs Remote
Desktop.

## Install (once, about 10 minutes)

1. Install **Python 3.11+** (python.org, tick *Add to PATH*) and make sure
   **Microsoft Edge** is installed.
2. Unzip the release, e.g. to `C:\MantracTower`.
3. Put `C:\Automation\credentials.txt` on the machine, as today.
4. Sign in as the Windows account the automation will use, then double-click
   **`INSTALL_SERVER.bat`** and accept the administrator prompt. It:
   - checks Python and Edge;
   - installs the packages;
   - opens port 8787 to the company network only;
   - stops the machine sleeping;
   - registers the scheduled task **Mantrac Tower**, which starts the tower
     when this account signs in and restarts it within a minute if it stops;
   - finishes with the health check.
5. Set **automatic sign-in** for that account with Microsoft's **Autologon**
   tool (learn.microsoft.com/sysinternals/downloads/autologon). It keeps the
   password encrypted; the installer never asks for it.
6. Sign out and in again (or restart): the tower starts by itself. The
   window prints the link to send colleagues.

## Leaving Remote Desktop

Do **not** close or minimise the Remote Desktop window while a run may be
going. Windows can stop drawing the session, and Edge then freezes.
Double-click **`disconnect_keep_running.bat`** instead. It hands the session
to the machine's own console: you are disconnected, and the automation keeps
working. Connect again with Remote Desktop as usual.

## Is it healthy?

`check_server.bat` checks the following and changes nothing:
- Python, the packages and Edge, and that a browser window opens;
- the dashboard, on the machine and on the network address;
- the Hub and the carrier sites;
- disk space, the scheduled task, the sleep setting and the firewall rule;
- the credentials file, and whether this is a Remote Desktop or console
  session.

Each line is PASS, WARN, FAIL or SKIP.

## Everyday

- **Starting runs, the PO page, ATLAS and Human Action** are all in the
  dashboard, from any PC on the company network.
- **A human check:** press **Open Session**, then complete it in your own
  browser.
- **Updating:** stop the task (Task Scheduler → *Mantrac Tower* → End),
  replace the folder (keep `C:\Automation`), and sign in again.
- **Backups:** `C:\Automation`, `ml\data`, `controlplane\data` and the PO
  data folder.
- **New access key:** delete `dashboard\.runtime\access_key` and restart.
  Everyone then needs the new link.
