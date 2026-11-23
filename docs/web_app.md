# Web App

The server can serve the LocalRock Web App, a folder of static files, from its
own HTTPS address. Page, API and MQTT then share one origin, so the browser
needs no CORS and trusts a single certificate. This lives only on the
`dualtob/local_roborock_server` fork.

It's off by default. To turn it on, point `web_app.folder` at the unpacked Web
App and restart the server:

```toml
[web_app]
folder = "/opt/web_app"
```

A relative path is resolved against the folder that holds `config.toml`.

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
