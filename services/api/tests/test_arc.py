from unittest.mock import MagicMock, patch

import httpx
import pytest

from tothemoon_api.arc import ping_arc_network
from tothemoon_api.config import Settings


@pytest.fixture
def mock_settings():
    return Settings(arc_testnet_rpc_url="https://fake-arc-rpc.local")


@patch("tothemoon_api.arc.get_settings")
@patch("tothemoon_api.arc.httpx.post")
def test_ping_arc_network_success(mock_post, mock_get_settings, mock_settings):
    mock_get_settings.return_value = mock_settings

    mock_response = MagicMock()
    mock_response.json.return_value = {
        "jsonrpc": "2.0",
        "id": 1,
        "result": "0x4cef52",
    }  # 5042002 in hex
    mock_response.raise_for_status.return_value = None
    mock_post.return_value = mock_response

    result = ping_arc_network()

    assert result["status"] == "online"
    assert result["chain_id"] == 5042002
    assert result["url"] == "https://fake-arc-rpc.local"


@patch("tothemoon_api.arc.get_settings")
@patch("tothemoon_api.arc.httpx.post")
def test_ping_arc_network_degraded(mock_post, mock_get_settings, mock_settings):
    mock_get_settings.return_value = mock_settings

    mock_response = MagicMock()
    mock_response.json.return_value = {"jsonrpc": "2.0", "id": 1}  # No result
    mock_response.raise_for_status.return_value = None
    mock_post.return_value = mock_response

    result = ping_arc_network()

    assert result["status"] == "degraded"
    assert "error" in result


@patch("tothemoon_api.arc.get_settings")
@patch("tothemoon_api.arc.httpx.post")
def test_ping_arc_network_offline(mock_post, mock_get_settings, mock_settings):
    mock_get_settings.return_value = mock_settings

    mock_post.side_effect = httpx.RequestError("Connection timeout")

    result = ping_arc_network()

    assert result["status"] == "offline"
    assert "error" in result
