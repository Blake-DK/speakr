"""Read-scoped API tokens may list projects, read one and list its recordings.

Every other studio route stays closed to scoped tokens: a route without
require_scope refuses them (src/app.py, enforce_token_scopes).
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.app import app

READ_ROUTES = ('studio.list_projects', 'studio.get_project', 'studio.project_recordings')


def test_read_routes_accept_a_read_scoped_token():
    for endpoint in READ_ROUTES:
        assert app.view_functions[endpoint]._required_scopes == frozenset({'read'}), endpoint


def test_every_other_studio_api_route_refuses_scoped_tokens():
    for rule in app.url_map.iter_rules():
        if rule.endpoint.startswith('studio.') and rule.rule.startswith('/api/studio/') \
                and rule.endpoint not in READ_ROUTES and rule.endpoint != 'studio.health':
            view = app.view_functions[rule.endpoint]
            assert getattr(view, '_required_scopes', None) is None, rule.rule
