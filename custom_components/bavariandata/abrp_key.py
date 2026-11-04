"""The ABRP telemetry API key this release ships with.

Empty in git on purpose: the release workflow writes the project's key in from
a GitHub secret before it builds the zip, so the key stays out of the history.
A key on a user's machine can't be hidden from that user, and isn't meant to
be. A copy from git has no key, and the ABRP screen asks for one of its own.
"""

PROJECT_API_KEY = ""
