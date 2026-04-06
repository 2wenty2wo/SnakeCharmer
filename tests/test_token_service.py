import json
import time

from app.webui.token_service import TokenService


def test_token_status_missing(tmp_path):
    service = TokenService(str(tmp_path / "trakt_token.json"))
    assert service.status() == "missing"


def test_token_status_valid(tmp_path):
    token_file = tmp_path / "trakt_token.json"
    now = int(time.time())
    token_file.write_text(
        json.dumps(
            {
                "access_token": "secret",
                "created_at": now - 10,
                "expires_in": 7200,
                "refresh_token": "refresh-secret",
            }
        )
    )
    service = TokenService(str(token_file))
    assert service.status(now=now) == "valid"
    metadata = service.read_metadata()
    assert metadata is not None
    assert metadata.has_refresh_token is True
    assert metadata.created_at == now - 10
    assert metadata.expires_in == 7200


def test_token_status_expiring_soon(tmp_path):
    token_file = tmp_path / "trakt_token.json"
    token_file.write_text(
        json.dumps({"created_at": 1000, "expires_in": 3600, "refresh_token": "x"})
    )
    service = TokenService(str(token_file))
    assert service.status(now=4500) == "expiring_soon"


def test_token_status_expired(tmp_path):
    token_file = tmp_path / "trakt_token.json"
    token_file.write_text(json.dumps({"created_at": 1000, "expires_in": 60}))
    service = TokenService(str(token_file))
    assert service.status(now=2000) == "expired"


def test_delete_token_file(tmp_path):
    token_file = tmp_path / "trakt_token.json"
    token_file.write_text("{}")
    service = TokenService(str(token_file))
    assert service.delete() is True
    assert not token_file.exists()
