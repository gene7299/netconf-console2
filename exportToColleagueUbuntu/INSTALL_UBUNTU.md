# netconf-console2 Ubuntu CLI / GUI

此資料夾提供 Ubuntu 22.04 x86_64 建置的單檔 CLI `netconf-console2` 與
PySide6 GUI `netconf-console2-gui`。兩個執行檔都已包含 Python 與相應 runtime，
使用端不需要另外安裝 Python；TLS 憑證、私鑰、CA 及 SSH known_hosts 仍須由部署
環境另外提供。

## 啟動

```bash
chmod +x ./netconf-console2
./netconf-console2 --help
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

Direct SSH：

```bash
./netconf-console2 \
  --host 192.168.9.9 --port 830 --transport ssh \
  --username oranuser --password --interactive
```

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

GUI 的帳密、草稿、範本與設定備份會以本機使用者的加密金鑰保存於
`~/.netconf-console2/gui-settings.key`；金鑰檔權限會限制為使用者可讀寫，請勿刪除。
明文 JSON 匯出不含密碼；憑證與私鑰一律留在外部檔案，不會打包進執行檔。

執行檔是以 Ubuntu 22.04 x86_64 建置；請在 Ubuntu 22.04、24.04、26.04 目標機
實測後再部署。此版本針對 x86_64；Linux 執行檔不保證可在 ARM 上執行。
