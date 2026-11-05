"""백엔드 표시 이름과 화면 표가 같은지 본다. 두 폴더가 다 있는 저장소 전체 검사다."""

import re
import unittest
from pathlib import Path

from backend.engine.rfp.vocabulary import ATTRIBUTE_TEXT

LABELS = Path(__file__).resolve().parents[2] / "frontend/src/rfp/labels.ts"


class DisplayLabelsTest(unittest.TestCase):
    def test_attribute_names_match_the_screen(self) -> None:
        """속성 표시 이름은 화면 ATTRIBUTE_LABEL과 같다.

        Returns:
            None: 두 표를 비교한다.
        """
        text = LABELS.read_text(encoding="utf-8")
        block = text.split("export const ATTRIBUTE_LABEL", 1)[1].split("};", 1)[0]
        self.assertEqual(dict(re.findall(r"(\w+): '([^']+)'", block)), ATTRIBUTE_TEXT)
