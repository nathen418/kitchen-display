# Kitchen Display

A small self-hosted web app for a kitchen wall panel (built for a Crestron touch panel, but any
device with a browser works). Paste a recipe link into Home Assistant, press **Send to display**,
and the panel shows it a few seconds later.

- **Recipe view**: the server fetches the page, reads its schema.org recipe data (JSON-LD,
  falling back to microdata) and shows a large, ad-free card with ingredients on the left and
  steps on the right. Tap an ingredient to tick it off; tap a step to mark where you are.
  Follows the device's light/dark setting and stacks into one column on portrait screens.
- **Full website**: the panel opens the original site. The app is no longer in control after
  that, so the next link appears only once the panel reloads its boot URL (e.g. after a reboot).

If recipe view can't read a recipe (no recipe data on the page, or the site blocks non-browser
requests with a bot challenge), it falls back to the full website.

## How it works

```
Home Assistant dashboard --(button press)--> automation --> rest_command (POST + secret)
                                                                   |
Panel (boot URL) <-- polls /api/current every 4 s --------- Kitchen Display container
```

Home Assistant pushes to the app. The app never connects to Home Assistant and holds no Home
Assistant credentials. The latest link is saved in `data/state.json`, so the panel shows it
again after a reboot or container restart.

## API

| Method | Path           | Auth                             | Purpose                              |
|--------|----------------|----------------------------------|--------------------------------------|
| GET    | `/`            | none                             | Display page (use as the boot URL)   |
| GET    | `/api/current` | none                             | Current link, mode and parsed recipe |
| POST   | `/api/set`     | `Authorization: Bearer <secret>` | Set the link (body below)            |
| GET    | `/healthz`     | none                             | Health check                         |

`POST /api/set` body: `{"url": "https://...", "mode": "Recipe view"}`. `mode` is
`Recipe view` / `recipe` or `Full website` / `site`; it defaults to recipe view.

```sh
curl -X POST https://kitchen.example.com/api/set \
  -H "Authorization: Bearer $KITCHEN_SECRET" -H "Content-Type: application/json" \
  -d '{"url": "https://www.example.com/some-recipe", "mode": "Recipe view"}'
```

## Install

Requires Docker with the Compose plugin.

```sh
git clone <this repo> kitchen-display && cd kitchen-display
cp .env.example .env
# put a long random value in .env, e.g.: openssl rand -base64 36
chmod 600 .env
mkdir -p data && sudo chown 10080 data     # the container runs as uid 10080
docker compose up -d --build
```

The app listens on port 8080. Put it behind a reverse proxy with TLS (Nginx Proxy Manager,
Caddy, Traefik, ...) and use that address everywhere below; `kitchen.example.com` is a
placeholder.

### Security notes

- The display page and `/api/current` are unauthenticated by design so a panel can show them
  without logging in. Keep the app on your local network; don't expose it to the internet.
- `/api/set` requires the secret. Anyone holding it can make the server fetch any URL
  (that is how recipe view works), so treat it like a password.

## Home Assistant

Files in [`homeassistant/`](homeassistant/):

1. Create three helpers (Settings > Devices & services > Helpers):
   - Text: **Kitchen Display URL** (`input_text.kitchen_display_url`, max length 255)
   - Dropdown: **Kitchen Display Mode** (`input_select.kitchen_display_mode`) with options
     `Recipe view` and `Full website`
   - Button: **Kitchen Display Send** (`input_button.kitchen_display_send`)
2. Add [`rest_command.yaml`](homeassistant/rest_command.yaml) to `configuration.yaml` with your
   address, and put `kitchen_display_auth: "Bearer <KITCHEN_SECRET>"` in `secrets.yaml`.
   Restart Home Assistant (a new `rest_command` is not picked up by a YAML reload).
3. Add the automation from [`automation.yaml`](homeassistant/automation.yaml). It sends the link
   when the button is pressed and raises a notification if the app returns an error.
4. Add the card from [`dashboard-card.yaml`](homeassistant/dashboard-card.yaml) to a dashboard.

## Panel setup

Set the panel's boot/home URL to `https://kitchen.example.com/`.

## Development

The app is plain Python 3.13 with one dependency (`aiohttp`). To run it locally:

```sh
pip install aiohttp
KITCHEN_SECRET=$(python -c "import secrets; print(secrets.token_urlsafe(36))") \
DATA_DIR=./data python app/app.py
```

`app/recipe.py` (the recipe extractor) has no dependencies outside the standard library.
