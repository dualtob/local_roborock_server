# MQTT over WebSockets

Browsers can't open raw TCP sockets, so a browser client such as the LocalRock Web App can't use MQTT over TLS on `mqtt_tls_port`. The server can also accept MQTT over WebSockets on its HTTPS port instead. It's off by default.

## Turning It On

```toml
[network]
mqtt_websocket_enabled = true
```

Restart the stack after changing it.

## Connecting

- URL: `wss://<stack_fqdn>:<https_port>/mqtt`, which is the same host, port and certificate as the HTTPS API. With `listener_mode = "external_tls"`, your reverse proxy terminates TLS and must forward WebSocket upgrades on `/mqtt`.
- WebSocket subprotocol: `mqtt`. Connections that don't offer it are refused.
- Frames: binary only. Each frame carries MQTT bytes; packets may span frames.
- Login: the same MQTT username and password as MQTT over TLS. For an app login, that's the one derived from the login's `rriot` data.

## How Logins and Topics Are Checked

Each WebSocket is handed to the same MQTT proxy that serves `mqtt_tls_port`, as if it were one more client connection. The login check, the effect of `admin.protocol_auth_enabled` and `admin.new_connections_enabled`, onboarding rules and MQTT tracing are therefore identical for both. A refused login gets a "not authorized" CONNACK, and the WebSocket is then closed. The broker is only reached after the login is accepted.

MQTT over TLS on `mqtt_tls_port` is unchanged whether this switch is on or off. With the switch off, `/mqtt` has no WebSocket endpoint, and plain HTTP requests to it are handled as before.

## Related Docs

- [Reverse Proxy](reverse_proxy.md)
- [Custom MQTT](custom_mqtt.md)
