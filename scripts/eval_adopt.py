"""후보 비교 결과 JSON에서 과제별 채택 기록과 결정 메모를 만드는 CLI(KAN-146).

채택은 사람이 정한다. 이 도구는 기록을 항상 status PROPOSED로 쓰며, ADOPTED 전환은 사람이 기록 파일을 고쳐
승인 뒤 eval/policy/adoptions/에 커밋한다. 결과 파일은 읽기만 하고 기존 기록·메모 파일은 덮어쓰지 않는다.
네트워크·DB를 쓰지 않는다.
"""

import argparse
import json
import sys
from pathlib import Path

from eval.adoption import build_record, render_decision_note
from eval.release_gate import GATES_PATH, load_gates


def parse_rejects(items: list[str]) -> dict[str, str]:
    """--reject name=reason 목록을 dict로 만든다.

    Args:
        items: 'name=reason' 문자열 목록.

    Returns:
        dict[str, str]: {후보 이름: 사유}. '='가 없거나 이름이 겹치면 ValueError다.
    """
    pairs = [item.partition("=") for item in items]
    if any(not sep or not name for name, sep, _ in pairs) or len({p[0] for p in pairs}) != len(
        pairs
    ):
        raise ValueError("EVAL_INVALID_INPUT")
    return {name: reason for name, _, reason in pairs}


def main(argv: list[str] | None = None) -> int:
    """인자를 읽어 채택 기록 JSON(--out)과 결정 메모 Markdown(--note)을 새 파일로 쓴다.

    Args:
        argv: 명령행 인자(테스트 주입용).

    Returns:
        int: 0 성공, 1 입력 오류(--out과 --note가 같은 경로 포함)·이미 있는 출력 파일. 표준 출력에는
            게이트 건수·blocker만 낸다.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", required=True, choices=["classification", "draft", "embedding"])
    parser.add_argument("--results", type=Path, nargs="+", required=True)
    parser.add_argument("--adopt", required=True)
    parser.add_argument("--gates", type=Path, default=GATES_PATH)
    parser.add_argument("--decided-by", required=True)
    parser.add_argument("--rationale", type=Path, required=True, help="사유 한 단락 텍스트 파일")
    parser.add_argument("--reject", action="append", default=[], help="name=reason, 반복")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--note", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        # 같은 경로면 두 번째 쓰기가 실패하며 빈 기록 파일이 남는다.
        if args.out.resolve() == args.note.resolve():
            raise ValueError
        record = build_record(
            [path.read_bytes() for path in args.results],
            task=args.task,
            adopted_candidate=args.adopt,
            gates=load_gates(args.gates),
            decided_by=args.decided_by,
            rationale=args.rationale.read_text(encoding="utf-8"),
            rejected=parse_rejects(args.reject),
        )
        note = render_decision_note(record)
        # 한쪽만 쓰고 멈추지 않도록 둘 다 없는지 먼저 본다.
        if args.out.exists() or args.note.exists():
            raise FileExistsError
        with (
            args.out.open("x", encoding="utf-8") as out,
            args.note.open("x", encoding="utf-8") as md,
        ):
            out.write(record.to_json())
            md.write(note)
    except FileExistsError:
        print("EVAL_OUTPUT_EXISTS", file=sys.stderr)
        return 1
    except (ValueError, OSError):
        print("EVAL_INVALID_INPUT", file=sys.stderr)
        return 1
    print(json.dumps({"counts": record.gates["counts"], "blocker": record.gates["blocker"]}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
