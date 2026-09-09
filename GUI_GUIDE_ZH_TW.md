# NETCONF Windows GUI 操作說明（3.4.0）

GUI 是獨立的 `netconf-console2-gui.exe`，不取代原本的 CLI
`netconf-console2.exe`。Windows x64 EXE 已包含 Python、Tk/ttk、ncclient、
SSH/TLS 與 pyang，相同資料夾內不需要原始碼，也不需另裝 Python。

## 啟動

直接雙擊 `netconf-console2-gui.exe`。也可以在 PowerShell 執行：

```powershell
.\netconf-console2-gui.exe
```

沒有設備時可以先看離線示範；示範資料是內建的合成資料，所有修改只在記憶體，
不會建立網路連線，也不會修改 Cobra：

```powershell
.\netconf-console2-gui.exe --demo
```

## 上排：連線設定

「連線 / 開始監聽」為加大藍底白字按鈕；忙碌或已連線時停用並顯示灰色。

| 模式 | 主要欄位 | 預設 port | 認證 |
|---|---|---|---|
| Direct SSH | Server host、Port | 830 | SSH 帳號、密碼或 private key |
| Direct TLS | Server host、Port | 6513 | Client cert、private key、Trusted CA |
| SSH Call Home | Listen host、Listen port | 4334 | SSH 帳號、密碼或 private key |
| TLS Call Home | Listen host、Listen port | 4335 | Client cert、private key、Trusted CA |

選模式、填欄位，按「連線 / 開始監聽」。Call Home 要填本機可監聽的位址，
例如 `0.0.0.0`；不是 RU 位址。RU 的 Call Home 目的地必須設定為本機可達的
IP/port，Windows 防火牆也必須允許該 port；GUI 不會自動更改防火牆或 RU 設定。
若環境使用 TLS port `4336`，請自行將 Listen port 改為 `4336`。

- `Source / Target` 預設 **running**。candidate/startup 僅在伺服器支援時讀取。
- SSH「驗證 SSH host key」預設不勾選；未勾選時直接連線，不再跳出警告。
  若要驗證設備身分，可勾選並提供已核對的 `known_hosts`。
- TLS「驗證名稱」預設不勾選，**憑證鏈／CA 驗證仍保留**。
  勾選後 `Server name / SAN` 用於驗證 RU 的憑證名稱；例如來源是 NAT IP、
  但 SAN 是 `cobra-ru`，此欄填 `cobra-ru`。
- 以上是首次啟動的預設值；自行勾選後，下次會恢復勾選狀態。
  關閉身分／名稱驗證會減少對冒充設備的防護，請在可信任的測試網路使用。
- TLS/SSH 私鑰可各自選擇；密碼與所有設定以 Windows DPAPI 加密保存在本機。
- 等待秒數控制連線／Call Home 等待；Call Home 可填 `0`，表示等待到使用者取消。
  RPC timeout 必須大於零。
- 「中斷 / 取消等待」會取消 Call Home 等待；正在握手或執行的 RPC 可能需要等到
  timeout 才結束。連線與 YANG 編譯在背景執行，不阻塞視窗。

### 收合連線區與自動重連

上下排之間按「▲ 隱藏連線設定」可騰出 XML 顯示空間，再按「▼ 顯示連線設定」
恢復；收合狀態也會保存。旁邊的「斷線後自動重連」預設不勾選，勾選狀態會保存。

- 首次連線需手動操作。成功連線後若 transport 回報斷線，依序等待 2、4、8、16、
  30 秒重試，之後每次最多等待 30 秒（另加每次連線／握手 timeout）。
- Direct 模式重新連往原端點；Call Home 模式重新監聽原位址／port，等待 RU
  再次呼入，不會主動要求 RU 呼入。使用此次手動連線時的認證與 TLS/SSH 驗證選項。
- 「停止重連」、取消勾選、手動中斷或關閉視窗會停止重試；可手動連線重新啟用。
  已在等待的 Call Home 會取消，握手可能仍要等 timeout。重試錯誤顯示於狀態列，
  不會每次跳出警告。
