import truststore


def enable_system_trust_store() -> None:
    """Verify outbound TLS against the OS trust store instead of certifi's
    bundled root list, so a corporate TLS-inspecting proxy's root CA
    (installed into the container image via update-ca-certificates) is
    honored by requests/urllib3. Must run before any HTTPS call is made."""
    truststore.inject_into_ssl()
