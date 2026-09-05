# netconf-console2 常用命令對照清單

這份清單把常用的 `sysrepocfg`、`sysrepoctl`、`netopeer2-cli` 操作，對照成
`netconf-console2` 的命令。`netconf-console2` 的範例預設是在互動模式的
`netconf>` 提示字元下執行。

## 先釐清：YANG 檔案與 YANG 設定資料

「修改 YANG 檔」有兩種意思，兩者要分開看：

1. 修改由 YANG model 定義的遠端設定資料，例如 users、NACM、Call Home
   設定。這是 NETCONF `edit-config`／`edit-data` 的工作，
   `netconf-console2` 可以直接做。
2. 修改或安裝伺服器上的 `.yang` schema 檔案。這是 Sysrepo 本機 schema
   管理工作，通常由 `sysrepoctl` 完成，不是標準 NETCONF operation；
   `netconf-console2` 不能直接取代 `sysrepoctl -i/-U/-u`。

`get-schema` 只能從 NETCONF server 下載 schema 到本機，不能修改 server
上的 schema。若設備另外提供 vendor RPC/action 來管理 schema，才可透過
`rpc` 或 `run-rpc` 呼叫。

## 連線與工作階段

```powershell
netconf-console2 --host 192.168.9.9 --port 830 `
  --transport ssh --username oranuser --password --interactive
```

`--password` 不帶值時會以遮罩提示輸入，避免密碼出現在 PowerShell history
或 process command line。

| 目的 | `netopeer2-cli` | `netconf-console2` | 說明 |
| --- | --- | --- | --- |
| 連線 | `connect` | `connect --ssh --host HOST --port 830 --username USER --password` | 也可使用啟動時的連線參數 |
| TLS 連線 | `connect` + TLS 設定 | `connect --tls --host HOST --port 6513 --cert client.crt --key client.key --trusted-ca ca.pem` | mTLS |
| SSH Call Home | `listen --ssh ...` | `listen --ssh --host 0.0.0.0 --port 4334 --username USER --password` | 等待設備連入 |
| TLS Call Home | `listen --tls ...` | `listen --tls --host 0.0.0.0 --port 4335 --cert client.crt --key client.key --trusted-ca ca.pem` | 驗證 TLS client handshake |
| 查看 session | `status` | `status` | 顯示 peer、session ID、NETCONF version |
| 查看 server hello/capabilities | `help`／session 資訊 | `hello`、`capabilities` | `capabilities --raw` 可看原始 URI |
| 中斷連線 | `disconnect` | `disconnect` | 不關閉整個程式 |
| 離開 CLI | `quit`／`exit` | `exit`／`quit` | |

## Namespace 與 YANG schema

先執行：

```text
netconf> namespaces
netconf> namespaces refresh
```

連線時會嘗試讀取 server 的 YANG Library。模組名稱可直接當 XPath prefix，
例如 `ietf-interfaces:interfaces`。成功執行 `get-schema` 後，程式也會解析
YANG 檔的 `namespace` 與宣告的 `prefix`，並保存到：

```text
%USERPROFILE%\.netconf-console2\namespaces.json
```

| 目的 | `sysrepoctl`／`netopeer2-cli` | `netconf-console2` |
| --- | --- | --- |
| 列出已知 model | `sysrepoctl -l`、`get-schema` | `capabilities`、`namespaces` |
| 取得 YANG schema | `get-schema ietf-interfaces` | `get-schema ietf-interfaces` |
| 保存 schema 到檔案 | `get-schema ... --out FILE` | `get-schema ietf-interfaces --out .\\yang\\ietf-interfaces.yang` |
| 強制重新取得 YANG Library | 通常由 CLI/model manager 處理 | `namespaces refresh` |
| 加入手動 namespace | CLI 參數或 XML context | 啟動時 `--ns vendor=URI` |

## 查詢資料：`get`、`get-config`

### 直接對照

| 目的 | `sysrepocfg` | `netopeer2-cli` | `netconf-console2` |
| --- | --- | --- | --- |
| 讀取 running config | `sysrepocfg -X -d running -f xml` | `get-config --source running` | `get-config --db running` |
| 讀取 startup config | `sysrepocfg -X -d startup -f xml` | `get-config --source startup` | `get-config --db startup` |
| 讀取 candidate config | `sysrepocfg -X -d candidate -f xml` | `get-config --source candidate` | `get-config --db candidate` |
| 讀取 state + config | 不直接等價 | `get` | `get` |
| 以 XPath 過濾 | `-x XPATH` | `get-config --filter-xpath XPATH` | `--xpath XPATH` |
| 以 subtree/XML 過濾 | 匯出或指定 XML | `--filter XML` | `--filter XML` 或 `--filter-file FILE` |
| 顯示 defaults | `--defaults MODE` | `--with-defaults MODE` | `--with-defaults MODE` |

例如，原本的：

```bash
sysrepocfg -X -d startup -m o-ran-usermgmt -f xml \
  -x "/o-ran-usermgmt:users/user[name='oranuser2']"
