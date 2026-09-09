# netconf-console2 Windows 原始碼交付包

此資料夾是可安裝、可閱讀及可修改的最小原始碼包，不包含 Windows 單檔
EXE、測試、Git metadata、PyInstaller 建置檔或 Python cache。

## 必要條件

- Windows 10/11
- Python 3.10 或更新版本，且 Windows `py` launcher 可用
- 第一次安裝通常需要網路，供 pip 下載 Python 相依套件

## 最快安裝方式

在 PowerShell 進入本資料夾後執行：

```powershell
.\install-netconf-console2.ps1
.\run-netconf-console2.ps1 --interactive
.\run-netconf-console2-gui.ps1
```

安裝腳本會建立本資料夾專用的 `.venv`，並從這份原始碼執行 `pip install`。
不會修改系統 Python，也不需要以系統管理員身分執行。

等效的手動安裝命令：

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade .
.\.venv\Scripts\netconf-console2.exe --help
```

如需安裝隨附的預先建置 wheel，而不是目前原始碼：

```powershell
.\install-netconf-console2.ps1 -UseWheel
```

## 原始碼內容

```text
exportToColleague\
├── ncc\netconf_console\       application source
├── setup.py                   package metadata
├── pyproject.toml             Python build-system metadata
├── requirements-runtime.txt   runtime dependency list
├── install-netconf-console2.ps1
├── run-netconf-console2.ps1
├── run-netconf-console2-gui.ps1
├── netconf_console2-3.4.0-py3-none-any.whl   optional fallback
├── GUI_GUIDE_ZH_TW.md
├── COMMAND_REFERENCE_ZH_TW.md
├── INSTALL_WINDOWS.md
├── LICENSE.txt
└── SHA256SUMS.txt
```

Runtime dependencies 包含 `ncclient`、`paramiko`、`lxml`、`prompt-toolkit`、`pyang`
及其遞迴相依套件。若目標電腦完全離線，必須另外準備與目標 Python 版本和
Windows 架構相符的 wheelhouse；只有 application wheel 並不足以離線安裝。

## 四種連線方式

GUI 同樣支援以下四種模式。使用包含 Tcl/Tk 的 Windows Python，執行
`run-netconf-console2-gui.ps1` 後填寫上排連線欄位；預設來源是 running。
完整操作與安全限制請看 [GUI_GUIDE_ZH_TW.md](GUI_GUIDE_ZH_TW.md)。

Direct SSH：

```powershell
.\run-netconf-console2.ps1 `
  --host 192.168.9.9 --port 830 --transport ssh `
  --username oranuser --password --interactive
```

Direct TLS / mTLS：

```powershell
.\run-netconf-console2.ps1 `
  --host 192.168.9.9 --port 6513 --transport tls `
  --cert .\certs\client.crt --key .\certs\client.key `
  --trusted-ca .\certs\ca.pem --tls-server-name oru.example `
  --interactive
```

SSH Call Home：

```powershell
.\run-netconf-console2.ps1 `
  --call-home --transport ssh `
  --listen-host 0.0.0.0 --listen-port 4334 `
  --username oranuser --password --interactive
```

TLS Call Home：

```powershell
.\run-netconf-console2.ps1 `
  --call-home --transport tls `
  --listen-host 0.0.0.0 --listen-port 4335 `
  --cert .\certs\client.crt --key .\certs\client.key `
  --trusted-ca .\certs\ca.pem --tls-server-name oru.example `
  --interactive
```

`--password` 不帶明文值時會顯示遮罩輸入。Call Home 需要 Windows Firewall
允許指定的 inbound TCP port，並讓 O-RU 能路由到此 Windows 主機。

## Pretty XML 與直接存檔

若要直接保存排版好的 UTF-8 XML（3.2.1 起）：

```text
netconf> get-config --db running --pretty --out .\cobra-running-config.xml
netconf> get --pretty --out .\cobra-all-data.xml
```

也可離線排版既有 XML，並修正 PowerShell 重導造成的 UTF-16 編碼問題：

```powershell
.\run-netconf-console2.ps1 --format-xml .\cobra-running-config1.xml `
  --pretty --out .\cobra-running-config1s.xml
```

檔案由程式直接以 UTF-8 寫入，不需使用 `>`、`iconv` 或 `xmllint`。

## `namespaces` 與 `namespaces refresh`

`namespaces` 只列出目前有效的內建、設備發現與快取 aliases，不會強制發送
RPC。`namespaces refresh` 會向目前連線設備重新查詢 YANG Library 並更新：

```text
%USERPROFILE%\.netconf-console2\namespaces.json
```

兩者都不會修改 running/startup；refresh 也不會下載全部 `.yang` 檔案。

## 安全與完整性

- 不要將實際密碼、private key 或 certificate 放入公開交付包或版本庫。
- TLS certificate、key、trusted CA、CRL 與 SSH `known_hosts` 都由部署環境提供。
- 可用 `Get-FileHash <檔案> -Algorithm SHA256` 對照 `SHA256SUMS.txt`。
- 完整命令與 YANG 修改範例請參考 `COMMAND_REFERENCE_ZH_TW.md`。
