# NETCONF Windows GUI 操作說明（3.9.1）

## 3.9.1：新增節點的必填 choice 分支

YANG 的 `choice` 與 `case` 是 schema-only 結構，不會直接出現在 XML；例如
`transport` choice 的實際 XML 節點是 `ssh` 或 `tls`。在「新增 YANG 節點」視窗中，
即使目前選取的是 `name` 等 leaf，也會在「缺少必填 choice 分支」清單看到
`transport → ssh`、`transport → tls` 等可加入選項。選擇後按「加入 choice 分支」，
再填寫該分支下顯示為「待填」的必要 leaf，最後按「加入 XML 草稿」。

若清單沒有可加入項目，可能是該分支為 `config false`、opaque (`anyxml`／`anydata`)、
feature／when 條件未啟用，或父節點尚未建立；這些情況會保留本機檢查與伺服器驗證限制，
不會把 `choice`／`case` 偽造為 XML 標籤。

新增節點預覽與加入草稿時只保留實際 XML 元素、屬性或 QName 值使用的 namespace；
不會因為目前已載入整份 YANG schema，就把所有 module 的 `xmlns` 都寫入小範本。

新增節點與既有 leaf 表單的值欄位現在都是可自由輸入的文字欄位；下方「建議值（可直接修改）」
只提供輔助，不會把輸入限制在清單內。建議值會合併 schema default、boolean／enumeration／
identityref 選項及目前可見的 leafref 目標；選取後按「帶入建議值」，仍可再手動修改，最後按
「套用欄位值」才會寫入本機草稿。密碼、secret、private-key 等敏感欄位不提供建議值；
config false、startup、既有 list key 與選取根 leaf-list 識別值仍維持唯讀保護。

## 3.9.0：第 4～7 項功能

### 4. 複製 list 與個人範本庫

1. 在 DATA TREE 選取現有 list instance，例如 `netconf-client[name='client0']`。
2. 使用「工具 → 複製所選 list 項目…」。表單會保留可寫結構及一般值，清空所有根 list keys，
   必須逐個重新填寫；同名 key 組合會被拒絕，不會覆寫既有項目。
3. `config false` 與 opaque 節點不複製；密碼、私鑰等敏感值留為待填欄位。
   處理依常見敏感欄位名稱、private-key 祖先及 PEM 標記，**特殊 vendor 敏感欄位仍須人工檢查**。
4. 「加入 XML 草稿」只在父節點產生新項目的 create 預覽，不送出。
   若與既有父／子草稿重疊，請先處理原草稿；不要用複製功能覆蓋未完成草稿。

「工具 → 保存至個人範本庫」可將目前選取的 list／container 存成 `.ncctemplate`，
預設放在 GUI 設定資料夾的 `templates` 子資料夾。範本保存時再次清除敏感值與根 list keys，
以 Windows DPAPI 加密；僅原帳號／電腦可開啟，最多 8 MiB、512 個節點、32 層。
「工具 → 開啟個人範本庫」開啟檔案清單；先選取正確的父節點／list instance，再載入範本。
載入必須匹配目前完整 schema 指紋；不匹配時拒絕，不猜測不同 firmware 的欄位對應。
範本可新增結構，但不能覆寫既有 container；所有缺值、choice、引用與權限仍需檢查。

### 5. 三方衝突處理與送出結果核對

- 選取草稿，使用「工具 → 原始值／設備最新值／草稿：三方比對…」。工具只重新讀取選取根範圍。
- 並排顯示三方值；點選一列可在下方查看三份 XML。設備的無關變更預設保留，
  只有草稿變更且設備原值未變時預設採用草稿；雙方修改同一項時必須逐項選擇。
- 按「此項採用設備最新值」或「此項採用我的草稿」；處理完後「確認合併為新草稿」。
  這只更新原始基準與本機草稿；取消不變更原草稿，送出前仍會再比對設備。
- 新增／移除整個子樹是原子選項，不拆散新 list 的結構。選取節點／祖先已消失、定位不唯一，
  或需移除整個選取根時，會要求重新讀取父節點；不會猜另一個 instance 或自動處理順序重排。
- 右下「送出結果核對」保留最近一次修改的逐項預期值／讀回值／結果，雙擊查看完整子樹 XML。
  「重新讀回核對」只讀取原設備、schema、source，不重送，也不自動解除其他寫入保護。
  sysrepocfg 結果仍須 NETCONF 讀回，不能只憑 SSH exit=0 判定設備符合預期。
