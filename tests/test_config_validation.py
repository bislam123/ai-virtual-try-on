"""Tests for backend/app/config.py's startup validation
(Settings.check_production_secrets, Settings.check_production_cors).

No database, no network, no app startup needed -- these construct Settings
directly (bypassing the module-level singleton) and call the check methods
explicitly, mirroring exactly what backend/app/config.py itself does at
import time (`settings = Settings(); settings.check_production_secrets();
settings.check_production_cors()`).
"""

import pytest

from backend.app.config import InsecureProductionConfigError, Settings

_INSECURE_JWT = "dev-only-insecure-secret-change-me"
_INSECURE_DB_URL = "postgresql+psycopg2://postgres:devpassword@localhost:5432/aitryon"
_SECURE_JWT = "a-real-random-64-character-secret-value-not-the-dev-default-000000"
_SECURE_DB_URL = "postgresql+psycopg2://prod_app_user:a-real-strong-password@db.internal:5432/aitryon"


# --- Local dev / test-suite behavior is unaffected --------------------------


def test_default_environment_is_development():
    """The module-level singleton (backend.app.config.settings) already
    proved this by importing successfully at all -- this pins the actual
    default value so a future change can't silently make "production" the
    default without a test catching it."""
    assert Settings().environment == "development"


def test_development_environment_with_insecure_defaults_does_not_raise():
    """Exactly today's local dev / test-suite experience: insecure
    defaults, no AITRYON_ENVIRONMENT set. Must remain completely
    unaffected by this feature."""
    s = Settings(environment="development", jwt_secret_key=_INSECURE_JWT, database_url=_INSECURE_DB_URL)
    s.check_production_secrets()  # must not raise


def test_unset_environment_with_insecure_defaults_does_not_raise():
    s = Settings(jwt_secret_key=_INSECURE_JWT, database_url=_INSECURE_DB_URL)
    s.check_production_secrets()  # must not raise


# --- Production mode: insecure defaults are rejected -------------------------


def test_production_rejects_insecure_jwt_secret():
    s = Settings(environment="production", jwt_secret_key=_INSECURE_JWT, database_url=_SECURE_DB_URL)
    with pytest.raises(InsecureProductionConfigError, match="AITRYON_JWT_SECRET_KEY"):
        s.check_production_secrets()


def test_production_rejects_insecure_database_credential():
    s = Settings(environment="production", jwt_secret_key=_SECURE_JWT, database_url=_INSECURE_DB_URL)
    with pytest.raises(InsecureProductionConfigError, match="AITRYON_DATABASE_URL"):
        s.check_production_secrets()


def test_production_rejects_insecure_database_credential_even_with_other_host_or_db_name():
    """The check looks for the insecure credential itself (a substring),
    not the whole default URL verbatim -- an operator who changes the
    host/db name but leaves the known-insecure password in place must
    still be caught."""
    s = Settings(
        environment="production",
        jwt_secret_key=_SECURE_JWT,
        database_url="postgresql+psycopg2://postgres:devpassword@some-other-host.example.com:5432/different_db_name",
    )
    with pytest.raises(InsecureProductionConfigError, match="AITRYON_DATABASE_URL"):
        s.check_production_secrets()


def test_production_reports_both_problems_when_both_are_insecure():
    s = Settings(environment="production", jwt_secret_key=_INSECURE_JWT, database_url=_INSECURE_DB_URL)
    with pytest.raises(InsecureProductionConfigError) as exc_info:
        s.check_production_secrets()
    message = str(exc_info.value)
    assert "AITRYON_JWT_SECRET_KEY" in message
    assert "AITRYON_DATABASE_URL" in message


# --- Production mode: secure values are accepted ------------------------------


def test_production_accepts_secure_values():
    s = Settings(environment="production", jwt_secret_key=_SECURE_JWT, database_url=_SECURE_DB_URL)
    s.check_production_secrets()  # must not raise


# --- Error messages never contain the actual secret values -------------------


@pytest.mark.parametrize(
    "jwt_secret_key,database_url",
    [
        (_INSECURE_JWT, _SECURE_DB_URL),
        (_SECURE_JWT, _INSECURE_DB_URL),
        (_INSECURE_JWT, _INSECURE_DB_URL),
    ],
)
def test_error_message_never_contains_the_secret_values(jwt_secret_key, database_url):
    s = Settings(environment="production", jwt_secret_key=jwt_secret_key, database_url=database_url)
    with pytest.raises(InsecureProductionConfigError) as exc_info:
        s.check_production_secrets()
    message = str(exc_info.value)
    assert _INSECURE_JWT not in message
    assert "devpassword" not in message
    assert _SECURE_JWT not in message
    assert "a-real-strong-password" not in message


