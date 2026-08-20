from __future__ import annotations

import sys
from pathlib import Path

from docx import Document
from docxtpl import DocxTemplate


EXPECTED_KEYS = {
    "general_manager", "wu_aoxiang", "yang_yilin", "ye_mengzhen",
    "zhang_feilong", "lin_baili", "liang_jialong", "liu_hanwen",
    "xu_xinxin", "gao_canjian", "liu_jindi", "ma_gengbin", "zhang_chunwei",
}


def main(template_text: str, rendered_text: str) -> None:
    template_path = Path(template_text)
    renderer = DocxTemplate(template_path)
    keys = set(renderer.get_undeclared_template_variables())
    assert keys == EXPECTED_KEYS, sorted(keys)
    renderer.render({key: "1. 本周工作事项\n2. 下周推进计划" for key in keys})
    renderer.save(rendered_text)
    rendered = Document(rendered_text)
    text = "\n".join(p.text for p in rendered.paragraphs)
    assert "{{" not in text and "}}" not in text
    assert text.count("本周工作事项") == len(EXPECTED_KEYS)
    print(f"Render smoke test passed: {Path(rendered_text).resolve()}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
