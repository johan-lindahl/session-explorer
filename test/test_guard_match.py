from _pkg import guard_match


RULES = [{"exe": "docker", "sub": ["compose", "up"]},
         {"exe": "playwright", "sub": ["test"]}]


def test_matches_exact_subcommand():
    assert guard_match.matches("docker compose up -d", RULES) is True


def test_basename_of_absolute_exe():
    assert guard_match.matches("/usr/local/bin/docker compose up", RULES) is True


def test_does_not_match_other_subcommand():
    assert guard_match.matches("docker ps", RULES) is False


def test_up_must_not_match_cleanup():
    assert guard_match.matches("npm run cleanup", RULES) is False


def test_strips_leading_cd_and_env_assignment():
    assert guard_match.matches("cd /x && FOO=1 docker compose up", RULES) is True


def test_unparseable_returns_false_fail_open():
    # command substitution / shell body it cannot confidently parse → no match.
    assert guard_match.matches("bash -c 'docker compose up'", RULES) is False
