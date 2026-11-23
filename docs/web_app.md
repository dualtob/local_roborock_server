# Web App

The server can serve the LocalRock Web App, a folder of static files, from its
own HTTPS address. Page, API and MQTT then share one origin, so the browser
needs no CORS and trusts a single certificate. This lives only on the
`dualtob/local_roborock_server` fork.

Outside the Docker image it's off by default. To turn it on, point
`web_app.folder` at the unpacked Web App and restart the server:

```toml
[web_app]
folder = "/opt/web_app"
```

A relative path is resolved against the folder that holds `config.toml`.

## In the Docker image

The image built from this fork's `Dockerfile` already contains a Web App
release, unpacked in `/opt/web_app`, and turns on both the Web App and
[MQTT over WebSockets](mqtt_websocket.md). With `docker compose up`, the Web App
is at `https://<stack_fqdn>:<https_port>/` and needs no extra ports.

The image does this through two environment variables. They only fill in keys
that `config.toml` leaves out, so a key you set there always wins:

| Variable | Image value | Used when `config.toml` has no |
| --- | --- | --- |
| `ROBOROCK_SERVER_WEB_APP_FOLDER` | `/opt/web_app` | `[web_app] folder` |
| `ROBOROCK_SERVER_MQTT_WEBSOCKET_ENABLED` | `true` | `[network] mqtt_websocket_enabled` |

To run the image without the Web App, set `folder = ""` under `[web_app]` in
`config.toml`; for no `/mqtt`, set `mqtt_websocket_enabled = false`.

### Choosing the release

The release is the `WEB_APP_VERSION` build argument: a `web-v*` tag of
[dualtob/LocalRock](https://github.com/dualtob/LocalRock/releases). `compose.yaml`
pins it:

```yaml
    build:
      args:
        WEB_APP_VERSION: web-v0.1.0
```

The build downloads
`https://github.com/dualtob/LocalRock/releases/download/<WEB_APP_VERSION>/localrock-web.zip`
and unpacks it into `/opt/web_app`. Rebuilding with the same tag gives the same
Web App. To update it, change the tag and run `docker compose up -d --build`.
The image records the tag in the `io.github.dualtob.localrock.web-app.version`
label and in `/opt/web_app.version`.

To make the build fail if the release's zip is ever replaced, also pass its
SHA-256 as the `WEB_APP_SHA256` build argument.

### Building from a local zip

When GitHub is unreachable, or to test a Web App build before releasing it,
build from a local `localrock-web.zip` (the `:composeApp:wasmJsBrowserDistribution`
output zipped with `index.html` at its root, as the release is). Put it in a
folder and pass that folder as the `web_app_zip` build context:

```bash
WEB_APP_ZIP_DIR=/path/to/folder \
  docker compose -f compose.yaml -f compose.web-app-zip.yaml build
```

or, without Compose:

```bash
docker buildx build --build-context web_app_zip=/path/to/folder \
  --build-arg WEB_APP_VERSION=web-v0.1.0 -t local_roborock_server .
```

Nothing is downloaded then. Keep `WEB_APP_VERSION` matching the zip, since it's
only recorded, not checked.

## What gets served

- `/` serves the Web App's `index.html` only for requests whose `Accept`
  header includes `text/html`, as a browser's page load does. `/` is also the
  robots' region bootstrap, and robots and the Roborock App don't send
  `text/html`, so they keep getting the region answer, exactly as without the
  Web App.
- Any other `GET` or `HEAD` path is served from the folder only if all of
  these hold:
  - it names a file in the folder;
  - the file is a static asset: `.html`, `.js`, `.mjs`, `.wasm`, `.css`,
    `.map`, `.txt`, `.png`, `.svg`, `.ico`, `.webp`, `.ttf`, `.otf`, `.woff`,
    `.woff2` or `.webmanifest`, or a `.cvr`, `.xml` or `.json` file under
    `composeResources/`;
  - no API route answers the path: not the admin dashboard (`/admin` and
    everything under it), not `/mqtt`, not any other registered route, and not
    a Roborock API path (`/region`, `/api/...`, `/user/...` and the rest the
    server knows).

  The file gets a content type from its extension (`.wasm` is served as
  `application/wasm`, `.png` as `image/png`). A `.webmanifest` file is always
  served as `application/manifest+json`, whatever the host's MIME table says.

  This covers what a phone needs to add the Web App to its home screen: the
  manifest (for example `/manifest.webmanifest`) and the icons at the root
  (for example `/apple-touch-icon.png`). They follow the same rules, so a
  manifest or icon at an API or admin path is never served either.

Everything else goes to the existing routes, exactly as with the option unset.
All other methods reach the API and catch-all routes as before, and files
outside the folder are never served. So a stray file in the folder, such as
one named `region` or `api/v1/login.js`, can never answer for the Robots' or
the App's routes.