- **不會自動重送 edit-config、commit 或 copy-config**。斷線時保留未送出的 XML；
  重連完成後，必須先重新讀取確認設備狀態，才可送出修改。請先匯出要保留的草稿，
  因為重新讀取會詢問是否放棄草稿。只更新 YANG 不算重新讀取設備資料。
- 偵測依據是 SSH/TLS transport 的連線狀態；未加入額外心跳 RPC。網路無聲丟包
  可能要到 transport 偵測到斷線才重試，不保證立即偵測所有網路故障。

## 多組連線、SSH 帳號與下次還原

頂部改為「連線設定組」清單，不再顯示 NETCONF / XML WORKSPACE 橫幅。

- 填完設定後按「連線 / 開始監聽」，即會加入使用過的連線與 SSH 帳號清單，
  包含連線失敗的嘗試。已選取／輸入既有名稱時，更新該組快照，不另建 `(2)`。
  關閉視窗只保存目前欄位，不再自動新增清單項目。
- 可在「連線設定組」輸入名稱，按「儲存連線設定」。同名儲存會更新該組快照；
  「另存新組」沿用目前欄位，清空名稱，方便建立另一組。選取清單項目會還原
  該組的模式、位址、連接埠、認證、TLS 路徑、SAN、驗證選項、逾時與讀取選項。
- SSH 認證頁另有「SSH 帳號組」清單。「新增帳號」清空帳號、密碼及 SSH key 路徑；
  「儲存帳號」保存這組帳號、密碼、key 路徑及 agent／搜尋金鑰選項。
  要保留同帳號不同認證，請按「新增帳號」後填入，或輸入另一個設定組名稱。
  未指定名稱的新帳號可自動用 `(2)` 等名稱區別；既有名稱則更新原紀錄。
  選取 SSH 帳號不會改變目標 host、port、TLS 路徑或驗證勾選。
- 一般欄位變更約 0.6 秒後自動保存為「上次畫面設定」，包括尚未輸入完整的欄位。
  下次開啟會恢復相同值、密碼及選擇的清單項目，但**不會自動連線**。
  連線中或忙碌時不能切換設定組／帳號組，請先中斷連線。
- `--demo` 和 `--self-test` 不讀取、不覆寫個人設定；測試只使用隔離的合成帳密。

### 管理、重新命名與批次刪除（3.3.3）

連線設定組及 SSH 帳號組右側各有「管理…」。請先中斷連線並停止重連，才能管理。

- 可搜尋名稱，使用 Ctrl / Shift 多選。「載入並編輯」載入一組至主畫面，修改後
  按對應的儲存按鈕，不會立即連線。
- 「重新命名」保留設定與順序，同步更新目前選擇；若名稱已存在，拒絕覆寫。
- 「刪除選取項目…」確認後批次刪除並立即加密保存。刪除只影響**本機清單**，
  不會刪除 RU 上的使用者、憑證、私鑰或其他檔案。
- 連線組與帳號組是獨立快照，刪除帳號組不會清除其他連線組內保存的同一帳密。
  目前畫面欄位／上次欄位還原也會保留；這不是完整清除認證的功能。
- 關閉或重開 GUI 不會把已刪除的清單項目加回。再次按連線或儲存，才可能重新加入。
  刪除無復原按鈕，建議先在進階頁匯出加密備份；寫入失敗時原清單保持不變。
- 3.3.3 不再因 source、default/state、折行、收合或自動重連選項變更而新增匿名
  連線歷史。舊版已累積的項目不會擅自合併或刪除，可自行批次整理。

設定檔位於 `%USERPROFILE%\.netconf-console2\gui-settings.dpapi`，不放在 EXE
目錄、原始碼包或 Git repository。整份設定（包含密碼）只以密文落地，寫入採
原子替換；不會在加密失敗時改存明文。無法讀取／解密時保留原檔並顯示錯誤。
多個視窗同時使用時，以最後儲存的視窗為準。