- 同頁可勾選 `NETCONF rollback-on-error`，預設關閉，僅 server 宣告該 capability 時可用。
  勾選會在實際 RPC 預覽加入 error-option；test-only 亦保留。此選項不適用 sysrepocfg。
  **逾時／斷線不等於失敗，也不等於已 rollback**；仍需讀回，不會自動重送、commit 或保存 startup。

三方 XML 與結果表可能包含敏感值，只保留在本次執行的記憶體；分享畫面前請遮蔽。

### 6. 連線設定匯入與同事分享

1. 分享前使用進階頁「匯出設定 JSON（不含密碼）」。JSON 仍含主機、帳號與原路徑，分享前請檢查。
2. 同事從進階頁「匯入設定…」，或工具選單匯入 `.json`／`.dpapi`。
3. 預覽清單後以雙擊、Space 或「勾選／取消此項」選擇項目；初始全不勾選。
   可修改匯入名稱並按「套用名稱／選項」。同名預設自動加「匯入」序號；
   必須明確勾選「取代同名」並確認，才會取代既有設定組。
4. 預設清空憑證、私鑰、Known hosts、YANG 路徑，並排除密碼。
   普通 JSON 即使手動加入 password 也不匯入；原電腦的 DPAPI 備份可明確勾選匯入密碼／保留路徑。
5. 確認後只合併勾選的連線／SSH 帳號組，不會改上排現有欄位、上次選取或正在使用的 session，
   也不會自動連線。最後未儲存的 `last` 欄位不自動匯入；如要分享，請先存成具名設定組。

私鑰與憑證檔本身不包含在匯入檔內；跨電腦須依公司流程另外配置。上限 4 MiB／1,000 組，
未知欄位／格式錯誤會拒絕；保存失敗時原清單不受影響。DPAPI 備份不是跨電腦分享格式。

### 7. 一鍵連線診斷與遮蔽報告

先填好上排連線欄位，中斷 NETCONF 並停止自動重連，再選「工具 → 一鍵連線診斷／遮蔽問題報告…」。
按「開始診斷」才建立臨時 session，列出 TCP、SSH 握手／認證或 TLS 憑證握手、
NETCONF subsystem／hello、schema 載入結果及耗時；Call Home 另顯示監聽位址與接入來源。
Direct SSH 跳板的認證及轉送時間單列計算。部分共享步驟不假造獨立耗時，顯示「—」。

診斷完成會關閉自己的臨時 session，不接管主視窗連線，不變更驗證選項，不讀取 running 設定、
不送出 edit-config／commit／copy-config。schema 查詢會發出唯讀 get／get-schema，
可以使用／更新本機 schema cache；通過並不表示有寫入權限。

按「取消診斷」可取消監聽及下一個 schema 步驟；正在握手／RPC 時需等底層呼叫返回或逾時。
連線 timeout 限制為 1～60 秒，RPC timeout 為 1～30 秒；這不是整趟診斷總時限。
下方預覽即實際匯出的遮蔽 JSON：去除位址／帳號，僅保留階段、耗時、錯誤類別與固定排查提示，
不包含原始 exception 文字、XML 設定、密碼、金鑰／憑證內容或完整 capability URLs。
請先檢查預覽，再「匯出遮蔽後報告 JSON」。主視窗仍連線時不啟動第二條診斷，避免占用 Call Home port。

---

## 3.8.0 跨節點草稿、既有 leaf 表單與 leafref 關聯

### 草稿清單與跨次執行保存

修改右上 XML 後，切換 DATA TREE 的其他節點會保留原草稿；回到同一節點會顯示草稿。
DATA TREE 下的「草稿清單」顯示份數，也可從「工具」開啟，查看路徑、source、狀態與 XML，
並個別刪除。不會批次送出，仍須逐份檢查右下 RPC，再自行確認修改。

- 依 NETCONF 端點／帳號／連線路徑、running 或 candidate、含 list key 的節點路徑隔離。
  不將 Call Home 的臨時來源 port 當成設備識別。若同一位址可能接到不同設備，請自行核對設備身分。
- 相異 list instance 可有不同草稿；**父子範圍重疊不會自動合併**。
  例如已編輯整個 interface，再點其 l2-mtu 時，請先回草稿清單開啟原 interface 草稿。
- 輸入停頓約 700 ms 後加密保存；關閉／主動中斷連線前再次保存。
  未完成、暫時無法解析的 XML 也能保留，但無法產生可送出的 RPC。
