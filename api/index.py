"""Vercel entrypoint: export only the WSGI app, never the local HTTP handler."""

from vercel_app import app
