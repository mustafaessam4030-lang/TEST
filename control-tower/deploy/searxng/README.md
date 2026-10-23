# SearXNG for ATLAS web research

ATLAS searches the web only through a self-hosted SearXNG (open source, no
API key, no paid service). Without it, ATLAS says "I did not search the web"
and why; it never pretends.

`settings.yml` here: local only (127.0.0.1:8888), JSON output on, no rate
limiter. The secret key comes from the `SEARXNG_SECRET` environment variable.

## Tested: Linux (Python 3.11+)

This is the setup the demo acceptance tests ran against.

```sh
git clone --depth 1 https://github.com/searxng/searxng.git searxng
python3 -m venv searxng-venv
searxng-venv/bin/pip install -r searxng/requirements.txt
cd searxng
SEARXNG_SETTINGS_PATH=/path/to/control-tower/deploy/searxng/settings.yml \
SEARXNG_SECRET=$(python3 -c "import secrets; print(secrets.token_hex(24))") \
  ../searxng-venv/bin/python -m searx.webapp
```

Check it: `http://127.0.0.1:8888/search?q=air+waybill&format=json` returns results.

## Windows: SETUP_WEB_SEARCH.bat (the simple route)

Double-click `SETUP_WEB_SEARCH.bat` in the tower folder once. It runs
`python -m intelligence.searxng_local install`: downloads this SearXNG version
from GitHub into `searxng\`, gives it its own Python environment there,
installs its packages, starts it and checks a real search. After that the
tower (`update_eta.py`, the supervisor) starts it by itself when it is not
running. For Windows it supplies a stand-in for the Linux-only `pwd` module
(used only by a cache this setup leaves off), adds the certificates Windows
trusts (for networks that inspect HTTPS) and passes on the Internet Settings
proxy. Tested here on Linux with `pwd` removed to imitate Windows; the first
real Windows run is on your PC. Remove it by deleting the `searxng` folder.

## Windows: WSL 2 or Docker (alternatives, NOT tested)

SearXNG does not support Windows natively. There are two routes, and neither
has been tested on the company machine:

- **WSL 2** (Ubuntu): run the Linux steps above inside WSL. WSL 2 forwards
  `127.0.0.1:8888` to Windows. It needs virtualisation enabled, which a
  virtual server may not have.
- **Docker Desktop**: needs WSL 2 or Hyper-V, and a licence for company use
  above Docker's small-business limit.

  ```bat
  docker run -d --name atlas-searxng -p 127.0.0.1:8888:8080 ^
    -e SEARXNG_SECRET=<a long random string> ^
    -e SEARXNG_BIND_ADDRESS=0.0.0.0 -e SEARXNG_PORT=8080 ^
    -v "%CD%\deploy\searxng\settings.yml:/etc/searxng/settings.yml:ro" ^
    searxng/searxng
  ```

On either route, the search engines SearXNG queries (DuckDuckGo, Brave and
others) must be reachable from that network. A company proxy or firewall can
block them. `python atlas_demo.py --check` makes a real test query and says
whether results came back.
