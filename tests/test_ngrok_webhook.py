from scripts.ngrok_webhook import find_https_tunnel, read_env, set_env_var


def test_set_env_var_replaces_in_place_and_keeps_comments(tmp_path):
    env = tmp_path / ".env"
    env.write_text("# Telegram\nBOT_TOKEN=x\nPUBLIC_BASE_URL=http://localhost:8000\nA=1\n")
    set_env_var(env, "PUBLIC_BASE_URL", "https://abc.ngrok-free.app")
    assert env.read_text() == "# Telegram\nBOT_TOKEN=x\nPUBLIC_BASE_URL=https://abc.ngrok-free.app\nA=1\n"


def test_set_env_var_appends_when_missing_and_ignores_commented_key(tmp_path):
    env = tmp_path / ".env"
    env.write_text("# PUBLIC_BASE_URL=old\nA=1\n")
    set_env_var(env, "PUBLIC_BASE_URL", "https://abc.ngrok-free.app")
    assert env.read_text().splitlines() == [
        "# PUBLIC_BASE_URL=old", "A=1", "PUBLIC_BASE_URL=https://abc.ngrok-free.app",
    ]


def test_read_env_strips_quotes_and_comments(tmp_path):
    env = tmp_path / ".env"
    env.write_text('# c\nDB_HOST="localhost"\nTOKEN=a:b=c\n\n')
    assert read_env(env) == {"DB_HOST": "localhost", "TOKEN": "a:b=c"}


def test_find_https_tunnel_matches_port_and_scheme():
    tunnels = [
        {"public_url": "http://x.ngrok.app", "config": {"addr": "http://localhost:8000"}},
        {"public_url": "https://y.ngrok.app", "config": {"addr": "http://localhost:5000"}},
        {"public_url": "https://z.ngrok.app", "config": {"addr": "http://localhost:8000"}},
    ]
    assert find_https_tunnel(tunnels, 8000) == "https://z.ngrok.app"
    assert find_https_tunnel(tunnels, 9000) is None
