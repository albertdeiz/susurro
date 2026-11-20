"""Susurro: select text, press a shortcut, listen."""

# Validate TLS against the macOS keychain (includes corporate CAs / inspecting proxies).
# Must run before edge_tts is imported, since it builds its SSL context at import time.
import truststore

truststore.inject_into_ssl()

PORT = 47823
