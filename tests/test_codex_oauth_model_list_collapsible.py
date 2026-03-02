from pathlib import Path
import unittest


def _model_tab_source() -> str:
    return Path("web/src/components/settings/ModelTab.tsx").read_text(encoding="utf-8")


class TestCodexOAuthModelListCollapsible(unittest.TestCase):
    def test_codex_model_list_defaults_to_collapsed(self) -> None:
        """Codex 卡片中的模型列表应默认折叠。"""
        source = _model_tab_source()
        self.assertIn(
            "const [codexModelsExpanded, setCodexModelsExpanded] = useState(false);",
            source,
        )

    def test_codex_model_list_has_click_to_expand_gate(self) -> None:
        """Codex 卡片中的完整模型列表应由点击展开后再渲染。"""
        source = _model_tab_source()
        self.assertIn("onClick={() => setCodexModelsExpanded((v) => !v)}", source)
        self.assertIn("{codexModelsExpanded && (", source)