def test_error_message_never_contains_a_custom_secret_value_either():
    """Not just the two known defaults -- an arbitrary custom secret must
    never appear either, in case a future caller passes one through."""
    surprising_secret = "sk_live_totally_made_up_regression_probe_value_123456"
    s = Settings(environment="production", jwt_secret_key=_INSECURE_JWT, database_url=_SECURE_DB_URL)
    # jwt_secret_key is still the insecure default here (deliberately, to
    # trigger the raise) -- database_url is swapped for one containing the
    # "surprising" secret-looking string, which must not leak either, even
    # though database_url itself isn't the field that's wrong in this case.
    s.database_url = f"postgresql+psycopg2://prod:{surprising_secret}@db.internal:5432/aitryon"
    with pytest.raises(InsecureProductionConfigError) as exc_info:
        s.check_production_secrets()
    assert surprising_secret not in str(exc_info.value)


def test_real_env_var_pathway_does_not_leak_via_pydantic_error_wrapping(monkeypatch):
    """Regression test for the actual bug caught while building this
    feature: raising from inside a pydantic @model_validator produces a
    ValidationError whose own __str__() includes an `input_value=...`
    fragment containing the real configured values (confirmed directly
    during development -- not a hypothetical). check_production_secrets()
    is a plain method for exactly this reason; this test exercises the
    real construction pathway (env vars, not direct kwargs) end to end."""
    monkeypatch.setenv("AITRYON_ENVIRONMENT", "production")
    monkeypatch.setenv("AITRYON_JWT_SECRET_KEY", _INSECURE_JWT)
    monkeypatch.setenv("AITRYON_DATABASE_URL", _INSECURE_DB_URL)

    s = Settings()
    with pytest.raises(InsecureProductionConfigError) as exc_info:
        s.check_production_secrets()
    message = str(exc_info.value)
    assert _INSECURE_JWT not in message
    assert "devpassword" not in message


def test_module_level_settings_singleton_loaded_successfully():
    """backend.app.config's module-level `settings = Settings();
    settings.check_production_secrets()` already ran once at import time
    (by the time this test file itself was collected) -- if it had raised,
    nothing in this whole test suite would have been able to import
    anything that depends on backend.app.config. This just makes that
    implicit proof explicit and named."""
    from backend.app.config import settings

    assert settings.environment == "development"


# --- Settings.check_production_cors ------------------------------------------


def test_development_environment_with_wildcard_cors_does_not_raise():
    """Local dev / test-suite behavior unaffected, same as the secrets
    check above -- this only ever activates in production."""
    s = Settings(environment="development", cors_origins=["*"])
    s.check_production_cors()  # must not raise


def test_production_rejects_wildcard_cors_origin():
    s = Settings(
        environment="production",
        jwt_secret_key=_SECURE_JWT,
        database_url=_SECURE_DB_URL,
        cors_origins=["*"],
    )
    with pytest.raises(InsecureProductionConfigError, match="AITRYON_CORS_ORIGINS"):
        s.check_production_cors()


def test_production_rejects_wildcard_mixed_with_real_origins():
    """A wildcard anywhere in the list is still a wildcard -- "some real
    origins plus *" is not meaningfully safer than "just *"."""
    s = Settings(environment="production", cors_origins=["https://app.example.com", "*"])
    with pytest.raises(InsecureProductionConfigError, match="AITRYON_CORS_ORIGINS"):
        s.check_production_cors()


def test_production_accepts_explicit_real_origins():
    s = Settings(
        environment="production",
        jwt_secret_key=_SECURE_JWT,
        database_url=_SECURE_DB_URL,
        cors_origins=["https://app.example.com"],
    )
    s.check_production_cors()  # must not raise


def test_production_does_not_reject_the_localhost_dev_default_origins():
    """Deliberately out of scope, per check_production_cors()'s own
    docstring: leaving cors_origins at its localhost dev default in
    production is a functional misconfiguration (the frontend can't reach
    the API), not a security one -- it's more restrictive than intended,
    not less, so this check does not flag it."""
    s = Settings(environment="production", jwt_secret_key=_SECURE_JWT, database_url=_SECURE_DB_URL)
    s.check_production_cors()  # must not raise, even though cors_origins is still the dev default