```

在 NETCONF 中建議每一層都帶 prefix：

```text
netconf> get-config --db startup --xpath "/o-ran-usermgmt:users/o-ran-usermgmt:user[o-ran-usermgmt:name='oranuser2']"
```

`sysrepocfg -m MODULE` 是本機 Sysrepo 的 module 限制；NETCONF 沒有同名的
`--module` 選項，必須用完整 XPath 或 subtree filter 選取 module 的 root
node。

## 修改遠端 YANG 設定資料

以下都是會修改遠端 datastore 的命令。修改前建議先 `lock`，完成後
`commit`，再視需要將 running 複製到 startup。

### 匯入／套用 XML

| 目的 | `sysrepocfg` | `netopeer2-cli` | `netconf-console2` |
| --- | --- | --- | --- |
| 從 XML merge 到 running | `sysrepocfg -I config.xml -d running -f xml` | `edit-config --target running --config config.xml` | `edit-config .\\config.xml --db running --default-operation merge` |
| 套用到 candidate | `sysrepocfg -I config.xml -d candidate -f xml` | `edit-config --target candidate --config config.xml` | `edit-config .\\config.xml --db candidate` |
| 從 stdin 匯入 | `sysrepocfg -I -d running` | `edit-config --target running --config -` | `edit-config - --db running` |
| 多個 XML 一次套用 | `sysrepocfg -I ...` | 依 CLI 版本而定 | `edit-config .\\a.xml .\\b.xml --db candidate` |
| 使用 legacy root-stripping 行為 | 無 | 依 CLI 版本而定 | `edit-config1 .\\config.xml --db running` |

完整 RFC 6241 edit-config 選項：

```text
netconf> edit-config .\config.xml --db candidate `
  --default-operation merge `
  --test-option test-then-set `
  --error-option rollback-on-error
```

Windows PowerShell 的 stdin 方式：

```powershell
Get-Content .\config.xml | netconf-console2 `
  --host 192.168.9.9 --port 830 --transport ssh `
  --username oranuser --password --db candidate --edit-config -
```

XML 中若要精確控制單一節點，也可使用標準 `nc:operation`：

```xml
<config xmlns="urn:ietf:params:xml:ns:netconf:base:1.0"
        xmlns:nc="urn:ietf:params:xml:ns:netconf:base:1.0">
  <interface xmlns="urn:ietf:params:xml:ns:yang:ietf-interfaces"
             nc:operation="replace">
    <name>eth0</name>
    <enabled>true</enabled>
  </interface>
</config>
```

### `set`、`delete`、`create` 便利命令

這三個是 `netconf-console2` 提供的簡短 path 操作；它們最後仍會送出
NETCONF `edit-config`。

```text
netconf> set /if:interfaces/if:interface[if:name='eth0']/if:enabled=false --db candidate
netconf> set /if:interfaces/if:interface[if:name='eth0']/if:enabled=true --db candidate --operation replace
netconf> delete /if:interfaces/if:interface[if:name='eth0'] --db candidate
netconf> create /if:interfaces/if:interface[if:name='eth1'] --db candidate
```

| 目的 | `sysrepocfg` | `netconf-console2` |
| --- | --- | --- |
| 修改一個 leaf | `-I`／`-E` 搭配 XML | `set PATH=VALUE` |
| 刪除 node | `-I`／`-E` 搭配 `nc:operation="delete"` | `delete PATH` |
| 建立 list/container | `-I`／`-E` 搭配 `nc:operation="create"` | `create PATH` 或 `edit-config FILE` |
| merge/replace/create | XML `nc:operation` | `set --operation merge\|replace\|create` |
| delete/remove 行為 | XML `nc:operation` | `delete --del-operation remove\|delete` |

`set` 適合簡單 leaf。涉及 password、key、list 多個 leaf 或複雜 choice/case
時，建議使用 XML 檔搭配 `edit-config`，避免 shell history 及 escaping 問題。

### Datastore lifecycle

