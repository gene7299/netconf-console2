"""Explicit SSH authentication, keeping login passwords separate from key secrets."""
from pathlib import Path

import paramiko
from ncclient.transport.errors import AuthenticationError


_AUTH_METHOD_LABELS = {
    "password": "登入密碼",
    "publickey": "SSH 私鑰",
    "keyboard-interactive": "互動式登入",
    "gssapi-with-mic": "GSSAPI／Kerberos",
}


def _auth_method_text(methods):
    return ", ".join(_AUTH_METHOD_LABELS.get(method, method) for method in sorted(methods))


def _user_message(mode, password, failures, allowed):
    allowed = set(allowed)
    if allowed == {"password"}:
        if password is None:
            return ("SSH 認證失敗\n\n"
                    "DUT 只接受登入密碼，但目前沒有提供登入密碼。\n\n"
                    "請在 SSH 認證頁填入登入密碼，並將認證方式選為「password」。")
        return ("SSH 認證失敗\n\n"
                "DUT 只接受登入密碼，但目前登入密碼未通過。\n\n"
                "請確認 SSH 使用者名稱、登入密碼，以及 DUT 是否允許此帳號使用 SSH password 登入。")
    if "publickey" in allowed and "password" not in allowed:
        return ("SSH 認證失敗\n\n"
                "DUT 只接受 SSH 私鑰，登入密碼無法使用。\n\n"
                "請選擇「private-key」或「auto」，指定 DUT 已授權的私鑰；私鑰有加密時再填入私鑰密碼。")
    if mode == "private-key":
        return ("SSH 認證失敗\n\n"
                "指定的 SSH 私鑰未通過認證。\n\n"
                "請確認私鑰檔案、私鑰密碼，以及對應公鑰是否已加入 DUT 帳號。")
    if mode == "agent":
        return ("SSH 認證失敗\n\n"
                "SSH Agent 沒有提供可用的認證。\n\n"
                "請確認 Agent 正在執行且已載入 DUT 已授權的私鑰，或改用 password／private-key。")
    if any(item.startswith("登入密碼：") for item in failures):
        return ("SSH 認證失敗\n\n"
                "登入密碼未通過。\n\n"
                "請確認 SSH 使用者名稱與登入密碼，或改用 DUT 已授權的 SSH 私鑰。")
    if not failures:
        return ("SSH 認證失敗\n\n"
                "沒有可用的 SSH 認證資料。\n\n"
                "請填入登入密碼，或指定 DUT 已授權的 SSH 私鑰。")
    return ("SSH 認證失敗\n\n"
            "DUT 拒絕目前提供的 SSH 認證資料。\n\n"
            "請確認 SSH 使用者名稱、認證方式與登入資料，或改用 DUT 已授權的 SSH 私鑰。")


def _authentication_error(mode, password, failures, allowed):
    error = AuthenticationError(_user_message(mode, password, failures, allowed))
    details = []
    if failures:
        details.append("認證嘗試紀錄：\n" + "\n".join("• " + item for item in failures))
    if allowed:
        details.append("DUT 回報可接受的認證方式：" + _auth_method_text(allowed))
    error.auth_details = "\n\n".join(details)
    return error


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
    raise _authentication_error(mode, password, failures, allowed)