- 保存於 GUI 設定檔同一資料夾的 `gui-drafts.nccdrafts`，使用 Windows DPAPI，
  僅原 Windows 帳號／電腦可解密；不會降級保存明文。示範／測試模式僅存在記憶體。
  此檔不適合交給同事，也已加入 Git 忽略規則。最多 100 份、單份 XML 16 MiB、總內容 32 MiB。
  目前請使用單一 GUI 實例編輯草稿；不支援多個程序同時更新同一份草稿檔。
- 重開程式或重連後，先連到原設備並選同一 source，再在草稿清單選「重新比對／載入」。
  確認設備後，工具會唯讀重讀、比對原值與 schema；不同就停止，不會自動覆蓋或合併衝突。
  遇到衝突可使用 3.9.0 的三方比對，逐項確認後重建草稿；也可先匯出 XML 留存，再手動重新建立。
- NETCONF 修改讀回相符後只移除已送出的那份草稿；讀回不同／失敗會保留並禁止直接重送。
  sysrepocfg 修改仍須另行重新讀取 NETCONF 確認。設備有其他本機草稿時，整份 datastore 的
  commit／discard／保存等寫入先停用，避免誤處理其他草稿；比較與 validate 不受此限制。
- 「還原」或手動重新讀取目前編輯範圍時，確認放棄會刪除該範圍草稿；其他草稿不受影響。
  加密保存失敗時請先匯出未保存 XML。無法解密的原檔不會覆寫；請先妥善保留原檔，
  關閉程式後將它移開，再重開程式建立新的草稿檔。

草稿預覽／XML 匯出仍可能含密碼等設定，分享前請遮蔽。加密不會保護已解密顯示在螢幕上的內容。

### 編輯已存在的 leaf

選取 leaf、list 或 container，按「表單編輯 leaf／引用…」；也可雙擊 DATA TREE 的 leaf。
表單列出所選範圍已有的 leaf／leaf-list，顯示型別、限制、units、default 與欄位說明。
所有可寫欄位都可直接輸入；「帶入建議值」可帶入 schema default、型別選項或可見 leafref
候選，帶入後仍可修改。按「套用欄位值」或 Enter
做本機型別檢查。切換欄位會先套用上一個有效輸入；「更新 XML 草稿」只更新右上 XML 與 RPC 預覽。
**不會直接送出。** config false、startup、既有 list key 與選取根 leaf-list 的識別值唯讀。
新增尚未存在的欄位仍使用下一節的「新增子節點…／新增根節點…」。

### 刪除已存在的資料節點

選取 DATA TREE 中要移除的既有可寫節點，按紅色「刪除整個節點…」；也可在樹狀清單按 Delete
鍵或從「工具 → 刪除整個選取節點…」執行。GUI 會自動切換到父節點，把該項目從本機
XML 草稿移除，右下預覽會產生 `nc:operation="remove"`；確認預覽後，仍要自行按
「NETCONF方式修改」或「使用系統sysrepocfg修改」才會送出。

這樣選取 `netconf-client` 這類 container／list 時，不必在整棵子樹中手動刪除 XML。
若選取的父節點底下已有子節點草稿，確認刪除父節點時會明確列出並一併移除那些
子草稿，避免它們再次阻擋父層的刪除；取消確認則不會丟棄任何草稿。
根節點、`config false`、未知 schema 節點及 list key 識別 leaf 不提供刪除按鈕；要刪除
整個 list 項目請選取 list 本身，而不是其中的 key leaf。刪除只先改本機草稿，取消確認
不會修改 XML 或設備。

### leafref 候選與引用目標

既有 leaf 表單與新增表單會從目前可見快照及這份草稿解析 leafref，提供候選值、path、
require-instance 與 when 提示。支援相對路徑、module prefix、`current()` 與 list key 條件，
候選上限 200 筆；不會拿其他節點草稿當成設備已存在的資料。
按「查看引用目標…」可查看實際 instance 路徑，再按「保留草稿並跳到目標」。
若目標只在新草稿而不在 DATA TREE，會提示回原草稿查看，不會誤跳至其他 list 項目。

**沒有候選不等於目標不存在**：NACM、defaults 或尚未讀取的資料都可能影響結果。
無法可靠解析時標記待伺服器驗證；完整 `must`／`when`／`unique`、引用存在性與權限仍由
NETCONF server 驗證。可先做 test-only（需 server 支援），不會改用實際寫入測試。

---

3.7.0：新增 schema 範本表單、可新增節點提示及整個根節點不存在時的建立入口。

## 建立尚未存在的節點／list 項目

