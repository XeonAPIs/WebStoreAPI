import ipaddress
import socket
from urllib.parse import urlparse


def validate_upstream_url(url):
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("Upstream URL must use HTTP or HTTPS and include a hostname.")
    if parsed.username or parsed.password:
        raise ValueError("Credentials are not allowed in upstream URLs.")
    if parsed.port and not 1 <= parsed.port <= 65535:
        raise ValueError("Invalid upstream port.")
    hostname = parsed.hostname.rstrip(".").lower()
    if hostname in ("localhost",) or hostname.endswith((".localhost", ".local", ".internal")):
        raise ValueError("Private and internal hostnames are not allowed.")
    try:
        addresses = {ipaddress.ip_address(hostname)}
    except ValueError:
        try:
            addresses = {
                ipaddress.ip_address(result[4][0])
                for result in socket.getaddrinfo(hostname, parsed.port or (443 if parsed.scheme == "https" else 80))
            }
        except (socket.gaierror, ValueError):
            raise ValueError("Upstream hostname could not be resolved.") from None
    if not addresses or any(not address.is_global for address in addresses):
        raise ValueError("Private, reserved, and internal IP addresses are not allowed.")
    return parsed