| 目的 | `sysrepocfg`／`sysrepoctl` | `netopeer2-cli` | `netconf-console2` |
| --- | --- | --- | --- |
| lock candidate | `-l` 僅是本機 edit lock | `lock candidate` | `lock --db candidate` |
| unlock candidate | 本機 operation 結束後釋放 | `unlock candidate` | `unlock --db candidate` |
| commit candidate | `sysrepocfg -I ... -d candidate` 後由應用程式處理 | `commit` | `commit` |
| confirmed commit | 需應用程式/API | `commit confirmed ...` | `commit confirmed --confirm-timeout 60` |
| persist token | 需 API | 依版本支援 | `commit confirmed --persist token` |
| persist-id | 需 API | 依版本支援 | `commit --persist-id token` 或 `cancel-commit --persist-id token` |
| 取消 confirmed commit | 需 API | `cancel-commit` | `cancel-commit [--persist-id token]` |
| 丟棄 candidate changes | `sysrepocfg` 本機另行處理 | `discard-changes` | `discard-changes` |
| running → startup | `sysrepocfg -C running -d startup` | `copy-config` | `copy-running-to-startup` 或 `copy-config running --db startup` |
| 清除 startup | `sysrepocfg -d startup ...` 依操作處理 | `delete-config startup` | `delete-config --db startup` |
| validate candidate | `sysrepocfg`／Sysrepo validation | `validate candidate` | `validate candidate` |

常見安全流程：

```text
netconf> lock --db candidate
netconf> edit-config .\oru-change.xml --db candidate --test-option test-then-set --error-option rollback-on-error
netconf> validate candidate
netconf> commit
netconf> copy-running-to-startup
netconf> unlock --db candidate
```

如果只是要驗證 XML，不送出 edit：

```text
netconf> validate .\oru-change.xml
```

## `copy-config`、`delete-config`

```text
netconf> copy-config running --db startup
netconf> copy-config startup --db running
netconf> copy-config .\baseline.xml --db candidate
netconf> copy-config --source running --db startup
netconf> delete-config --db startup
```

`copy-config` 的第一個位置參數可以是 `running`、`startup`、`candidate`，
也可以是 XML 檔案。`--db` 是目的 datastore。

## NMDA：`get-data`、`edit-data`

| 目的 | `netopeer2-cli` | `netconf-console2` |
| --- | --- | --- |
| 讀 operational | `get-data --datastore operational` | `get-data --datastore operational` |
| 限制 depth | `--depth N` | `--depth N` |
| origin filter | `--origin ORIGIN` | `--origin ORIGIN` |
| 顯示 origin metadata | `--with-origin` | `--with-origin` |
| 修改 NMDA datastore | `edit-data ...` | `edit-data .\\config.xml --datastore running --default-operation merge` |

例如：

```text
netconf> get-data --datastore operational --xpath "/if:interfaces/if:interface" --depth 2 --with-origin
netconf> edit-data .\config.xml --datastore running --default-operation merge
```

`operational` 通常是唯讀資料；能否用 `edit-data` 修改某個 datastore 仍取決於
設備 capability 與 YANG model。

## Generic RPC、YANG RPC 與 action

| 目的 | `sysrepocfg` | `netopeer2-cli` | `netconf-console2` |
| --- | --- | --- | --- |
| XML RPC 檔案 | `sysrepocfg -R request.xml -f xml` | `user-rpc request.xml` | `rpc .\\request.xml` 或 `rpc --content .\\request.xml` |
| stdin RPC | `-R` | `user-rpc -` | `rpc -` |
| YANG RPC path | `-R` XML | `user-rpc`／CLI 專用命令 | `run-rpc /module:rpc` |
| YANG 1.1 action | `-R` XML | `user-rpc` | `action /module:container/action` |
| 保留完整 message-id envelope | XML RPC | `user-rpc` | `rpc --full .\\request.xml` |

範例：

```text
netconf> rpc .\get-something.xml
netconf> run-rpc /o-ran-alarms:acknowledge-alarm
netconf> action /o-ran-software-management:software-inventory/download
```

如果 server 沒有原生 CLI wrapper，使用 `rpc` 是最通用的方式。回應中的
`rpc-error`、`error-type`、`error-tag`、`error-severity`、`error-path`、
`error-message` 與 `error-info` 會保留。

## Notifications

```text
netconf> subscribe --stream NETCONF
netconf> watch
```

若要 replay：

```text
netconf> subscribe --stream NETCONF --start-time 2026-01-01T00:00:00Z --stop-time 2026-01-01T01:00:00Z
```

| 目的 | `sysrepocfg` | `netopeer2-cli` | `netconf-console2` |
| --- | --- | --- | --- |
| 發送 notification | `sysrepocfg -N notification.xml` | 通常不是 client 工作流程 | 沒有標準 client 等價命令；使用 server-side Sysrepo 或 vendor RPC |
| 建立 subscription | 不適用 | `subscribe` | `subscribe`／`create-subscription` |
| 接收 notification | 不適用 | CLI notification output | `watch`／`notifications` |

