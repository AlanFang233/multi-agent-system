"""Offline checks for the English Coding Agent python-debugging Skill."""
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / 'skills' / 'coding' / 'python-debugging' / 'SKILL.md'


def _read():
    raw = SKILL.read_text(encoding='utf-8')
    parts = raw.split('---', 2)
    assert len(parts) == 3 and not parts[0].strip()
    return yaml.safe_load(parts[1]), parts[2], raw


def test_metadata_is_valid():
    meta, instructions, raw = _read()
    assert meta['name'] == 'python-debugging'
    assert 1 <= len(meta['description']) <= 1024
    assert instructions.strip()
    assert len(raw) <= 10000


def test_actual_tools_documented():
    _, body, _ = _read()
    for tool in (
        'sandbox:create', 'sandbox:run_python', 'sandbox:run_pytest',
        'filesystem_code:write_file', 'filesystem_code:edit_file',
    ):
        assert tool in body


def test_debugging_safety_and_evidence():
    _, body, _ = _read()
    for rule in (
        'exit_code', 'timed_out', 'three patch-and-retest attempts',
        'Do not weaken', 'Do not read `.env`', 'Never invent',
    ):
        assert rule in body
