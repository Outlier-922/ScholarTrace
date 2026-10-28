"""Credential wizard must store a hash and never replace an existing config."""
import sys
from dotenv import dotenv_values
import pytest
from scripts import configure_demo
from backend.app.demo_access import verify_password


def test_wizard_hides_password_and_preserves_hash(tmp_path, monkeypatch, capsys):
    target = tmp_path / 'private.env'
    monkeypatch.setattr(sys, 'argv', ['configure_demo.py', '--output', str(target)])
    answers = iter(['', ''])
    monkeypatch.setattr('builtins.input', lambda _: next(answers))
    password = 'SYNTHETIC private test only'
    monkeypatch.setattr(configure_demo, 'getpass', lambda _: password)
    configure_demo.main()
    text = target.read_text(encoding='utf-8')
    values = dotenv_values(target)
    assert password not in text and password not in capsys.readouterr().out
    assert verify_password(password, values['SCHOLARTRACE_DEMO_PASSWORD_HASH'])
    assert values['SCHOLARTRACE_DEMO_ORIGIN'] == 'http://127.0.0.1:8000'
    assert values['SCHOLARTRACE_DEMO_ALLOW_WRITES'] == 'false' and values['LLM_API_KEY'] == ''
    with pytest.raises(SystemExit, match='already exists'):
        configure_demo.main()
    assert target.read_text(encoding='utf-8') == text


def test_mismatched_password_does_not_write(tmp_path, monkeypatch):
    target = tmp_path / 'private.env'
    monkeypatch.setattr(sys, 'argv', ['configure_demo.py', '--output', str(target)])
    answers = iter(['http://127.0.0.1:8000', 'reviewer'])
    passwords = iter(['SYNTHETIC password one', 'SYNTHETIC password two'])
    monkeypatch.setattr('builtins.input', lambda _: next(answers))
    monkeypatch.setattr(configure_demo, 'getpass', lambda _: next(passwords))
    with pytest.raises(SystemExit, match='Passwords differ'):
        configure_demo.main()
    assert not target.exists()


def test_plain_http_public_origin_rejected(tmp_path, monkeypatch):
    target = tmp_path / 'private.env'
    monkeypatch.setattr(sys, 'argv', ['configure_demo.py', '--output', str(target)])
    monkeypatch.setattr('builtins.input', lambda _: 'http://public.example')
    with pytest.raises(SystemExit, match='Invalid demo origin'):
        configure_demo.main()
    assert not target.exists()
