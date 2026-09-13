# netconf-console2 Windows EXE 交付包

此資料夾是 Windows x64 單檔執行版，不包含原始碼、Python wheel、測試或建置
腳本。同事只需 Windows 10/11；不必安裝 Python、pip 或 Python 相依套件。

## 獨立 GUI（3.9.1）

3.9.1 修正新增節點的必填 YANG choice 分支：choice/case 不會偽造成 XML 標籤；
例如 `transport` 會列出可加入的 `transport → ssh`／`transport → tls` 具體節點，
即使目前選取其他 leaf 也能直接加入草稿。

3.9.0 完成第 4～7 項：list 複製／個人範本庫、三方衝突與送出結果核對、設定匯入分享、
分階段連線診斷與遮蔽報告。入口位於「工具」、進階頁與右下「送出結果核對」。
rollback-on-error 預設關閉，需 server 宣告支援；所有合併／範本仍只產生本機草稿。
詳見 GUI_GUIDE_ZH_TW.md 開頭。原本 CLI EXE 保持不變。

3.8.0 新增「草稿清單」：跨節點保留修改，Windows DPAPI 加密保存，重開後必須重新比對設備。
另有「表單編輯 leaf／引用…」修改既有欄位、提供 leafref 候選及跳至引用目標。
所有表單只更新草稿，不自動送出。詳見 GUI_GUIDE_ZH_TW.md 開頭的操作步驟與限制。

3.7.0 新增 DATA TREE「新增子節點…／新增根節點…」及可新增節點提示。
可依設備 schema 逐層建立 leaf、container、list、leaf-list，填完後只加入 XML 草稿；
新增使用 create，避免覆寫既有資料。詳見 GUI_GUIDE_ZH_TW.md。

3.6.2 的系統 SSH host key 驗證預設不勾選；「連線系統 SSH」閒置時為藍色、
成功後為綠色；登入密碼欄位加寬，跳板分頁改名為「SSH跳板」。Source / Target
初次開啟預設為 running。

3.6.1 將「NETCONF方式修改」（綠色）及「使用系統sysrepocfg修改」（紅色）
並排放到 XML 區工具列，停用時呈灰色；連線設定分頁改為粗體及藍底白字選中狀態。

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

3.5.0 新增草稿 test-only 驗證、RPC 錯誤定位、Direct SSH 的 VMware 跳板、
限時確認提交、加密設定備份／勾選差異還原、stream 回放／篩選及告警表格、停用原因。
限時提交需 server confirmed-commit 1.1；使用 persist token，斷線後仍需等 server 逾時回復。
`.nccbackup` 只能由原 Windows 帳號／電腦解密；還原只先載入草稿，不自動送出。
跳板不需要額外安裝 sshpass；TLS 與 Call Home 模式請勿勾選跳板。

3.6.0 新增獨立「系統 SSH／sysrepo」分頁，可用另組 OS 帳號登入 22 port，
經明確確認將最小 running 變更 XML 送給 sysrepocfg stdin。不會在 NETCONF 拒絕權限時自動改用 root。
遠端須有 sysrepocfg；Windows 不需安裝它。WSL Docker 情境請確認 SSH port 對應的是 O-RU 容器。
修改前比對、修改後讀回；不保存 startup，不重送。stdin 模式的 --lock 存在版本差異，詳見操作說明。

同一個「系統 SSH／sysrepo」區域另有「備份／還原」分頁：可將 running（及選用的
candidate/startup）匯出到遠端 UTC 時間目錄，保存 `modules.txt` 與 `SHA256SUMS`；
還原只套用最新且驗證通過的 `running.xml`，並在明確確認後停止／恢復相關服務。
完整操作與安全限制請看 [GUI_GUIDE_ZH_TW.md](GUI_GUIDE_ZH_TW.md)。

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
