# MQTT over WebSockets

Browsers can't open raw TCP sockets, so a browser client such as the LocalRock Web App can't use MQTT over TLS on `mqtt_tls_port`. The server can also accept MQTT over WebSockets on its HTTPS port instead. It's off by default, except in the Docker image (see below).

## Turning It On

```toml
[network]
mqtt_websocket_enabled = true
```

Restart the stack after changing it.

The Docker image built from this fork turns it on whenever `config.toml` leaves
`mqtt_websocket_enabled` out (through `ROBOROCK_SERVER_MQTT_WEBSOCKET_ENABLED=true`;
see [Web App](web_app.md#in-the-docker-image)). Set `mqtt_websocket_enabled = false`
to turn it off there.

## Connecting

- URL: `wss://<stack_fqdn>:<https_port>/mqtt`, which is the same host, port and certificate as the HTTPS API. With `listener_mode = "external_tls"`, your reverse proxy terminates TLS and must forward WebSocket upgrades on `/mqtt`.
- WebSocket subprotocol: `mqtt`. Connections that don't offer it are refused.
- Frames: binary only. Each frame carries MQTT bytes; packets may span frames.
- Login: the same MQTT username and password as MQTT over TLS. For an app login, that's the one derived from the login's `rriot` data.

## Browser Clients on Another Origin

A browser client served from its own hostname (say `https://web.example.com`) calls the server cross-origin. List the origins allowed to do that:

```toml
[network]
cors_allowed_origins = ["https://web.example.com"]
```

Each entry is an origin: scheme, host and, if it isn't the default, port, with no path (`https://web.example.com:8443`). Case and a spelled-out default port (`:443`, `:80`) make no difference. Restart the stack after changing it.

With the list set:

- The HTTPS API answers CORS preflight (`OPTIONS`) requests from listed origins, allowing any method and request header, `Authorization` and `Content-Type` included, and adds `Access-Control-Allow-Origin` to their responses. Requests from any other origin get no CORS headers, so browsers block them from reading the response.
- `/mqtt` only accepts WebSockets whose `Origin` header is on the list. Browsers don't apply CORS to WebSockets, so this check is what keeps another website you visit from reaching the broker through your browser. A WebSocket from an unlisted origin, or without an `Origin` header (a non-browser client), is refused with HTTP 403.

The list is empty by default. Then nothing gets CORS headers, and `/mqtt` accepts any origin, as before. Non-browser clients that need `/mqtt` while the list is set can send one of the listed origins as their `Origin` header.

## How Logins and Topics Are Checked

Each WebSocket is handed to the same MQTT proxy that serves `mqtt_tls_port`, as if it were one more client connection. The login check, the effect of `admin.protocol_auth_enabled` and `admin.new_connections_enabled`, onboarding rules and MQTT tracing are therefore identical for both. A refused login gets a "not authorized" CONNACK, and the WebSocket is then closed. The broker is only reached after the login is accepted.

MQTT over TLS on `mqtt_tls_port` is unchanged whether this switch is on or off. With the switch off, `/mqtt` has no WebSocket endpoint, and plain HTTP requests to it are handled as before.

## Related Docs

- [Reverse Proxy](reverse_proxy.md)
- [Custom MQTT](custom_mqtt.md)
