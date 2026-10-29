"""Deploy compose files (opt-in HTTPS).

The base docker-compose.prod.yml routes by PathPrefix only. Traefik derives the
domains it asks Let's Encrypt for from Host() rules, so with no Host() rule it
logs `No domain parsed in provider ACME` and never requests a certificate, even
with DOMAIN and ACME_EMAIL set. docker-compose.https.yml adds Host() routers on
the websecure entrypoint and an http -> https redirect, and is opt-in: the base
file must not change for anyone who does not pass `-f docker-compose.https.yml`.
"""
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
PROD_COMPOSE = REPO_ROOT / "docker-compose.prod.yml"
HTTPS_COMPOSE = REPO_ROOT / "docker-compose.https.yml"

APP_SERVICES = ("api", "worker", "email_worker", "maintenance_worker", "beat", "web")


def _services(path: Path) -> dict:
    return yaml.safe_load(path.read_text())["services"]


def _labels(path: Path, service: str) -> dict[str, str]:
    """List-form labels as {key: value} (compose merges list labels by key)."""
    out = {}
    for item in _services(path)[service]["labels"]:
        key, _, value = item.partition("=")
        out[key] = value
    return out


def test_https_file_routes_by_host():
    for service in ("web", "api"):
        labels = _labels(HTTPS_COMPOSE, service)
        router = f"traefik.http.routers.{service}-https"
        rule = labels[f"{router}.rule"]
        assert rule.startswith("Host(`") and "DOMAIN:?" in rule, rule
        assert labels[f"{router}.entrypoints"] == "websecure"
        assert labels[f"{router}.tls.certresolver"] == "letsencrypt"
        assert labels[f"{router}.service"] == service

    web_rule = _labels(HTTPS_COMPOSE, "web")["traefik.http.routers.web-https.rule"]
    assert web_rule.endswith("`)") and "&&" not in web_rule, web_rule

    api = _labels(HTTPS_COMPOSE, "api")
    api_rule = api["traefik.http.routers.api-https.rule"]
    assert api_rule.startswith("Host(`") and "DOMAIN:?" in api_rule
    assert api_rule.endswith(" && PathPrefix(`/api`)"), api_rule
    assert api["traefik.http.routers.api-https.middlewares"] == "strip-api-prefix"


def test_https_file_requires_acme_email():
    # The base Traefik command cannot change, so the requirement is enforced by
    # interpolation in the override.
    env = _services(HTTPS_COMPOSE)["traefik"]["environment"]
    assert "ACME_EMAIL:?" in env["ACME_EMAIL"], env


def test_https_file_redirects_plain_http():
    web = _labels(HTTPS_COMPOSE, "web")
    api = _labels(HTTPS_COMPOSE, "api")
    assert (
        web["traefik.http.middlewares.https-redirect.redirectscheme.scheme"] == "https"
    )
    assert web["traefik.http.routers.web.middlewares"] == "https-redirect"
    # Redirect first, then strip the prefix.
    assert (
        api["traefik.http.routers.api.middlewares"]
        == "https-redirect,strip-api-prefix"
    )


def test_https_file_only_adds_to_the_base_file():
    base = _services(PROD_COMPOSE)
    over = _services(HTTPS_COMPOSE)
    assert set(over) <= {"traefik", "api", "web"}
    for service in ("api", "web"):
        assert set(over[service]) == {"labels"}
    # The only base label the override may re-set is the plain-http router's
    # middlewares attachment; everything else is new.
    for service in ("api", "web"):
        base_labels = _labels(PROD_COMPOSE, service)
        for key, value in _labels(HTTPS_COMPOSE, service).items():
            if key in base_labels:
                assert key == f"traefik.http.routers.{service}.middlewares", key
    assert "command" not in over["traefik"]
    assert base["traefik"]["image"] == "traefik:v2.11"


def test_base_prod_file_unchanged():
    """Opt-in means opt-in: the base file's behaviour is frozen here."""
    services = _services(PROD_COMPOSE)
    assert set(services) == {
        "traefik",
        "postgres",
        "redis",
        "api",
        "worker",
        "email_worker",
        "maintenance_worker",
        "beat",
        "web",
    }
    for name in APP_SERVICES:
        assert "image" not in services[name], name

    assert services["api"]["labels"] == [
        "traefik.enable=true",
        "traefik.http.routers.api.rule=PathPrefix(`/api`)",
        "traefik.http.routers.api.entrypoints=web",
        "traefik.http.routers.api.middlewares=strip-api-prefix",
        "traefik.http.routers.api-secure.rule=PathPrefix(`/api`)",
        "traefik.http.routers.api-secure.entrypoints=websecure",
        "traefik.http.routers.api-secure.tls.certresolver=letsencrypt",
        "traefik.http.routers.api-secure.middlewares=strip-api-prefix",
        "traefik.http.routers.api-secure.service=api",
        "traefik.http.middlewares.strip-api-prefix.stripprefix.prefixes=/api",
        "traefik.http.services.api.loadbalancer.server.port=8000",
    ]
    assert services["web"]["labels"] == [
        "traefik.enable=true",
        "traefik.http.routers.web.rule=PathPrefix(`/`)",
        "traefik.http.routers.web.entrypoints=web",
        "traefik.http.routers.web.priority=1",
        "traefik.http.routers.web-secure.rule=PathPrefix(`/`)",
        "traefik.http.routers.web-secure.entrypoints=websecure",
        "traefik.http.routers.web-secure.tls.certresolver=letsencrypt",
        "traefik.http.routers.web-secure.priority=1",
        "traefik.http.routers.web-secure.service=web",
        "traefik.http.services.web.loadbalancer.server.port=3000",
    ]

    for name in ("api", "worker", "email_worker", "maintenance_worker", "beat"):
        assert services[name]["build"] == {
            "context": ".",
            "dockerfile": "apps/api/Dockerfile.prod",
        }, name

    web_build = dict(services["web"]["build"])
    args = web_build.pop("args")
    assert web_build == {"context": "./apps/web", "dockerfile": "Dockerfile.prod"}
    # NEXT_PUBLIC_UPLOAD_CONCURRENCY is being removed by a separate change, so
    # it may be present or absent; every other build arg is frozen.
    args.pop("NEXT_PUBLIC_UPLOAD_CONCURRENCY", None)
    assert args == {
        "NEXT_PUBLIC_API_URL": "/api",
        "NEXT_PUBLIC_BASE_PATH": "${NEXT_PUBLIC_BASE_PATH:-}",
    }