DPAPI 通常限定同一 Windows 使用者與電腦解密；請勿把設定檔當成可攜式密碼庫
交給同事。它也不能防範已取得相同 Windows 使用者權限的程式。
參考 [Microsoft DPAPI 說明](https://learn.microsoft.com/en-us/windows/win32/api/dpapi/nf-dpapi-cryptprotectdata)。
私鑰與憑證本體不會複製進設定檔，只保存所選路徑；搬動檔案後需重新指定。
這項密碼保存功能只適用 GUI；既有 CLI 的歷史／TOML profile 規則不變。

### SSH 錯誤：BadAuthenticationType / ['publickey']

這表示伺服器在當次 SSH 認證階段回報只允許 `publickey`，而用戶端最後嘗試的
方式不被允許；不等同密碼打錯，也不是 SSH host key 驗證錯誤。
見 [Paramiko 認證例外說明](https://docs.paramiko.org/en/stable/api/ssh_exception.html)。

GUI 3.4.0 將登入密碼與私鑰密碼分開，沒有放寬伺服器安全策略。
「SSH 認證方式 / 私鑰密碼」分頁選擇 `auto`、`password`、`private-key` 或 `agent`。
只有 auto 會採用 SSH 認證頁的 Agent／搜尋勾選；其他模式只嘗試指定方法。
已在合成的本機 Direct SSH 和 SSH Call Home 公鑰限定伺服器驗證：

| GUI 認證欄位 | 結果／處理方式 |
|---|---|
| 只有帳號與登入密碼 | 公鑰限定伺服器不接受；需選擇已授權的 SSH 私鑰 |
| 正確且未加密的 SSH 私鑰，密碼空白 | 可公鑰認證 |
| 未加密私鑰，但登入密碼仍有值 | GUI auto／private-key 不會拿登入密碼解密私鑰；私鑰密碼留白即可 |
| 正確且加密的 SSH 私鑰 | 在「私鑰密碼（passphrase）」填解密密語；登入密碼可與它不同 |
| password 模式且有選私鑰 | 只嘗試登入密碼，不會默默改用公鑰 |

GUI auto 依序嘗試指定私鑰、本機金鑰、SSH Agent、登入密碼。錯誤會說明失敗階段，
不輸出密碼內容。舊設定的私鑰密碼不會自動猜測搬移：加密私鑰使用者需填新欄位並儲存。
CLI 保留原本 ncclient 相容流程，因此舊 CLI 私鑰／password 行為不變。
切換連線組會恢復該組完整認證快照，所以請核對目前帳號、Private key 與兩種密碼。
亦應確認 host／port／Call Home 呼入設備
是否一致；若預期伺服器接受密碼，需查設備該帳號的 SSH／NETCONF 認證政策及日誌。
僅憑這則錯誤無法確定現場是哪個原因，也不應據此關閉 host key 驗證。

### 進階頁：匯出各種 GUI 設定

「進階 / YANG schema」頁提供兩個按鈕，皆包含目前尚未儲存的欄位、既存連線
設定組及 SSH 帳號組；不會讀取或匯出 NETCONF server 的 running XML。

- **匯出設定 JSON（不含密碼）**：UTF-8 可讀檔，所有 password、key_passphrase 欄位均移除。
  仍含帳號、主機、IP、設定組名稱及本機路徑，分享前請自行檢查；這是參考設定，
  不是 CLI TOML，也不支援直接匯入 GUI。
- **匯出加密備份（含密碼）**：完整 DPAPI 設定備份，只適合原 Windows 使用者／
  電腦還原，不能當作跨電腦密碼移轉檔。還原時先關閉所有 GUI 視窗，備份原本的
  `%USERPROFILE%\.netconf-console2\gui-settings.dpapi`，再把匯出檔複製到該路徑並
  命名為 `gui-settings.dpapi`，重新開啟 GUI。

兩種匯出都只記錄憑證／私鑰／schema 資料夾的路徑，不夾帶其內容。

### Windows 圖示

3.3.2 統一 EXE、主視窗與子視窗圖示，並設定獨立 Windows AppUserModelID，
原始碼及 wheel 安裝版也包含同一圖示。如果工作列先前固定的是舊 Python／Tk
捷徑，請關閉舊版後啟動新版；必要時取消舊捷徑固定，再固定新版 EXE。

## 左側：資料樹

連線後先讀取伺服器目前允許此帳號查看的 running 資料，再下載／編譯 YANG。
根列表顯示實際資料的最上層 **container、list 或 leaf**，不把所有根節點誤稱為 leaf。
`+` 可展開下一層，`-` 只折疊該節點，不觸發讀取或改變 XML 選取。
重新讀取也保留展開／折疊狀態，不會因為選到子節點而重新打開已折疊的祖先。
list instance 顯示 key，例如 `interface [name=eth0]`。
點名稱會先顯示快照，再用 subtree filter 重新讀取所屬根節點；不要求 XPath capability。
「重新讀取全部」重建目前來源的資料樹。NACM 隱藏的資料不會被繞過或補造。

- **包含 YANG default 值**：優先要求 `report-all-tagged`，其次 `report-all`。
  若伺服器不支援，顯示原生回覆並提出提醒，不自行補出可能不符合 when/choice/
  if-feature 條件的預設節點。未勾選時使用伺服器原生的 default handling；
  明確設定成預設值的資料仍可能出現。
- **包含 config false**：以 `<get>` 取得 running configuration 加 state。
  此選項只適用 running；不會把 state 寫入 running/candidate/startup。
- **更新 YANG**：重新抓取 schema。一般重連可重用有 revision/content-id 的 cache；
  cache 位於使用者的 `.netconf-console2/gui-schemas`，不在交付包或 repository。
- 若設備無法提供某些 schema，可在「進階 / YANG schema」選本機 `.yang` 資料夾作為備援。
  必須包含正確 revision，以及 import/include 的相依 modules/submodules。
  YANG 缺失或編譯錯誤時仍可瀏覽 XML，但停用送出，避免誤判 config 或 list key。
- 「連線 / Schema 詳情」可查看 session、capabilities 及完整編譯提醒。

## 右上：讀取與編輯 XML

| 顏色 | 意義 |
|---|---|
| 黃色 | 伺服器以 `wd:default=true/1` 明確標記的 default |
| 淡黃色 | 目前值等於 schema 的 default；不代表它一定是隱含值 |
| 藍灰色 | schema 判定的 `config false`，不可寫入 |
| 橘色 | 目前修改／新增的值 |
| 淡紫色 | 未取得對應 schema 的節點，不能推定可寫入 |

config false 與修改標色優先於 default；繼承自父節點的 config false 也會辨識。
default 標記同時支援 RFC 6243 與 libyang/sysrepo 的 metadata namespace。
pyang 會處理 augment、uses、choice/case、submodule、feature 與 deviation。
已停用 feature 下的節點不會顯示成可寫 schema；若 pyang 在展開 grouping 時
對這些已排除節點提出 leafref 警告，會保留提醒，但不封鎖其他有效節點。
若 leafref 錯誤影響仍啟用的節點，則仍停用寫入。

直接在 XML 區修改 leaf 值，約 0.2 秒後更新預覽。可修改已知的可寫 leaf、
新增已知的設定節點、刪除設定節點；刪除會轉成明確的 `nc:operation="remove"`。
選到 list key 時不能直接改名；也不能手動插入 `nc:operation`、DTD/entities 或
任意 XML attributes。keyless list、opaque anyxml/anydata、ordered-by user
的重新排序不在此版編輯範圍內。leaf-list 的值請從父節點編輯，才能同時產生
移除舊值／新增新值的 RPC。完整的 type、must、when、leafref 等資料值驗證
仍由 NETCONF server 執行，GUI 不是完整離線 YANG instance validator。

「重新讀取」向伺服器抓取所選根節點；「還原」回到這次讀取的 XML；
「Pretty」重新排版；「自動折行」只改變畫面，不修改 XML；
右側「匯出XML」保存目前編輯區內容，包含尚未送出的修改；檔案仍使用 UTF-8。

左側 DATA TREE 的「更新 YANG」右側有「匯出XML」，匯出目前已讀取的**整棵資料樹快照**，
包含折疊／尚未展開的節點，以 NETCONF `<data>` 包住所有根節點並排版成 UTF-8 XML。
內容依實際讀取時的 source、default、config false 選項與帳號權限，不包含右側
尚未送出的編輯。不會額外連線或送出 RPC；如需最新完整資料，先按「重新讀取全部」。
未讀取資料或忙碌時不能匯出；意外斷線但仍保有快照時可以匯出，並不代表設備即時狀態。
匯出不是 edit-config RPC，也不會修改設備。XML 可能包含敏感設定，請妥善保管檔案。
切換節點／來源／重新讀取時若有尚未送出的修改，會先詢問是否放棄。

## 右下：實際送出的 XML 與送出

預覽是完整 `<rpc message-id="…"><edit-config>…`，包含指定 target。
送出的 XML 與預覽一致（NETCONF 1.0 delimiter／1.1 chunk framing 不屬於 XML）。
只送出變更節點與定位所需的 ancestor/list key；不整包回送 state 或未變更的 defaults，
也會移除 `wd:default`／origin 等讀取標記。

按「送出修改…」後會列出變更與移除筆數，確認後才執行：

1. 鎖定目標 datastore；拿不到 lock 就停止，不強制解鎖別人的 session。
2. 重新讀取設定並檢查所選內容是否已被其他人修改；若不同則停止，要求重新讀取。
3. 送出預覽中的 edit-config，以 `default-operation=none` 避免意外建立祖先節點。
4. 解鎖並重新讀取；RPC 回應顯示在「最後 RPC 回應」。

lock、檢查用 get-config 和 unlock 是額外的保護 RPC；右下主要預覽的是修改 RPC。
失敗／timeout 不會自動重試；結果不明時，必須先重新讀取確認，才能再送出。
NETCONF 錯誤可能在不支援 rollback 的設備上留下部分套用結果，因此不能以
「回覆錯誤」推定伺服器完全沒變更。

**running 修改不會自動保存 startup；candidate 修改不會自動 commit。**
startup 的 XML 編輯仍唯讀，但可透過下列明確保存操作更新；不會隱含做持久化。

## 3.4.0 設定操作、搜尋、匯入與紀錄

### 設定操作選單

最上方「設定操作」提供：

| 操作 | 效果／capability |
|---|---|
| 保存 running → startup | copy-config 整份設定；需要 `:startup:` |
| 比較 running / startup | 唯讀讀取兩份設定，顯示差異；需要 `:startup:` |
| 提交 candidate → running | commit 整份 candidate；需要 `:candidate:`；不會保存 startup |
| 捨棄 candidate | discard-changes，整份 candidate 回到 running；需要 `:candidate:` |
| 驗證 datastore | validate 目前讀取的 source；需要 `:validate:`；不是驗證尚未送出的草稿 |

不支援、未連線、忙碌、有未送出草稿或結果待確認時，相關選項會停用。
先送出／還原草稿並重新讀取，再操作。比較／預覽不會修改設備。
按確認才鎖定相關 datastore、重新比對預覽快照、送出所示 RPC、讀回及解鎖。
快照已改變或 lock 失敗就停止，不強制解鎖他人，不自動重送。
RPC 成功但讀回／解鎖失敗會標成待確認；請重新連線／讀取，不要重送。

**這些是整份 datastore 操作，不是只保存目前 leaf。** candidate 也可能包含同事的
未提交內容，discard 會一併捨棄。NACM 可能隱藏部分資料；預覽並非不可見資料的保證。
文字差異也可能來自 namespace prefix、順序或 default 表示法，不是 schema 等價證明。
GUI 目前不提供 confirmed-commit 倒數回復；修改連線相關設定前請準備帶外恢復方式。
協定語意參考 [RFC 6241](https://www.rfc-editor.org/rfc/rfc6241.html)。

### 修改差異與搜尋

右下「修改差異」即時列出 target、含 module／list key 的 instance 路徑、原值、新值，
並附 XML diff（含新增、移除）。這裡顯示真實值，分享畫面前請檢查敏感資料。
「工具 → 搜尋 DATA TREE / 路徑」搜尋目前完整快照，即使節點尚未展開也可找到。
支援名稱、module-qualified 路徑、值、description 的不分大小寫文字搜尋，最多 500 筆；
雙擊跳到 instance，只顯示快照，不暗中發 RPC；需要最新值再按重新讀取。
「工具 → 搜尋編輯區 XML」或 Ctrl+F 標示符合文字（最多 5000 筆）。
「工具 → 所選節點 YANG 說明」或 DATA TREE 的 F1 顯示型別、default、單位、enum、
range、length、pattern、must／when 與 typedef 限制。完整 YANG 語意仍由 server 驗證。

### XML 匯入（不是立即還原整台設備）

先讀取 running/candidate、選擇要套用的節點，再按「工具 → 匯入 XML 到選取節點」。
接受選取 subtree，或 DATA TREE 匯出的 data、config、含 data 的 rpc-reply。
完整 data 檔只擷取與目前選取路徑、namespace、list key 唯一吻合的 instance；其他根不動。
上限 32 MiB，拒絕 DTD、entity、操作 RPC 及手寫 nc:operation。
會檢查 schema、可寫性與結構，忽略檔案的 config false 並保留目前唯讀值；未知欄位拒絕。
**選取 subtree 內，檔案省略的可寫節點視為刪除。** 載入前會顯示移除筆數，取消保留草稿。
載入只改本機編輯區；請核對「修改差異」和「待送出 RPC」，再按送出。
這不是完整離線 YANG validate；type、leafref、must 等最終有效性以 server 回覆為準。

### 操作紀錄與事件通知

「操作紀錄」保留最近 500 筆 UTC 時間、設備端點、操作名稱、修改筆數／部分欄位路徑、
完成／失敗類型，可匯出 JSON。正常模式跨次啟動保存於
`%USERPROFILE%\.netconf-console2\gui-operations.json`；demo／自我測試不寫個人紀錄。
不保存 XML、leaf 值、登入密碼或私鑰密碼；仍含端點資訊。它是操作摘要，不是完整 wire trace。

「事件通知」輸入 stream 名稱（預設 NETCONF），按「開始訂閱」使用目前 session 的
RFC 5277 create-subscription。需 server 宣告 `:notification:`；stream 名稱由設備提供。
若沒有 `:interleave:`，確認訂閱後會停用其他讀写 RPC，避免違反 session 限制。
「停止（中斷連線）」會確認後關閉 session（RFC 5277 沒有通用 unsubscribe RPC）。
四種連線方式均使用同一機制；斷線及自動重連後不會自動重訂閱。
通知僅留記憶體最近 100 筆，每筆最多 65536 字元，過長標示截斷；可另行匯出文字檔。
只遮蔽常見 password／secret／private-key 欄位，其他自訂敏感資料須自行檢查再分享。
此版不提供 stream discovery、replay/filter 編輯或 RFC 8639/8640 動態訂閱。
參考 [RFC 5277](https://www.rfc-editor.org/rfc/rfc5277.html)。

## 測試與建置

從原始碼啟動：

```powershell
py -3 -m pip install -e .
py -3 -m netconf_console.gui.app
```

需使用包含 Tcl/Tk 的 Windows Python，pyang 相依套件會由 pip 安裝。
GUI EXE 的離線 runtime/widget 測試：

```powershell
.\netconf-console2-gui.exe --self-test .\gui-self-test.json
```

此命令只測本機示範資料，不測真實 RU。完整 repository 另外提供
`tests/integration/gui_transport_smoke.py`，會對本機臨時 NETCONF peers 執行
四種傳輸的實際握手、schema、讀取、lock/edit/unlock 及回讀，並逐字比較預覽與
伺服器收到的 XML。測試不使用正式設備或正式認證資料。

本次也以使用者提供的 `localhost:830` SSH 端點完成唯讀實機測試：讀到 10 個
running 根節點、84 個 YANG modules 與 2,162 個 schema 節點；可辨識伺服器
default 與 config false，並在本機產生含正確 list key 的修改預覽。
**這項實機測試沒有送出 edit-config、commit 或 copy-config。** 四種模式的
寫入回讀與逐字 XML 比對僅在隔離的本機測試 peers 執行。

標準依據：[RFC 6241](https://www.rfc-editor.org/rfc/rfc6241.html)、
[RFC 6243](https://www.rfc-editor.org/rfc/rfc6243.html)、
[RFC 7950](https://www.rfc-editor.org/rfc/rfc7950.html)。
