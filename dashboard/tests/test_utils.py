import copy

import pytest

from utils import deep_merge, sanitize_name


class TestSanitizeName:
    def test_lowercase_and_strip_special(self):
        assert sanitize_name("My_Bot.Name!@#123") == "my-bot-name-123"

    def test_collapse_consecutive_hyphens(self):
        assert sanitize_name("bot---name") == "bot-name"

    def test_truncate_to_48(self):
        result = sanitize_name("a" * 100)
        assert len(result) == 48

    def test_path_traversal_rejected(self):
        result = sanitize_name("../etc/passwd")
        assert result == "etc-passwd"
        assert ".." not in result
        assert "/" not in result

    @pytest.mark.parametrize("name", ["!!!", "---", ""])
    def test_empty_raises(self, name):
        with pytest.raises(ValueError):
            sanitize_name(name)


class TestDeepMerge:
    def test_flat_key_override(self):
        result = deep_merge({"a": 1, "b": 2}, {"b": 3})
        assert result == {"a": 1, "b": 3}

    def test_nested_recursive_merge(self):
        base = {"x": {"a": 1, "b": 2}}
        override = {"x": {"b": 3, "c": 4}}
        result = deep_merge(base, override)
        assert result == {"x": {"a": 1, "b": 3, "c": 4}}

    def test_override_not_mutated(self):
        base = {"a": 1}
        override = {"b": {"nested": [1, 2]}}
        override_copy = copy.deepcopy(override)
        result = deep_merge(base, override)
        result["b"]["nested"].append(3)
        assert override == override_copy
