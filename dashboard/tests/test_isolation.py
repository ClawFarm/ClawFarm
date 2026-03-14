from unittest.mock import MagicMock, patch

import config
from isolation import _apply_network_isolation, _build_iptables_image, _remove_network_isolation
from templates import generate_config, write_bot_files
from utils import read_meta


class TestNetworkIsolation:
    """Tests for per-bot iptables-based network isolation."""

    def test_apply_generates_correct_script(self):
        """Verify _apply_network_isolation runs the correct iptables script."""
        mock_client = MagicMock()
        mock_network = MagicMock()
        mock_network.id = "abcdef123456789"
        mock_client.networks.get.return_value = mock_network

        with patch.dict("os.environ", {"LLM_HOST": "10.0.0.5", "LLM_PORT": "8000"}):
            _apply_network_isolation(mock_client, "openclaw-net-test", "test")

        mock_client.containers.run.assert_called_once()
        call_args = mock_client.containers.run.call_args
        assert call_args[0][0] == config._IPTABLES_IMAGE
        script = call_args[1]["command"][2]  # ["sh", "-c", script]
        assert "CF-test" in script
        assert "br-abcdef123456" in script
        assert "10.0.0.0/8" in script
        assert "172.16.0.0/12" in script
        assert "192.168.0.0/16" in script
        assert "10.0.0.5" in script
        assert "8000" in script
        assert call_args[1]["network_mode"] == "host"
        assert "NET_ADMIN" in call_args[1]["cap_add"]
        assert call_args[1]["remove"] is True

    def test_apply_no_llm_rule_when_unset(self):
        """No LLM ACCEPT rule when LLM_HOST/LLM_PORT are not set."""
        mock_client = MagicMock()
        mock_network = MagicMock()
        mock_network.id = "abcdef123456789"
        mock_client.networks.get.return_value = mock_network

        with patch.dict("os.environ", {"LLM_HOST": "", "LLM_PORT": ""}, clear=False):
            _apply_network_isolation(mock_client, "openclaw-net-test", "test")

        script = mock_client.containers.run.call_args[1]["command"][2]
        # The LLM rule line should be empty (just whitespace)
        assert "10.0.0.5" not in script
        # But RFC1918 blocks should still be there
        assert "10.0.0.0/8" in script

    def test_apply_returns_false_on_network_not_found(self):
        """Returns False when the Docker network doesn't exist."""
        mock_client = MagicMock()
        import docker.errors
        mock_client.networks.get.side_effect = docker.errors.NotFound("not found")
        assert _apply_network_isolation(mock_client, "openclaw-net-gone", "gone") is False

    def test_apply_returns_false_on_container_run_failure(self):
        """Returns False and logs warning when ephemeral container fails."""
        mock_client = MagicMock()
        mock_network = MagicMock()
        mock_network.id = "abcdef123456789"
        mock_client.networks.get.return_value = mock_network
        mock_client.containers.run.side_effect = RuntimeError("no iptables")

        with patch.dict("os.environ", {"LLM_HOST": "", "LLM_PORT": ""}):
            result = _apply_network_isolation(mock_client, "openclaw-net-test", "test")
        assert result is False

    def test_remove_generates_cleanup_script(self):
        """Verify _remove_network_isolation cleans up chain and DOCKER-USER jump."""
        mock_client = MagicMock()
        mock_network = MagicMock()
        mock_network.id = "abcdef123456789"
        mock_client.networks.get.return_value = mock_network

        result = _remove_network_isolation(mock_client, "openclaw-net-test", "test")
        assert result is True

        script = mock_client.containers.run.call_args[1]["command"][2]
        assert "CF-test" in script
        assert "br-abcdef123456" in script
        assert "-D DOCKER-USER" in script
        assert "-F CF-test" in script
        assert "-X CF-test" in script

    def test_remove_returns_false_on_failure(self):
        """Returns False when cleanup container fails."""
        import docker.errors
        mock_client = MagicMock()
        mock_client.networks.get.side_effect = docker.errors.NotFound("gone")
        mock_client.containers.run.side_effect = RuntimeError("fail")
        assert _remove_network_isolation(mock_client, "openclaw-net-test", "test") is False

    def test_chain_name_truncated(self):
        """Chain name is truncated to CF- + 25 chars max."""
        mock_client = MagicMock()
        mock_network = MagicMock()
        mock_network.id = "abcdef123456789"
        mock_client.networks.get.return_value = mock_network

        long_name = "a" * 50
        with patch.dict("os.environ", {"LLM_HOST": "", "LLM_PORT": ""}):
            _apply_network_isolation(mock_client, f"openclaw-net-{long_name}", long_name)

        script = mock_client.containers.run.call_args[1]["command"][2]
        expected_chain = f"CF-{'a' * 25}"
        assert expected_chain in script

    def test_create_bot_stores_isolation_in_meta(self, bot_env):
        """Metadata includes network_isolation when creating a bot."""
        cfg = generate_config("iso-bot")
        write_bot_files("iso-bot", cfg, network_isolation=True)
        meta = read_meta("iso-bot")
        assert meta["network_isolation"] is True

    def test_create_bot_default_isolation_true(self, bot_env):
        """Default network_isolation is True when not specified."""
        cfg = generate_config("default-bot")
        write_bot_files("default-bot", cfg)
        meta = read_meta("default-bot")
        assert meta["network_isolation"] is True

    def test_create_bot_isolation_false(self, bot_env):
        """Metadata stores network_isolation=False when explicitly set."""
        cfg = generate_config("open-bot")
        write_bot_files("open-bot", cfg, network_isolation=False)
        meta = read_meta("open-bot")
        assert meta["network_isolation"] is False

    def test_build_iptables_image_skips_when_present(self):
        """_build_iptables_image doesn't rebuild if image already exists."""
        mock_client = MagicMock()
        mock_client.images.get.return_value = MagicMock()  # Image found
        _build_iptables_image(mock_client)
        mock_client.images.build.assert_not_called()

    def test_build_iptables_image_builds_when_missing(self):
        """_build_iptables_image builds from inline Dockerfile when image not found."""
        import docker.errors
        mock_client = MagicMock()
        mock_client.images.get.side_effect = docker.errors.ImageNotFound("not found")
        _build_iptables_image(mock_client)
        mock_client.images.build.assert_called_once()
        call_kwargs = mock_client.images.build.call_args[1]
        assert call_kwargs["tag"] == config._IPTABLES_IMAGE
