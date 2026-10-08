"""Plain JSON state on disk: one positions file plus an append-only event log."""

import json
import os
import tempfile
import time
from pathlib import Path


class Store:
    def __init__(self, data_dir: Path):
        self.dir = data_dir
        self.dir.mkdir(parents=True, exist_ok=True)
        self.positions_path = self.dir / "positions.json"
        self.events_path = self.dir / "events.jsonl"

    def load_positions(self) -> list[dict]:
        if not self.positions_path.exists():
            return []
        return json.loads(self.positions_path.read_text())

    def save_positions(self, positions: list[dict]) -> None:
        fd, tmp = tempfile.mkstemp(dir=self.dir, suffix=".tmp")
        with os.fdopen(fd, "w") as f:
            json.dump(positions, f, indent=2)
        os.replace(tmp, self.positions_path)   # atomic: a crash never leaves half a file

    def event(self, kind: str, **data) -> dict:
        e = {"ts": int(time.time() * 1000), "kind": kind, **data}
        with self.events_path.open("a") as f:
            f.write(json.dumps(e) + "\n")
        return e

    def events(self, limit: int = 100) -> list[dict]:
        if not self.events_path.exists():
            return []
        lines = self.events_path.read_text().splitlines()[-limit:]
        return [json.loads(l) for l in reversed(lines)]
