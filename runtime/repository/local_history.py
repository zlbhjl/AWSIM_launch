from __future__ import annotations

from pathlib import Path


class LocalHistory:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def load(self) -> set[int]:
        if not self.path.exists():
            return set()

        processed: set[int] = set()
        with self.path.open("r", encoding="utf-8") as handle:
            for line in handle:
                value = line.strip()
                if value.isdigit():
                    processed.add(int(value))
        return processed

    def contains(self, loop_num: int) -> bool:
        return loop_num in self.load()

    def append(self, loop_num: int) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(f"{loop_num}\n")
