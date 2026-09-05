# netconf-console2 Windows EXE 交付包

此資料夾是 Windows x64 單檔執行版，不包含原始碼、Python wheel、測試或建置
腳本。同事只需 Windows 10/11；不必安裝 Python、pip 或 Python 相依套件。

## 先做離線自我檢查

在 PowerShell 進入本資料夾後執行：

```powershell
.\netconf-console2.exe --bundle-self-test
```

預期 Direct SSH、Direct TLS、SSH Call Home、TLS Call Home 與所有 frozen
dependencies 都顯示 `[OK]`。此命令不會連接遠端設備，也不會執行遠端
SSH/TLS handshake。

## 四種連線方式

Direct SSH：

```powershell
.\netconf-console2.exe `
  --host 192.168.9.9 --port 830 --transport ssh `
  --username oranuser --password --interactive
```

Direct TLS / mTLS：

```powershell
.\netconf-console2.exe `
  --host 192.168.9.9 --port 6513 --transport tls `
  --cert .\certs\client.crt --key .\certs\client.key `
  --trusted-ca .\certs\ca.pem --tls-server-name oru.example `
  --interactive
```

SSH Call Home：

```powershell
.\netconf-console2.exe `
  --call-home --transport ssh `
  --listen-host 0.0.0.0 --listen-port 4334 `
  --username oranuser --password --interactive
```

TLS Call Home：

```powershell
.\netconf-console2.exe `
  --call-home --transport tls `
  --listen-host 0.0.0.0 --listen-port 4335 `
  --cert .\certs\client.crt --key .\certs\client.key `
  --trusted-ca .\certs\ca.pem --tls-server-name oru.example `
  --interactive
```

`--password` 不帶明文值時會顯示遮罩輸入。Call Home 需要 Windows Firewall
允許指定的 inbound TCP port，並讓 O-RU 能路由到此 Windows 主機。TLS
certificate、private key、trusted CA、CRL 與 SSH `known_hosts` 是部署資料，
不會內嵌在 EXE。

## 已完成的建置端驗證

- 44/44 單元測試
- EXE frozen dependency/CLI 自我檢查
- Direct SSH：真實 loopback TCP、SSH 與 NETCONF `<hello>`
- Direct TLS：真實 loopback mTLS 與 NETCONF `<hello>`
- SSH Call Home：反向 TCP、SSH 與 NETCONF `<hello>`
- TLS Call Home：反向 TCP、accepted-socket mTLS 與 NETCONF `<hello>`
- 清除 Python PATH 後仍可獨立啟動

Loopback 測試使用臨時帳密、CA 與憑證，不接觸正式 O-RU。正式部署仍應以
目標設備、實際憑證及網路/Firewall 設定各做一次 interoperability test。

## `namespaces` 與 `namespaces refresh`

`namespaces` 只列出目前有效的內建、設備發現與快取 aliases，不會強制發送
RPC。`namespaces refresh` 會向目前連線設備重新查詢 YANG Library 並更新：

```text
%USERPROFILE%\.netconf-console2\namespaces.json
```

兩者都不會修改 running/startup；refresh 也不會下載全部 `.yang` 檔案。

## 檔案完整性與 Windows 簽章

```powershell
Get-FileHash .\netconf-console2.exe -Algorithm SHA256
Get-Content .\SHA256SUMS.txt
```

兩者雜湊必須相同。目前 EXE 未做 Authenticode 簽章；若公司政策要求，請由
公司憑證持有人簽章後再發布。不要執行來源不明或雜湊不符的檔案。

## 本資料夾應只有

- `netconf-console2.exe`
- `INSTALL_WINDOWS.md`
- `COMMAND_REFERENCE_ZH_TW.md`
- `LICENSE.txt`
- `SHA256SUMS.txt`

完整命令與 YANG 修改範例請參考 `COMMAND_REFERENCE_ZH_TW.md`。