1. 連線並讀取 running（或 candidate），確認 YANG schema 載入完整。
2. 例如已有 `ietf-netconf-server:netconf-server`，但沒有 `call-home`：
   選取 `netconf-server`，按 DATA TREE 下的「新增子節點…」，選擇 `call-home`。
   若連 `netconf-server` 都沒有，按「新增根節點…」選它。
3. 表單左側列出目前這一層的 schema 候選；可搜尋名稱／module／說明。
   灰色項目會說明不可新增的原因（已存在、config false、達到上限、另一分支已存在）。
4. 右側選取 container，使用下方候選清單和「新增子節點／項目」逐層建立：
   例如 `call-home → netconf-client → endpoints → endpoint`，再選設備 schema
   提供的 SSH 或 TLS 分支。list 的 key、mandatory 與 min-elements 欄位會產生待填提示。
   **新增另一筆 list 時，選取 list 的父容器，再新增一次相同 list 類型**，填不同 key。
5. 選取 leaf 填值後可直接輸入，或從「建議值（可直接修改）」選一項並按「帶入建議值」，
   再按「套用欄位值」（Enter 亦可）。建議值只是輔助，不會限制自訂輸入；可新增可選欄位，
   也可移除範本節點；缺少必填欄位時不能完成。
   不會自動選擇互斥 choice 分支，不會填造主機、密碼、金鑰等值。
6. 按「加入 XML 草稿」只合併進右上 XML，保留該選取範圍既有修改；右下顯示實際 RPC。
   確認後才使用「NETCONF方式修改」或「使用系統sysrepocfg修改」送出。
   不會自動 commit candidate 或保存 startup；「還原」可放棄新根節點草稿。

勾選「顯示可新增節點」後，DATA TREE 會以灰色標示候選，並在左側展開指示器欄顯示「＋」；候選會與相同階層節點對齊，雙擊可開啟表單。
提示來自 schema 與上次讀取的資料，不是設備實際資料；不會混入 DATA TREE XML 匯出。
**未讀到不保證不存在**：可能是 NACM 過濾、隱含 default 或 when 條件。
新增使用 `nc:operation="create"`，遇到設備已存在的同名節點／相同 list key 會拒絕，
不會默默覆寫；既有 leaf 修改仍用 merge，刪除仍用 remove。送出前會再次讀取比對。

範本依設備載入的有效 schema（含 feature、augment、deviation）產生。
本機檢查必填欄位、list key、重複項目、數量、choice 分支與編譯型別限制；
完整 `when`／`must`／leafref 的資料引用、`unique`、權限及裝置特殊限制仍由伺服器驗證。
可先用「測試草稿」的 test-only（需 `:validate:1.1`）；不支援時不會降級為實際寫入。
範本只自動建立必需結構；選取有 default 的 leaf 時才帶入建議值，不會灌入整棵預設資料。
anyxml／anydata 不提供結構化範本。範本預覽可能含密碼，分享或匯出前請自行遮蔽。

---

3.6.2：系統 SSH host key 驗證預設不勾選；「連線系統 SSH」閒置時為藍色，
連線成功後改為綠色。NETCONF、SSH 跳板與系統 SSH 登入密碼欄位已加寬；跳板分頁名稱為「SSH跳板」。
Source / Target 初次開啟預設為 `running`。

3.6.1：實際送出 XML 工具列改為並排的綠色「NETCONF方式修改」與右側紅色
「使用系統sysrepocfg修改」。不符合送出條件時呈灰色停用；確認與權限檢查維持不變。
連線設定各分頁以粗體與邊框區隔，目前選中的分頁為藍底白字。

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
  「另存新組」會先要求一個新的、不重複的名稱，再沿用目前欄位建立新組，
  不會因欄位內容相同而誤回存到舊組。選取清單項目會還原該組的完整快照：
  模式、位址、連接埠、Source / Target、認證、TLS 路徑、SAN、驗證選項、逾時、
  讀取選項、SSH 跳板及系統 SSH／sysrepocfg 設定；SSH／TLS／跳板／系統 SSH
  密碼會與其他 GUI 設定一樣使用 Windows DPAPI 保存。
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
  連線組本身仍保留儲存當下的完整帳密與其他連線欄位，所以刪除帳號組不會清除
  其他連線組內保存的同一帳密；這不是完整清除認證的功能。
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