`establish-subscription`、`modify-subscription`、`delete-subscription`、
`kill-subscription` 以及 YANG Push 的 `establish-push`、`modify-push`、
`resync-subscription`，目前 `netconf-console2` 尚未提供專用 command wrapper。
可先使用 `rpc .\\request.xml` 傳送對應的 XML RPC；這些功能不應用
`subscribe`／`watch` 取代。

## Capabilities、trace 與輸出

```text
netconf> capabilities
netconf> capabilities --raw
netconf> outputformat pretty
netconf> outputformat raw
```

啟動時也可以使用：

```powershell
netconf-console2 ... --output pretty --trace --trace-file .\logs\session.log
```

trace 會遮罩 password、secret leaf 與 credential XML；不要把 private key
檔案放進 `rpc` 或 debug log。

## 真正修改 `.yang` schema 檔案：沒有直接 NETCONF 對照

這些命令修改的是 Sysrepo server 本機 schema repository，而不是 running 或
startup datastore：

| 本機 schema 工作 | `sysrepoctl` | `netconf-console2` 對照 | 備註 |
| --- | --- | --- | --- |
| 列出 modules | `sysrepoctl -l` | `capabilities`／`namespaces`／`get-schema` | 不是完整本機安裝清單的嚴格替代 |
| 安裝 `.yang`／`.yin` | `sysrepoctl -i module.yang` | 無直接等價命令 | 必須在 server 主機執行，或使用 vendor RPC |
| 立即套用安裝 | `sysrepoctl -i module.yang -a` | 無直接等價命令 | `-a/--apply` 是 Sysrepo 本機行為 |
| 更新 schema | `sysrepoctl -U module.yang` | 無直接等價命令 | 可能影響資料相容性 |
| 移除 module | `sysrepoctl -u module-name` | 無直接等價命令 | 不應由一般 NETCONF client 假設可做 |
| 啟用 feature | `sysrepoctl -i ... -e FEATURE` | 無標準等價命令 | feature 通常在 schema install/change 時設定 |
| 停用 feature | `sysrepoctl -c MODULE -d FEATURE -a` | 無標準等價命令 | |
| 變更 replay/權限 | `sysrepoctl -c MODULE ...` | 無直接等價命令 | Sysrepo filesystem/repository 管理 |
| 新 module 初始資料 | `sysrepocfg -W initial.xml -m MODULE` | 先安裝 schema，再 `edit-config` | `-W` 是 Sysrepo 安裝排程前的本機功能 |
| 下載 module schema | 不適用 | `get-schema MODULE --out FILE` | 只保存到 client 本機 |

## O-RAN 常用驗證範例

確認 `oranuser2` 是否存在於 startup：

```text
netconf> get-config --db startup --xpath "/o-ran-usermgmt:users/o-ran-usermgmt:user[o-ran-usermgmt:name='oranuser2']"
```

確認 NACM group mapping：

```text
netconf> get-config --db running --xpath "/ietf-netconf-acm:nacm/ietf-netconf-acm:groups/ietf-netconf-acm:group[ietf-netconf-acm:user-name='oranuser2']"
```

確認 Call Home SSH user authentication list：

```text
netconf> get-config --db running --xpath "/ietf-netconf-server:netconf-server/ietf-netconf-server:call-home//ietf-ssh-server:user[ietf-ssh-server:name='oranuser2']"
```

若設備不接受 `//`，使用完整的 `ietf-netconf-server@2019-07-02` 結構：

```text
netconf> get-config --db running --xpath "/ietf-netconf-server:netconf-server/ietf-netconf-server:call-home/ietf-netconf-server:netconf-client/ietf-netconf-server:endpoints/ietf-netconf-server:endpoint/ietf-netconf-server:ssh/ietf-netconf-server:ssh-server-parameters/ietf-ssh-server:client-authentication/ietf-ssh-server:users/ietf-ssh-server:user[ietf-ssh-server:name='oranuser2']"
```

空的 `<data/>` 可能代表資料不存在，也可能是目前登入帳號受 NACM 限制而
看不到資料；需要用具備讀取權限的管理帳號確認。`startup` 查詢也需要 server
公布 `:startup` capability。

## 快速轉換規則

```text
sysrepocfg -X -d DB -x XPATH
    -> get-config --db DB --xpath "XPATH"

sysrepocfg -I FILE -d DB -f xml
    -> edit-config FILE --db DB

sysrepocfg -C running -d startup
    -> copy-config running --db startup

sysrepocfg -R FILE -f xml
    -> rpc FILE
```

請記住：`sysrepocfg -m MODULE` 是本機 Sysrepo module scope；轉成 NETCONF
時，應使用完整 namespace-qualified XPath 或 XML subtree filter。
