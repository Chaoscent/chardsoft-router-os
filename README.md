# Chardsoft Router Apps

The Apps Addon for [Chardsoft Router OS](https://github.com/chaoscent/chardsoft-router-os): run apps like Nextcloud, Jellyfin or Vaultwarden on the router in Docker containers, installed from the dashboard's **Apps** page.

Chardsoft Router OS never depends on this addon. Without it, the Apps page offers to install it.

## What it installs

- **Docker Engine and Docker Compose** from Docker's own apt repository (Debian, Raspberry Pi OS, Ubuntu).
- **Docker settings for a router** (`/etc/docker/daemon.json`):
  - `ip-forward-no-drop` (Docker 28+): Docker doesn't set the FORWARD policy to DROP, so LAN devices keep their internet even with the router's firewall off.
  - An address pool of its own (172.31.0.0/16), so Docker's networks don't clash with common LANs.
  - An existing `daemon.json` is left alone.
- **The App Manager** (`/usr/local/bin/chardsoft-apps` → `/opt/chardsoft-router-apps`, owned by root).
- **A sudoers rule** that lets Chardsoft Router OS run the App Manager, and nothing else.

## Install

From the dashboard: **Apps → Install Apps Addon**. By hand on the router:

```bash
git clone --branch dev/apps https://github.com/chaoscent/chardsoft-router-os chardsoft-router-apps
sudo chardsoft-router-apps/install.sh
```

The addon lives in the Chardsoft Router OS repository, on the branches `dev/apps` and `release/apps` (from the v1 release).

Running it again updates the addon; installed apps keep running. Remove it with `sudo /opt/chardsoft-router-apps/install.sh --remove` (add `--delete-data` to delete the apps' data too). Docker stays installed; `--purge` removes it as well (and the apps' data), if this installer installed it. Chardsoft Router OS's `uninstall.sh` runs `--remove --purge`.

## How apps fit into the router

Every app is reached by its own name: `<app>.chardsoft-router.local`, e.g. `http://nextcloud.chardsoft-router.local/` (the router itself is `chardsoft-router.local`). Chardsoft Router OS sets this up when an app is installed:

- **DNS:** dnsmasq answers the name with the router's address.
- **Caddy:** routes the name to the app over HTTP and HTTPS.
- **HTTPS:** a certificate from the router's local certificate authority, like the dashboard.

Catalog apps publish their port on **127.0.0.1 only**. Docker's published ports bypass the firewall (ufw), so nothing is exposed directly; Caddy is the only way in. Your own apps may publish more, after a warning (below).

## App Manager

```
chardsoft-apps version                      versions, Docker state (JSON)
chardsoft-apps catalog                      apps that can be installed (JSON)
chardsoft-apps status                       installed apps and their state (JSON)
chardsoft-apps install <app> --host <name>  download and start an app
chardsoft-apps start|stop|restart <app>
chardsoft-apps update <app>                 newer images, recreate
chardsoft-apps set-host <app> <name>        the name the app is reached at
chardsoft-apps remove <app> [--delete-data]
chardsoft-apps logs <app> [--lines N]
chardsoft-apps check-custom <file>          check your own app (JSON)
chardsoft-apps add <file> --host <name> [--accept-risks]
                                            install your own app
```

- `install` only takes apps from `catalog/`; your own apps come in through `add`.
- Each app is a Compose project named `chardsoft-<app>`.
- Its settings, with passwords generated per router, are in `/var/lib/chardsoft-router-apps/apps/<app>/app.env` (mode 600), and its data in `.../<app>/data/`.
- Removing an app keeps its data unless you pass `--delete-data`.
- Only one change runs at a time.

## Your own apps

The Apps page adds any image as an app: from a form (image, web port, folders to keep, variables), or from a pasted compose file. It gets a name like the catalog apps, e.g. `https://home-assistant.chardsoft-router.local/`.

- **Files** in `/var/lib/chardsoft-router-apps/apps/<app>/`: `custom.json` (name, web route, accepted risks), `compose.yaml` (your file, or the form's), `compose.json` (what runs).
- **Before every start** Docker Compose normalizes `compose.yaml` and the App Manager checks it, then publishes the web port on 127.0.0.1 (9200 and up) for Caddy. Relative folders (`./data`) land in the app's folder.
- **Refused:** building images (`build:`), and publishing ports the router uses (22, 53, 67, 68, 80, 443, 546, 547, 5000, 5353).
- **Risky, installed only after you accept them on the Apps page:** `privileged`, the host network, `pid`/`ipc`/`userns_mode`/`uts: host`, `cap_add`, devices, folders outside the app's folder (the Docker socket in particular), protections turned off in `security_opt`, and ports published beyond 127.0.0.1 (Docker opens those past the router's firewall). The accepted list stays visible on the app.
- **Removing** works like for catalog apps; `--delete-data` also removes its named volumes.

## Catalog

| App | Port (127.0.0.1) | Notes |
|---|---|---|
| Uptime Kuma | 9101 | |
| Vaultwarden | 9102 | Open it over HTTPS (the web vault needs it). Sign-ups are open until you turn them off. |
| Jellyfin | 9103 | Media goes into `/var/lib/chardsoft-router-apps/apps/jellyfin/data/media` (needs sudo for now). |
| Nextcloud | 9104 | With MariaDB. The trusted domain is set on first install; a later rename of the router needs `occ config:system:set trusted_domains`. |

Pi-hole and AdGuard are not in the catalog: they need port 53, which the router's own DNS (dnsmasq) uses.

### Adding an app

Add `catalog/<app>/app.json` and `catalog/<app>/compose.yaml`:

- **`app.json`:**
  - `id` (the folder name), `name`, `description`, `category`, `website`.
  - `port`: a free port, 9100 and up.
  - `download_mb`.
  - Optional `min_ram_mb`: the board memory the app needs to run well (1024, 2048, 4096...). On smaller boards the Apps page warns before installing.
  - Optional: `https` (true if it only works over HTTPS), `first_steps`, `secrets` (names of passwords to generate) and `dirs` (folders to create in its data folder).
- **`compose.yaml`:**
  - Publish the web port as `"127.0.0.1:${CHARDSOFT_PORT}:<port inside>"`.
  - Keep data under `${CHARDSOFT_DATA}`.
  - Variables available: `CHARDSOFT_HOST` (its name), `TZ` and the secrets.

Check it with `docker compose --env-file <an app.env> -f catalog/<app>/compose.yaml config`.

## Layout

```
install.sh          Installer (also updates and removes)
bin/chardsoft-apps  The App Manager (Python 3, standard library only)
catalog/<app>/      app.json + compose.yaml per app
tests/              python3 -m unittest discover -s tests
```

## License

Open source under the [Apache License 2.0](LICENSE): use, change and share it, also commercially, as long as the license and the [NOTICE](NOTICE) file (Copyright Chaoscent) stay with every copy. The names and logos are not licensed.

The apps themselves (Nextcloud, Jellyfin, ...) are separate software under their own licenses. Contributions come under the same license, see Chardsoft Router OS's [CONTRIBUTING.md](https://github.com/chaoscent/chardsoft-router-os/blob/dev/core/CONTRIBUTING.md).