按「NETCONF方式修改」後會列出變更與移除筆數，確認後才執行：

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
3.5.0 新增下方說明的 confirmed-commit 倒數回復；修改連線相關設定前仍請準備帶外恢復方式。
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
3.5.0 提供下方說明的 stream discovery 與 replay/filter；尚不提供 RFC 8639/8640 動態訂閱。
參考 [RFC 5277](https://www.rfc-editor.org/rfc/rfc5277.html)。

## 3.5.0 七項進階功能

### 1. 驗證尚未送出的 XML 草稿

修改右側 XML 後，選「設定操作 → 驗證 XML 草稿（test-only）」。
需要 server 宣告 `:validate:1.1`；只有 `:validate:1.0` 時會停用，**不降級成真正修改**。
確認後鎖定目標、比對目前設定與快照，再送出含 `test-option=test-only` 的 edit-config。
完成解鎖；不套用、不 commit、不保存 startup，草稿仍保留。解鎖失敗則關閉自己的 session。
它與「驗證 datastore」不同：後者只驗證 server 已有的設定，不包含編輯區草稿。
測試成功只代表當下通過 server 驗證；真正送出時仍會重新比對。

### 2. RPC 錯誤說明與 XML 定位

錯誤視窗整理 error-tag、error-app-tag、error-path、server 訊息及常見原因；
右下回應區保留原始 XML。以 error-path 的 namespace 和 list key 定位目前編輯區，
能唯一對應的節點以紅底標示。路徑缺漏、prefix 未宣告、位置不唯一或節點未顯示時不猜測。
紅底在重新編輯／載入 XML 時清除；config false 與 default 的既有標色仍保留。

### 3. VMware SSH 跳板（僅 Direct SSH）

上排目的地主機仍填 RU 位址，例如 `192.168.9.9`、port `830`；SSH 認證頁填 **RU 的帳號與密碼**。
在「SSH跳板」頁勾選啟用，填跳板 host `192.168.142.128`、port `22`、
跳板帳號 `caper` 及自己的跳板密碼。兩台機器的登入資料分開，不能互換。
Windows 只需能連到 VMware SSH；VMware 本身必須能透過 USB 網卡到 RU:830，
且 sshd 允許 direct-tcpip forwarding。工具不會自動修改 VMware 網路或 sshd 設定。

跳板 host key 驗證預設開啟，使用指定 Known hosts 或 `%USERPROFILE%\.ssh\known_hosts`。
非 22 port 的 host key 名稱為 `[host]:port`。先透過可信管道核對指紋並建立 known_hosts；
不會自動接受未知 key。RU 的 host key 驗證由原 SSH 認證頁獨立控制。
私鑰／agent／auto 也可選，跳板私鑰密碼與跳板登入密碼分開。
跳板設定隨連線設定組加密保存；無密碼 JSON 匯出移除兩種跳板密碼。
不需要 sshpass、外部 ssh.exe 或另開本機監聽 port，斷線會關閉所屬 tunnel。
Direct TLS、SSH Call Home、TLS Call Home 請取消跳板選項；這三種模式未改為透過跳板。

### 4. 限時確認提交與倒數

需 `:candidate:1.0` 與 `:confirmed-commit:1.1`。先將草稿明確送入 candidate，
再選「設定操作 → 限時提交 candidate → running」，輸入 30–600 秒並檢查整份 datastore 預覽。
確認後才送出 confirmed commit。上、下排之間顯示倒數，可從設定操作選單：

- 「確認保留限時提交」：保留 running；**不保存 startup**。
- 「取消限時提交／回復」：要求 server 回復提交前 running，再讀回核對。

本工具使用唯一 persist token；後續確認／取消都攜帶 persist-id，避免晚到的確認變成普通 commit。
**因此斷線不會立即回復：未確認時須等待 server 的 confirm-timeout。**
倒數不是設備狀態的證明，也無法取代帶外存取。期间保留 running/candidate 鎖，
暫停其他設定寫入與自動重連；不會自動確認、重送或保存 startup。
初始 RPC 逾時會標記結果待確認，不能再按確認保留，可嘗試明確取消或等待。
超過倒數加 RPC timeout 安全等待期後，關閉自己的 session 釋放鎖；請重新連線讀回核對結果。
程式關閉／崩潰後不會自動恢復 token 或確認；請等待 server timeout，再讀取 running。
若 server 不支援此 capability，普通 commit 不會自動獲得回復保護。

### 5. 加密設定版本與選擇性還原

「工具 → 建立加密設定備份」重新讀取目前 source 的 config-only 資料，盡量要求 defaults，
保存為帶時間名稱的 `.nccbackup`。包含版本、UTC 時間、設備端點、source、namespace 與 XML；
不含 config false 或未送出的本機草稿，也不包含 NACM 隱藏的設定。
整個檔案由 Windows DPAPI 加密，**只供原 Windows 帳號／電腦使用，不是跨電腦交換格式**。
寫入採原子替換，不建立明文暫存。請自行管理舊版本；工具不自動刪除備份。

要還原時，先讀取 running/candidate 並選擇目標 subtree，按「工具 → 開啟備份／選擇性還原」。
核對設備、時間和 source；逐項勾選要還原的差異，預設全部不勾选。
可查看原值與備份值；新增／移除整個容器或 list instance 視為一個原子項目。
schema 未知、鍵值／排序不支援或定位不唯一時拒絕，config false 保留目前值。
確認只會載入編輯區草稿，**仍須自行檢查差異、驗證並按送出**；不自動改動設備。
備份內省略而現在存在的可寫項目會列為移除，但只有勾選才套用到草稿。
`.gitignore` 已排除這類備份，避免誤上傳 GitHub。

### 6. Stream discovery、回放與告警表格

「工具 → 事件 streams／篩選／回放」可讀取 server 公布的 stream 清單、description、
replaySupport 與最早回放時間；伺服器未提供或權限不足時可保留手動輸入的即時 stream。
回放前須先查到該 stream 支援 replay。startTime / stopTime 使用含時區的 RFC3339，
如 `2026-09-09T08:00:00+08:00`。startTime 不能是未來，stopTime 需搭配 startTime 且不能較早。
可輸入通知 payload 的 subtree XML（含正確 namespace，不要包 filter／rpc／notification）。
「保存條件」不送出，還需按事件通知頁的「開始訂閱」；不會變更既有訂閱。
實際可回放範圍仍由設備決定；斷線後不自動重訂閱。

「工具 → 告警表格」顯示最近 100 筆事件的時間、severity、來源、事件名稱及選中事件 XML。
可按 severity 或文字在本機篩選，這不會改變 server subscription。
解析常見 O-RAN fault-severity/fault-source 與通用欄位；無法辨識時顯示 unknown／原文，
不是對所有 vendor event schema 的完整解碼器。收到 notificationComplete 才將訂閱標為結束；
replayComplete 只表示歷史部分播完，不代表即時訂閱停止。

### 7. 停用原因提示

將滑鼠停在主要停用按鈕上會顯示原因。設定操作選單也標示缺少 capability、
未連線、草稿未處理、結果待確認、訂閱限制或限時提交進行中。
「設定操作 → 查看功能停用原因」可一次查看狀態。不會为了啟用按鈕而繞過安全檢查。

## 3.6.0 系統 SSH／sysrepocfg 管理員修改

### 適用情境與連線

NETCONF 的 `access-denied` 表示該 NETCONF 帳號未通過授權；不等於系統 SSH 登入失敗。
這個功能供**已有該機器系統管理授權**的人操作，不會新增 NACM 規則、修改權限，
也不會在 NETCONF 失敗時暗中改用 root。仍需有效的系統 SSH 帳密／私鑰和 sysrepo 存取權限。

1. 點「顯示連線設定」，打開「系統 SSH／sysrepo」分頁。
2. Host 預設 `127.0.0.1`、port `22`、帳號 `root`；填入 Docker 模擬 O-RU 的實際系統 SSH 帳密。
3. 選擇 password／private-key／agent／auto；登入密碼與私鑰密碼分開。
4. host key 驗證預設不勾選；正式環境建議核對主機指紋並啟用，指定 Known hosts
   或使用 `%USERPROFILE%\.ssh\known_hosts`。未勾選時 SSH 不驗證伺服器身分。
5. 按「連線系統 SSH」。這只建立系統 SSH 連線，不執行修改；「中斷 SSH」不會中斷 NETCONF。

**Windows 的 `127.0.0.1:22` 必須實際轉送到存放該 sysrepo 的 O-RU Docker 容器。**
若 Docker 公開的是其他 port，請填公開的 port；若 22 是 WSL 主機本身的 sshd，
登入後執行的 sysrepocfg 可能不是容器裡那一套。工具不會自行 docker exec、sudo 或建立 port forwarding。
需要中繼主機时，可勾「經 SSH 跳板頁主機」，使用既有跳板頁的連線資料；此時目的地位址從跳板看出去。
系統 SSH host/port、帳密等隨連線設定組以 DPAPI 保存；無密碼 JSON 匯出不含這兩種系統 SSH 密碼。

### 修改一個 Call Home remote-address

1. NETCONF source 選 running，先讀取設備並載入 YANG。建議在 DATA TREE 選取要改的 remote-address leaf。
2. 在右側 XML 編輯區修改為 `2000::c5`，檢查最小變更 RPC。不要修改或重送其他帳號／密碼。
3. 按實際送出 XML 工具列右側的紅色「使用系統sysrepocfg修改」，或「工具 → 系統 SSH／sysrepocfg 修改草稿」。
4. 預覽會列出 NETCONF 設備、實際系統 SSH 端點、module、讀取／修改命令和 stdin XML。
5. 確認 OS 帳號有管理授權、兩種連線指向同一台設備的同一個 sysrepo instance，勾選確認後再按執行。
6. 工具先經系統 SSH 讀取該 module，比對原快照。不同就停止，**不送出修改**。
7. 比對相符才送一次修改，再經 SSH 讀回核對。不會重送、不保存 startup。
8. 無論成功或結果不明，都保留編輯內容並要求重新讀取 NETCONF；未重新讀取前不允許再次走此入口。

以此 module 為例，修改命令為：

```sh
sysrepocfg --edit --datastore running --module ietf-netconf-server --format xml --timeout 10 --lock
```

程式以 SSH exec 開啟命令，直接將 UTF-8 XML 寫入 stdin 並送 EOF，等同 `< XXX.xml`，
但不在 Windows 或遠端建立包含設定的臨時 XML，也不需 sshpass 或外部 ssh.exe。
對話框允許將 sysrepocfg 路徑改成例如 `/usr/local/bin/sysrepocfg`，不接受任意 shell 指令或 sudo。
遠端 SSH 執行環境須有可用的 sysrepocfg、已安裝的 YANG 及正確的 sysrepo repository。

stdin 是 `netconf-server` 根節點，**不是**整份 `rpc/edit-config/config` envelope，
也不是只有缺少根祖先的 `call-home`。只包含變更 leaf 和定位用的 `client0`、`ssh-ep0` keys；
未修改的密碼、remote-port、keepalives 不會帶入。
sysrepocfg 預設 merge，因此介面將原 RPC 的祖先 `default-operation=none` 轉成
sysrepo 專用 `sr:operation="none"`（`sr` namespace 為 `http://www.sysrepo.org/yang/sysrepo`），
而變更 leaf 仍保留 `nc:operation="merge"`。這個 stdin 預覽與原 NETCONF RPC 分開，不能混用。
參考 [sysrepo metadata 定義](https://github.com/sysrepo/sysrepo/blob/master/modules/sysrepo%402025-04-04.yang)。

### 限制與失敗處理

- 目前只支援 running；不提供候選提交、startup、任意 shell、sudo、docker exec 或 NACM 自動放行。
- 修改 Call Home 位址可能使目前 NETCONF 或系統 SSH 中斷；不會自動重連系統 SSH或重送。
  發出此操作時也暫停 NETCONF 自動重連，請手動確認新位址與路由。
- 讀取、修改、讀回是不同的 sysrepocfg 呼叫，**不是原子 compare-and-swap**。
  不保證抵擋比對後的其他管理員併發修改。
- 有些版本只在互動 editor 路徑處理 `--lock`，stdin edit 路徑不會取得該鎖。
  介面保留使用者指定的 `--lock`，但不宣稱全程鎖定。維護期間請避免其他寫入。
  版本差異可查 [sysrepocfg 原始碼的 op_edit](https://github.com/sysrepo/sysrepo/blob/master/src/executables/sysrepocfg.c)。
- 讀取使用 `--defaults explicit` 或 `report-all` 配合 GUI defaults 選項；舊版不支援時會在修改前停止。
  Namespace／default／順序表示法不同可能造成保守的比對失敗，建議重新讀取並選擇單一 leaf。
- SSH exit code 非零、命令逾時或連線中斷都不會自動重送。exit=0 但讀回失败／不同會明確標成待確認。
  關閉 SSH channel 不能保證遠端尚未執行；請核對實際 running 後再操作。
- 所有測試使用本機合成 SSH peer；未對你的真實 O-RU 執行 sysrepocfg 修改。

## 3.9.1 遠端 Sysrepo 備份／還原

「系統 SSH／sysrepo」分頁旁新增「備份／還原」分頁。這裡執行的是已登入 RU
的系統 SSH 命令，不是 NETCONF `copy-config`，也不是 GUI 本機設定的 DPAPI 備份。
因此必須先連線到真正存放 Sysrepo 的主機，且系統 SSH 帳號要有 `sysrepocfg`、
`sysrepoctl`、`sha256sum` 與 `systemctl` 的必要權限。

### 建立備份

1. 在「系統 SSH／sysrepo」填入 RU 的 system SSH host、port、帳號與認證，按「連線系統 SSH」。
2. 切到「備份／還原」；確認遠端 BASE（預設 `/data/backup-yang-baseline`）、
   `sysrepoctl` 路徑與初始 YANG module（預設 `o-ran-sync`）。BASE 只接受安全的絕對路徑，
   不接受 `sudo`、管線或其他 shell 片段。
3. `running` 固定必要；可選擇另外備份 `candidate`／`startup`，按「建立遠端備份」。
   「檢查 YANG 初始化」可先單獨確認 `sysrepoctl -l | grep -F -q -- o-ran-sync`。

每次備份會在遠端建立獨立且權限受限的 UTC 時間目錄：

```text
/data/backup-yang-baseline/YYYYMMDD-HHMMSS[-pid]/
├── running.xml       # 必有；另外勾選的 datastore 也會在這裡
├── modules.txt       # sysrepoctl -l
└── SHA256SUMS        # XML 的 sha256sum
```

等價的核心遠端操作是：

```sh
umask 077
sysrepocfg --export="$DEST/running.xml" --datastore=running --format=xml
sysrepoctl -l > "$DEST/modules.txt"
(cd "$DEST" && sha256sum ./*.xml > SHA256SUMS)
```

工具會先建立 `BASE` 並設定 `700`，備份目錄也是 `700`，檔案為 `600`；中途失敗會清理
不完整的時間目錄。時間相同時會加上 process id 尾碼，不會覆寫舊備份。

### 從最新備份還原 running

按「從最新備份還原 running」後，工具會在 BASE 下找名稱以時間開頭的最新目錄，要求同時有
`running.xml` 與 `SHA256SUMS`，先驗證 checksum；真正執行前還會再次確認最新目錄與 checksum。
還原只套用 `running.xml`，不會自動還原 candidate、startup、YANG module 安裝或 GUI 設定。

還原是可能中斷服務的管理操作，必須先中斷 NETCONF，並在確認視窗勾選授權後才會執行。
還原前不會檢查或阻擋本機 XML 草稿；還原完成後，原本的草稿可能與設備狀態不一致，請重新
讀取 running 並自行檢查或清理草稿。執行時會依序停止：

```text
meta-oran-mplaned.service
rumanager.service
netopeer2-server.service
mplane-dependency.service
meta-oran-dbus.service
```

然後對上列清單中還原前確實為 `active` 的服務執行：

```sh
sha256sum -c "$LATEST/SHA256SUMS"
sysrepocfg --copy-from="$LATEST/running.xml" --datastore=running --format=xml
```

程式只重新啟動還原前原本為 `active` 的服務，並按 `meta-oran-dbus`、
`mplane-dependency`、`netopeer2-server`、`meta-oran-mplaned`、`rumanager` 的順序嘗試恢復；
這比固定只啟動其中兩個服務安全，避免把原本正在運作的服務留在 stopped。若服務啟動失敗，
會明確列出，不會假裝全部恢復成功。

還原前後都不會自動重送 XML、commit 或保存 startup。還原成功或結果不明後，GUI 會要求重新
連線 NETCONF 並重新讀取 running；若最新備份在確認後被另一個備份取代，工具會停止而不套用。
這些按鈕不提供任意 shell、`sudo`、`docker exec` 或 NACM 自動放行，請只對已獲授權的 RU 使用。

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

早期版本曾以使用者提供的 `localhost:830` SSH 端點完成唯讀實機測試：讀到 10 個
running 根節點、84 個 YANG modules 與 2,162 個 schema 節點；可辨識伺服器
default 與 config false，並在本機產生含正確 list key 的修改預覽。
**這項實機測試沒有送出 edit-config、commit 或 copy-config。** 四種模式的
寫入回讀與逐字 XML 比對僅在隔離的本機測試 peers 執行。
3.8.0 新增草稿 DPAPI／隔離／重新比對、既有 leaf 表單與 leafref 條件的本機回歸測試；
本次更新沒有對真實 O-RU 執行連線或修改。

標準依據：[RFC 6241](https://www.rfc-editor.org/rfc/rfc6241.html)、
[RFC 6243](https://www.rfc-editor.org/rfc/rfc6243.html)、
[RFC 7950](https://www.rfc-editor.org/rfc/rfc7950.html)。
