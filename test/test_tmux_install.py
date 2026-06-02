from _pkg import tmux_install


def test_install_command_macos_brew(monkeypatch):
    monkeypatch.setattr(tmux_install, "_which", lambda n: "/opt/homebrew/bin/brew" if n == "brew" else None)
    assert tmux_install.install_command("Darwin") == "brew install tmux"


def test_install_command_linux_apt(monkeypatch):
    monkeypatch.setattr(tmux_install, "_which", lambda n: "/usr/bin/apt-get" if n == "apt-get" else None)
    assert tmux_install.install_command("Linux") == "sudo apt-get install -y tmux"


def test_install_command_unknown_returns_none(monkeypatch):
    monkeypatch.setattr(tmux_install, "_which", lambda n: None)
    assert tmux_install.install_command("Linux") is None
