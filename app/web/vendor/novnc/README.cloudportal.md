# Vendored noVNC client

Cloudportal-backed vendors the browser-side noVNC client from upstream noVNC v1.5.0.

Upstream: https://github.com/novnc/noVNC
Tag: v1.5.0
License: MPL-2.0. Upstream notices are retained in LICENSE.txt and the full license text is retained in docs/LICENSE.MPL-2.0.

The vendored Pako sources under vendor/pako are distributed under the MIT license
retained in vendor/pako/LICENSE.

Only the browser client is vendored. Proxmox VNC credentials and the VNC transport
remain backend-proxied through the short-lived Cloudportal console session.
