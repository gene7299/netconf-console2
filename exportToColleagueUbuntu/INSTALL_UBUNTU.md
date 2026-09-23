# netconf-console2 Ubuntu CLI / GUI

此資料夾提供 Ubuntu 22.04 x86_64 建置的單檔 CLI `netconf-console2` 與
PySide6 GUI `netconf-console2-gui`。兩個執行檔都已包含 Python 與相應 runtime，
使用端不需要另外安裝 Python；TLS 憑證、私鑰、CA 及 SSH known_hosts 仍須由部署
環境另外提供。

CLI bundle 是 headless 建置，不包含 PySide6 或 tkinter。TLS Call Home 使用隨
執行檔封裝的 GnuTLS，以送出 RFC 8071 C4 規定的 Heartbeat
`peer_allowed_to_send`；Direct TLS 預設使用 OpenSSL。

## 啟動

```bash
chmod +x ./netconf-console2
./netconf-console2 --help
./netconf-console2 --backend-info
./netconf-console2 --interactive
```

GUI：請在 Ubuntu 桌面工作階段執行：

```bash
chmod +x ./netconf-console2-gui
./netconf-console2-gui
```

離線示範模式（不連線設備）：

```bash
./netconf-console2-gui --demo
```

### GUI：sysrepocfg DATA TREE

左側樹狀視窗底部提供藍色 **NETCONF** 與綠色 **sysrepocfg** 頁籤。
在上方「系統SSH」先連線設備，再選擇 datastore 並按「Sysrepocfg讀取」。
支援 running、candidate、startup、operational；sysrepocfg tab 只切換左側 DATA TREE，
右側仍維持 NETCONF XML 編輯器與原有功能，不會覆蓋 NETCONF 草稿；共用的「匯出XML」
會依目前選取的 tab 匯出 NETCONF 或 sysrepocfg DATA TREE。
設備上需已安裝 `sysrepocfg`，且系統 SSH 帳號具有 sysrepo 讀取權限。

- 紅字：同設備、對應 datastore 的 NETCONF 新快照中未出現的節點；不是權限拒絕的證明。
- 灰字：尚未比較、NETCONF 讀取失敗或 schema/list key 不足，無法判定。
- 位址／跳板不同或 NETCONF 未連線時仍可讀取 sysrepocfg，但不自動比較。
- sysrepocfg 資料來源是唯讀；共用選取、YANG 說明、右鍵新增／刪除預覽、Pretty、還原、
  重新讀取、XML 匯出與搜尋功能仍可使用。新增／刪除只建立本機 XML 預覽，不會寫入設備。

比較結果屬於當次快照；設備資料或權限改變後，請重新讀取。

Direct SSH：

```bash
./netconf-console2 \
  --host 192.168.9.9 --port 830 --transport ssh \
  --username oranuser --password --interactive
```

自動測試請使用 versioned JSON API，避免解析互動 prompt 或錯誤文字：

```bash
read -rsp 'NETCONF password: ' NETCONF_PASSWORD
export NETCONF_PASSWORD
./netconf-console2 --test-api --transport ssh \
  --host 192.168.9.9 --port 830 --bind 192.168.9.252 \
  --username oranuser --password-env NETCONF_PASSWORD \
  --no-agent --no-look-for-keys --no-hostkey-verify \
  --result-file ./machine-result.json --events-file ./events.jsonl
unset NETCONF_PASSWORD
```

`machine-result.json` 包含穩定 classification code、失敗 phase 與 exception
chain；`events.jsonl` 是可追蹤的 transport/handshake phase。密碼值不寫入兩個檔案。

Direct TLS / mTLS（憑證路徑請依部署位置調整）：

```bash
./netconf-console2 \
  --host 192.168.9.9 --port 6513 --transport tls \
  --cert ./certs/netconf-client-chain.pem \
  --key ./certs/netconf-client.key.pem \
  --trusted-ca ./certs/ca-chain.pem \
  --tls-server-name 192.168.9.9 --capabilities
```

Call Home listener：

```bash
./netconf-console2 \
  --call-home --transport ssh \
  --listen-host 0.0.0.0 --listen-port 4334 \
  --username oranuser --password --interactive
```

TLS Call Home 須使用 `--tls-backend gnutls`；`--backend-info` 必須回報
`rfc8071_peer_allowed_to_send: true`。這個欄位表示 backend 設定，正式能力證據仍應
從 ClientHello PCAP 確認 extension type 15 的值為 `01`。

GUI 的帳密、草稿、範本與設定備份會以本機使用者的加密金鑰保存於
`~/.netconf-console2/gui-settings.key`；金鑰檔權限會限制為使用者可讀寫，請勿刪除。
明文 JSON 匯出不含密碼；憑證與私鑰一律留在外部檔案，不會打包進執行檔。

執行檔是以 Ubuntu 22.04 x86_64 建置；請在 Ubuntu 22.04、24.04、26.04 目標機
實測後再部署。此版本針對 x86_64；Linux 執行檔不保證可在 ARM 上執行。
