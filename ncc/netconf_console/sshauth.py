"""Explicit SSH authentication, keeping login passwords separate from key secrets."""
from pathlib import Path

import paramiko
from ncclient.transport.errors import AuthenticationError


def authenticate(transport, username, password, filenames, allow_agent, look_for_keys,
                 mode="auto", passphrase=None):
    if mode not in {"auto", "password", "private-key", "agent"}:
        raise AuthenticationError("未知 SSH 認證方式。")
    failures = []
    allowed = set()

    def attempt(work, label):
        try:
            work()
            if transport.is_authenticated():
                return True
            failures.append(label + "：尚需其他認證步驟")
        except paramiko.BadAuthenticationType as exc:
            allowed.update(exc.allowed_types)
            failures.append(label + "：server 不接受此認證方式")
        except Exception as exc:
            # Never include raw exceptions (they may echo credentials or paths).
            failures.append(label + "：" + type(exc).__name__)
        return False

    paths = list(filenames) if mode in {"auto", "private-key"} else []
    if mode == "auto" and look_for_keys:
        for folder in (Path.home() / ".ssh", Path.home() / "ssh"):
            paths += [str(folder / name) for name in ("id_rsa", "id_ecdsa", "id_ed25519")
                      if (folder / name).is_file()]
    for filename in dict.fromkeys(paths):
        def key_auth():
            key = paramiko.PKey.from_path(str(Path(filename).expanduser()),
                                         passphrase.encode("utf-8") if passphrase else None)
            transport.auth_publickey(username, key)
        if attempt(key_auth, "私鑰（請檢查檔案及私鑰密碼）"):
            return
    if mode == "agent" or (mode == "auto" and allow_agent):
        agent = paramiko.Agent()
        try:
            for key in agent.get_keys():
                if attempt(lambda: transport.auth_publickey(username, key), "SSH Agent"):
                    return
        finally:
            agent.close()
    if mode in {"auto", "password"} and password is not None:
        if attempt(lambda: transport.auth_password(username, password), "登入密碼"):
            return
    detail = "；".join(failures) or "沒有可用認證資料"
    if allowed:
        detail += "；server 目前允許：" + ", ".join(sorted(allowed))
    raise AuthenticationError(detail + "。登入密碼與私鑰密碼是不同欄位；host key 驗證不控制帳號認證。")
