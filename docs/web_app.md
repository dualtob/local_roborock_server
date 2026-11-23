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

- A browser opening `https://<stack_fqdn>:<https_port>/` gets the folder's
  `index.html`.
- Any other `GET` or `HEAD` path that names a file in the folder gets that
  file, with a content type from its extension (`.wasm` is served as
  `application/wasm`).

Everything else goes to the existing routes, exactly as with the option unset:

- `/` is also the robots' region bootstrap, so the index is only served when
  the request's `Accept` header names `text/html`, as a browser's page load
  does. Robots and the Roborock App keep getting the region answer.
- `/admin` and everything under it always go to the admin dashboard.
- Paths that don't name a file in the folder, and all other methods, reach the
  API and catch-all routes as before.
- Files outside the folder are never served.

Put only the Web App's build output in the folder: a file whose path matches an
API route (for example a file named `region`) would answer `GET` requests for
that route.
