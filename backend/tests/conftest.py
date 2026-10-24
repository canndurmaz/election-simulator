"""Test defaults.

Persistence is built when `app.server` is imported, so ELECTION_DB has to be switched off
*before* that import or the suite would create (and keep writing to) backend/data/election.db.
Tests that exercise persistence build their own Store against a tmp_path instead.
"""
import os

os.environ.setdefault("ELECTION_DB", "off")
