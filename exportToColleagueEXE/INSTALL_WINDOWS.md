# netconf-console2 Windows EXE 交付包

此資料夾是 Windows x64 單檔執行版，不包含原始碼、Python wheel、測試或建置
腳本。同事只需 Windows 10/11；不必安裝 Python、pip 或 Python 相依套件。

## 獨立 GUI（3.4.0）

雙擊 `netconf-console2-gui.exe` 開啟 Windows 視窗；原本
`netconf-console2.exe` 是 CLI，仍可獨立使用。
GUI 預設讀取 running，支援 Direct SSH/TLS 與 SSH/TLS Call Home、YANG 樹狀選單、
default/state/變更標色與實際修改 RPC 預覽。
詳見 [GUI_GUIDE_ZH_TW.md](GUI_GUIDE_ZH_TW.md)。
3.3.1 新增連線／SSH 帳號清單與加密密碼保存；重新開啟會恢復上次設定。
SSH host key 與 TLS 名稱驗證預設不勾選，TLS 憑證鏈驗證仍保留。
3.3.2 新增進階頁設定匯出、連線區收合與自動重連，修正樹狀折疊及工作列圖示。
自動重連不重送修改 XML；恢復連線後需重新讀取才能送出。
3.3.3 新增兩種設定組的管理／搜尋／重新命名／批次刪除，避免關閉視窗時重新加入。
SSH 公鑰認證錯誤與私鑰解密密語的排查步驟見 GUI 操作說明。
3.3.4 將連線／開始監聽按鈕加大並改為藍底白字，停用時呈灰色。
3.3.5 將匯出按鈕改名「匯出XML」，並新增左側整棵 DATA TREE 快照匯出。
3.3.6 將 DATA TREE「匯出XML」移至「更新 YANG」右側。
3.4.0 新增明確保存 startup／commit／discard、設定比較／validate、原值新值差異、
SSH 認證方式與獨立私鑰密碼、搜尋與 YANG 欄位說明、XML 匯入草稿、操作紀錄及事件訂閱。
操作選單依 server capability 啟用；整份 datastore 操作需要額外確認，不會自動持久化。
若沿用舊版加密 SSH 私鑰，請到新分頁填寫「私鑰密碼」並儲存設定組。

```powershell
.\netconf-console2-gui.exe
# 無設備的離線示範（不連線、不修改 RU）
.\netconf-console2-gui.exe --demo
```

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

- 單元測試（包含 pretty XML 與 UTF-8 編碼回歸測試）
- EXE frozen dependency/CLI 自我檢查
- Direct SSH：真實 loopback TCP、SSH 與 NETCONF `<hello>`
- Direct TLS：真實 loopback mTLS 與 NETCONF `<hello>`
- SSH Call Home：反向 TCP、SSH 與 NETCONF `<hello>`
- TLS Call Home：反向 TCP、accepted-socket mTLS 與 NETCONF `<hello>`
- 四種連線模式查詢並保存 UTF-8 pretty XML
- 離線格式化及 PowerShell UTF-16 輸入轉換
- 清除 Python PATH 後仍可獨立啟動

Loopback 測試使用臨時帳密、CA 與憑證，不接觸正式 O-RU。正式部署仍應以
目標設備、實際憑證及網路/Firewall 設定各做一次 interoperability test。

## Pretty XML 與直接存檔

若要直接保存排版好的 UTF-8 XML（3.2.1 起）：

```text
netconf> get-config --db running --pretty --out .\cobra-running-config.xml
netconf> get --pretty --out .\cobra-all-data.xml
```

也可離線排版既有 XML，並修正 PowerShell 重導造成的 UTF-16 編碼問題：

```powershell
.\netconf-console2.exe --format-xml .\cobra-running-config1.xml `
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

## 檔案完整性與 Windows 簽章

```powershell
Get-FileHash .\netconf-console2.exe -Algorithm SHA256
Get-Content .\SHA256SUMS.txt
```

兩者雜湊必須相同。目前 EXE 未做 Authenticode 簽章；若公司政策要求，請由
公司憑證持有人簽章後再發布。不要執行來源不明或雜湊不符的檔案。

## 本資料夾應只有

- `netconf-console2.exe`
- `netconf-console2-gui.exe`
- `INSTALL_WINDOWS.md`
- `GUI_GUIDE_ZH_TW.md`
- `COMMAND_REFERENCE_ZH_TW.md`
- `LICENSE.txt`
- `THIRD_PARTY_LICENSES.txt`
- `SHA256SUMS.txt`

完整命令與 YANG 修改範例請參考 `COMMAND_REFERENCE_ZH_TW.md`。
