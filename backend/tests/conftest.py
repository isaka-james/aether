"""Shared test setup.

Pin a deterministic configuration *before* any app module is imported. This matters
because some modules read settings at import time — notably ``auth``, which hashes the
configured password with bcrypt when it is first imported. Setting these here guarantees
the suite sees known credentials and a fixed JWT secret, and never tries to reach a real
database, cache, or host agent.
"""
import os

# Known single-user credentials + secret for the auth tests.
os.environ["AETHER_USERNAME"] = "tester"
os.environ["AETHER_PASSWORD"] = "s3cret-pw"
os.environ["AETHER_JWT_SECRET"] = "test-secret-please-ignore-0123456789abcdef"

# Persistence and the host agent stay off, so importing/exercising those modules is a
# pure no-op rather than an attempt to open a socket.
os.environ["AETHER_DATABASE_URL"] = ""
os.environ["AETHER_REDIS_URL"] = ""
os.environ["AETHER_HOST_AGENT_URL"] = "http://127.0.0.1:1"
