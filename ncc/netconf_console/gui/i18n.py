"""Small, dependency-free language layer for the Qt GUI.

The NETCONF/YANG payload is intentionally never translated.  This module only
translates GUI text and keeps the selected language separate from connection
profiles and device data.
"""

from __future__ import annotations

import locale


LANGUAGES = ("zh-TW", "zh-CN", "en")
LANGUAGE_LABELS = {
    "zh-TW": "繁體中文",
    "zh-CN": "簡體中文",
    "en": "English",
}

_current_language = "zh-TW"


def normalize_language(value):
    value = str(value or "").strip().lower().replace("_", "-")
    if value in {"zh-tw", "zh-hant", "zh-hk", "zh-mo", "traditional", "traditional-chinese"}:
        return "zh-TW"
    if value in {"zh-cn", "zh-hans", "zh-sg", "zh-my", "simplified", "simplified-chinese"}:
        return "zh-CN"
    if value in {"en", "en-us", "en-gb", "english"}:
        return "en"
    return ""


def detect_system_language(locale_name=None):
    """Map the OS locale to one of the supported GUI languages."""
    if locale_name is None:
        try:
            from PySide6.QtCore import QLocale
            locale_name = QLocale.system().name()
        except Exception:
            locale_name = locale.getlocale()[0] or ""
    normalized = normalize_language(locale_name)
    return normalized or ("zh-CN" if str(locale_name).lower().startswith("zh") else "en")


def resolve_language(preference=None):
    return normalize_language(preference) or detect_system_language()


def set_language(language):
    global _current_language
    _current_language = resolve_language(language)
    return _current_language


def current_language():
    return _current_language


# Common GUI terms are kept as phrases so status messages containing values or
# paths can also be translated without touching the value supplied by a user.
_HANS_PHRASES = {
    "繁體中文": "繁体中文", "簡體中文": "简体中文", "語言": "语言",
    "連線": "连接", "連線設定": "连接设置", "連線設定組": "连接设置组", "另存新組": "另存新组",
    "NETCONF連線": "NETCONF连接", "系統 SSH": "系统 SSH", "系統SSH": "系统SSH",
    "備份 / 還原": "备份 / 还原", "SSH 認證": "SSH 认证", "TLS 憑證": "TLS 证书",
    "SSH 跳板": "SSH 跳板", "進階": "高级", "資料夾": "文件夹", "檔案": "文件",
    "本機": "本机", "目前": "当前", "伺服器": "服务器", "網路": "网络",
    "帳號": "账号", "密碼": "密码", "認證": "认证", "憑證": "证书",
    "私鑰": "私钥", "已存": "已保存", "設定": "设置", "讀取": "读取",
    "選取": "选择", "選擇": "选择", "刪除": "删除", "匯出": "导出",
    "匯入": "导入", "範本": "模板", "儲存": "保存", "還原": "还原",
    "顯示": "显示", "隱藏": "隐藏", "搜尋": "搜索", "說明": "说明", "名稱": "名称",
    "型別": "类型", "類型": "类型", "狀態": "状态", "無法": "无法",
    "請": "请", "並": "并", "將": "将", "確認": "确认", "斷線": "断线",
    "重連": "重连", "執行": "执行", "預覽": "预览", "實際": "实际",
    "變更": "变更", "移除": "移除", "送出": "发送", "套用": "应用",
    "欄位": "字段", "建議": "建议", "候選": "候选", "節點": "节点",
    "父層": "父层", "根節點": "根节点", "子節點": "子节点", "清單": "列表",
    "項目": "项目", "必填": "必填", "新增": "新增", "加入": "加入",
    "完成": "完成", "提醒": "提醒", "錯誤": "错误", "警告": "警告",
    "正在執行": "正在执行", "尚未": "尚未", "沒有": "没有", "其他": "其他",
    "路徑": "路径", "命令": "命令", "詳細": "详细", "說明": "说明",
    "版本": "版本", "時間": "时间", "結果": "结果", "預期": "预期",
    "回應": "响应", "操作紀錄": "操作记录", "事件通知": "事件通知",
    "唯讀": "只读", "資料": "数据", "模組": "模块", "節": "节",
    "檢查": "检查", "驗證": "验证", "等待": "等待", "取消": "取消",
    "通過": "通过", "失敗": "失败", "跳到": "跳转到", "重新": "重新",
    "開啟": "打开", "關閉": "关闭", "新增根": "新增根", "新增子": "新增子",
    "使用系統": "使用系统", "方式": "方式", "修改": "修改", "目前沒有": "当前没有",
    "不會": "不会", "可能": "可能", "需要": "需要", "仍可": "仍可",
    "自行": "自行", "填入": "填入", "套用欄位值": "应用字段值",
    "帶入建議值": "带入建议值", "顯示不可新增節點": "显示不可新增节点",
}

_HANS_CHARS = str.maketrans({
    "體": "体", "簡": "简", "語": "语", "連": "连", "線": "线", "設": "设",
    "擇": "择", "讀": "读", "節": "节", "點": "点", "開": "开", "關": "关",
    "閉": "闭", "顯": "显", "變": "变", "復": "复", "匯": "汇", "帳": "帐",
    "號": "号", "認": "认", "證": "证", "預": "预", "檢": "检", "無": "无",
    "為": "为", "與": "与", "從": "从", "來": "来", "發": "发", "現": "现",
    "實": "实", "際": "际", "專": "专", "業": "业", "應": "应", "該": "该",
    "當": "当", "時": "时", "間": "间", "僅": "仅", "會": "会", "組": "组",
    "類": "类", "別": "别", "頁": "页", "範": "范", "圍": "围", "庫": "库",
    "檔": "档", "碼": "码", "輸": "输", "網": "网", "伺": "伺", "服": "服",
    "器": "器", "憑": "凭", "鑰": "钥", "儲": "储", "還": "还", "隱": "隐",
    "尋": "寻", "說": "说", "型": "型", "狀": "状", "並": "并", "將": "将",
    "執": "执", "預": "预", "覽": "览", "際": "际", "項": "项", "選": "选",
    "刪": "删", "匯": "汇", "導": "导", "範": "范", "補": "补", "錯": "错",
    "誤": "误", "擴": "扩", "張": "张", "線": "线", "讀": "读", "寫": "写",
    "檢": "检", "測": "测", "狀": "状", "態": "态", "響": "响", "聲": "声",
    "聽": "听", "傳": "传", "應": "应", "獨": "独", "立": "立", "總": "总",
    "數": "数", "據": "据", "模": "模", "組": "组", "載": "载", "頁": "页",
    "標": "标", "題": "题", "層": "层", "選": "选", "項": "项", "詳": "详",
    "細": "细", "說": "说", "明": "明", "參": "参", "考": "考", "專": "专",
    "業": "业", "個": "个", "別": "别", "這": "这", "裏": "里", "裡": "里",
    "為": "为", "進": "进", "階": "阶", "級": "级", "預": "预", "設": "设",
    "終": "终", "結": "结", "繼": "继", "續": "续", "處": "处", "理": "理",
    "錯": "错", "誤": "误", "選": "选", "擇": "择", "讀": "读", "取": "取",
    "問": "问", "題": "题", "產": "产", "生": "生", "從": "从", "單": "单",
    "純": "纯", "與": "与", "儲": "储", "存": "存", "檢": "检", "視": "视",
    "觀": "观", "視": "视", "傳": "传", "送": "送", "發": "发", "佈": "布",
    "啟": "启", "動": "动", "關": "关", "閉": "闭", "確": "确", "認": "认",
    "須": "须", "須": "须", "讓": "让", "維": "维", "持": "持", "從": "从",
    "屬": "属", "於": "于", "總": "总", "共": "共", "筆": "笔", "數": "数",
    "沒": "没", "有": "有", "可": "可", "能": "能", "會": "会", "因": "因",
    "為": "为", "與": "与", "條": "条", "件": "件", "導": "导", "致": "致",
    "維": "维", "護": "护", "簡": "简", "單": "单", "適": "适", "當": "当",
    "較": "较", "少": "少", "見": "见", "頻": "频", "率": "率", "狀": "状",
    "態": "态", "運": "运", "作": "作", "監": "监", "控": "控", "頁": "页",
    "按": "按", "鈕": "钮", "寬": "宽", "度": "度", "高": "高", "顏": "颜",
    "色": "色", "紅": "红", "綠": "绿", "藍": "蓝", "黑": "黑", "白": "白",
    "預": "预", "設": "设", "默": "默", "認": "认", "自": "自", "動": "动",
})

_EN_EXACT = {
    "資料/XML": "Data / XML", "RPC 範本": "RPC template", "帶入範本": "Load template",
    "載入 XML…": "Load XML…", "儲存 XML…": "Save XML…", "預覽 RPC": "Preview RPC",
    "送出 RPC": "Send RPC", "送出 RPC…": "Sending RPC…",
    "載入 RPC XML": "Load RPC XML", "儲存 RPC XML": "Save RPC XML",
    "訂閱設定 / Filter / Replay…": "Subscription / Filter / Replay…",
    "送出Create Subscription": "Send Create Subscription",
    "Session(s)管理": "Session manager",
    "Measurement範本": "Measurement templates",
    "Fault Management範本": "Fault Management templates",
    "讀取目前告警": "Read current alarms",
    "使用 O-RAN active-alarm-list 查詢目前告警；此範本只讀取設備資料。": "Query current alarms with O-RAN active-alarm-list; this template only reads device data.",
    "載入 active-alarm-list RPC 範本": "Load active-alarm-list RPC template",
    "告警 Notification 訂閱": "Alarm notification subscription",
    "帶入 alarm-notif subtree filter 與 fault-management stream；送出前可確認 DUT 支援的 stream。": "Load the alarm-notif subtree filter and fault-management stream; check DUT stream support before sending.",
    "帶入 alarm-notif 訂閱範本": "Load alarm-notif subscription template",
    "Create Subscription RPC 內容預覽（唯讀）": "Create Subscription RPC preview (read-only)",
    "內容會依目前 Stream、filter 與回放時間自動更新。": "The preview updates with the current stream, filter, and replay times.",
    "每筆訂閱會建立獨立 NETCONF session；請至 Session(s)管理分頁管理或中斷。": "Each subscription uses a separate NETCONF session. Manage or disconnect it on the Session manager tab.",
    "NETCONF Session 管理": "NETCONF session manager",
    "建立時間": "Created",
    "用途": "Purpose",
    "發送紀錄": "Sent operations",
    "操作": "Action",
    "每筆訂閱會建立獨立 NETCONF session；可在上方管理及中斷。": "Each subscription gets its own NETCONF session. Manage or disconnect sessions above.",
    "EPE Measurement 設定範本（先啟用再訂閱）": "EPE measurement configuration template (enable before subscribing)",
    "範本": "Template",
    "載入範本": "Load template",
    "Measurement interval（秒）": "Measurement interval (seconds)",
    "啟用": "Enable",
    "Object unit": "Object unit",
    "Report info": "Report info",
    "產生 edit-config 範本並切換至 RPC": "Generate edit-config template and open RPC tab",
    "此 RPC 會在各自的 supervision 訂閱 session 收到通知時送出。": "This RPC is sent on each supervision subscription session when a notification arrives.",
    "Create Subscription RPC 回應": "Create Subscription RPC reply",
    "收到 supervision-notification 時自動送 supervision-watchdog-reset": "Automatically send supervision-watchdog-reset when supervision-notification arrives",
    "只在收到 supervision-notification 時送一次；需 server 宣告 :interleave。預設關閉。": "Sends once when a supervision-notification arrives; the server must advertise :interleave. Off by default.",
    "通知表格 / 篩選…": "Notification table / Filter…", "清除通知": "Clear notifications",
    "Subscription 條件": "Subscription options",
    "偵測 DUT 支援": "Detect DUT support", "帶入所選 Stream": "Use selected stream",
    "Event Stream": "Event Stream", "用途": "Purpose", "典型 Notification": "Typical Notification",
    "DUT 支援": "DUT support", "Replay": "Replay", "尚未偵測": "Not detected",
    "支援": "Supported", "未支援": "Unsupported", "未回報": "Not reported",
    "startTime（選用）": "startTime (optional)", "stopTime（選用）": "stopTime (optional)",
    "Subtree filter XML（選用；填 notification payload，不要包 filter／rpc）": "Subtree filter XML (optional; enter the notification payload without filter/rpc wrappers)",
    "清除 Filter": "Clear filter", "帶入 Measurement 範本": "Load measurement template",
    "Measurement result 範本（7 groups / 37 objects）": "Measurement result template (7 groups / 37 objects)",
    "尚未偵測 DUT；勾選規格項目後可產生 subtree filter。": "DUT not detected; select specification items to build a subtree filter.",
    "Measurement group / object": "Measurement group / object", "規格": "Specification",
    "全選 37 項": "Select all 37", "清除選取": "Clear selection",
    "分類": "Category", "嚴重度": "Severity", "搜尋時間、來源、事件或 XML": "Search time, source, event, or XML",
    "軟體管理": "Software management", "檔案管理": "File management", "載波狀態": "Carrier state",
    "M-Plane 控制": "M-Plane control", "同步狀態": "Synchronization",
    "硬體／外部 I/O": "Hardware / external I/O", "安全／憑證": "Security / certificate",
    "電源狀態": "Power state", "天線／波束成形": "Antenna / beamforming",
    "量測作業": "Measurement operation", "NETCONF 控制": "NETCONF control", "其他": "Other",
    "尚未送出 RPC。": "No RPC sent yet.",
    "輸入 operation 或完整 rpc；完整 rpc 的 message-id 會保留，缺少時自動產生。按送出會直接執行 XML。": "Enter an operation or a complete rpc. Existing message-id values are preserved; missing IDs are generated. Send executes the XML directly.",
    "O-RAN supervision 訂閱需持續送 watchdog reset；此頁範本只執行一次。": "O-RAN supervision requires repeated watchdog reset RPCs. The template on this page runs once.",
    "Client 送出 Create Subscription，O-RU 發送 Notification。NETCONF stream 可接收所有允許的事件；包含 supervision 時需持續送 watchdog reset RPC。": "The client sends Create Subscription; the O-RU sends Notification. The NETCONF stream carries all permitted events. Supervision requires repeated watchdog reset RPCs.",
    "已接收 0 筆通知（最多保留最近 100 筆）": "0 notifications received (up to 100 retained)",
    "目前保留 %d 筆通知（最多 100 筆）": "%d notifications retained (up to 100)",
    "XML 已變更；請預覽或送出 RPC。": "XML changed; preview or send the RPC.",
    "RPC 預覽已產生；尚未送出。": "RPC preview is ready; not sent yet.",
    "請先連線；離線示範與限時提交期間無法送出 RPC。": "Connect first. RPC sending is unavailable in demo mode or during a confirmed commit.",
    "目前 session 已有訂閱；請先停止訂閱。": "This session already has a subscription.",
    "Create Subscription 已送出，等待回應。": "Create Subscription sent; waiting for a reply.",
    "已收到 RPC 回應。": "RPC reply received.",
    "已收到 RPC 回應": "RPC reply received",
    "已收到 RPC 回應；請重新讀取 DATA TREE 確認設備狀態。": "RPC reply received; reread the DATA TREE to check device state.",
    "RPC 失敗或結果待確認，請查看回應。": "RPC failed or its outcome is uncertain; check the reply.",
    "Create Subscription 失敗；可檢查回應後重新訂閱。": "Create Subscription failed; check the reply before subscribing again.",
    "訂閱結果待確認；繼續接收通知，請中斷連線後再重新訂閱。": "Subscription outcome is uncertain; notification reception continues. Disconnect before subscribing again.",
    "連線已中斷，訂閱已結束；重新連線後請重新訂閱。": "Connection lost; the subscription ended. Subscribe again after reconnecting.",
    "訂閱條件已設定；按 Create Subscription 送出。": "Subscription options saved; press Create Subscription to send.",
    "已啟用自動 watchdog reset；開始 supervision 訂閱後生效。": "Automatic watchdog reset enabled; it takes effect when you subscribe to supervision.",
    "Server 未宣告 :interleave；無法在此訂閱期間自動送 watchdog reset。": "The server does not advertise :interleave; automatic watchdog reset is unavailable during this subscription.",
    "已啟用自動 watchdog reset；收到 supervision notification 時送出。": "Automatic watchdog reset enabled; sent when a supervision notification arrives.",
    "已收到 supervision notification，但 server 未宣告 :interleave；無法在現有訂閱 session 送出 watchdog reset。": "Supervision notification received, but the server does not advertise :interleave; watchdog reset cannot be sent on this subscription session.",
    "自動 watchdog reset 失敗；下一筆 supervision notification 到達時會再試。": "Automatic watchdog reset failed; it will be retried when the next supervision notification arrives.",
    "watchdog reset 失敗；下一筆 supervision notification 到達時會再試。": "Watchdog reset failed; it will be retried when the next supervision notification arrives.",
    "已停用自動 watchdog reset；可在 RPC 分頁手動送出。": "Automatic watchdog reset is off; use the RPC tab to send it manually.",
    "收到 supervision notification，watchdog reset 已回覆。": "Supervision notification received; watchdog reset replied.",
    "RPC XML 上限 2 MiB。": "RPC XML is limited to 2 MiB.",
    "RPC XML 不接受 DOCTYPE 或 entity。": "DOCTYPE and entities are not allowed in RPC XML.",
    "rpc 必須使用 NETCONF base:1.0 namespace。": "rpc must use the NETCONF base:1.0 namespace.",
    "每個 rpc 必須只包含一個 operation。": "Each rpc must contain exactly one operation.",
    "message-id 不可為空白。": "message-id must not be blank.",
    "請輸入 RPC operation；Notification 由設備發送，請使用通知頁接收。": "Enter an RPC operation. Notifications are sent by the device and received on the Notification tab.",
    "RPC operation 必須指定 namespace。": "The RPC operation must specify its namespace.",
    "NETCONF session 已改變，請重新送出。": "The NETCONF session changed; submit the RPC again.",
    "限時提交尚未結束，不能送出自訂 RPC。": "A confirmed commit is pending; custom RPCs are unavailable.",
    "RPC 與預覽不同，請重新產生預覽。": "The RPC differs from the preview; generate the preview again.",
    "訂閱表單只接受 subtree filter；XPath 可使用自訂 RPC。": "The subscription form accepts subtree filters; use a custom RPC for XPath.",
    "繁體中文": "Traditional Chinese", "簡體中文": "Simplified Chinese", "简体中文": "Simplified Chinese",
    "English": "English", "語言 / Language": "Language",
    "新增 YANG 節點 — 本機 XML 草稿": "Add YANG Node — Local XML Draft",
    "選擇節點 → 填值／新增分支 → 加入草稿 → 檢查 RPC 後再送出": "Select a node → enter values / add branches → add to draft → review RPC before sending",
    "未讀到不代表不存在（可能是 NACM、default 或條件造成）。新增使用 create；when、must、leafref、unique 與權限仍由伺服器驗證。本視窗不會自動送出。": "Not being read does not mean that a node does not exist (NACM, defaults, or conditions may be involved). New nodes use create; the server validates when, must, leafref, unique, and permissions. This dialog never sends automatically.",
    "本機檢查通過；完整 YANG 條件與權限仍需伺服器驗證。尚未送出。": "Local checks passed; the server must still validate complete YANG conditions and permissions. Nothing has been sent.",
    "候選節點／搜尋": "Candidates / Search", "顯示不可新增節點": "Show unavailable nodes",
    "包含已存在、config false、數量上限及互斥 choice 分支等項目": "Include existing, config false, max-count, and conflicting choice branches",
    "搜尋 module、節點、型別或說明": "Search module, node, type, or description",
    "範本階層（選取欄位填值）": "Template hierarchy (select a field to enter a value)",
    "值／狀態": "Value / Status", "請選取候選節點": "Select a candidate node",
    "選取 leaf 後填入值": "Enter a value after selecting a leaf",
    "套用欄位值": "Apply field value", "建議值（仍可自行輸入）": "Suggested value (editable)",
    "帶入建議值": "Use suggested value", "缺少必填 choice 分支": "Missing required choice branch",
    "加入 choice 分支": "Add choice branch", "新增子節點／項目": "Add child node / item",
    "移除範本節點": "Remove template node", "這裡是可自由編輯的本機 XML 草稿": "Editable local XML draft",
    "加入 XML 草稿": "Add to XML draft", "管理連線設定組": "Manage connection profiles",
    "管理 SSH 帳號組": "Manage SSH account profiles", "載入並編輯": "Load and edit",
    "重新命名": "Rename", "刪除選取項目": "Delete selected item", "關閉": "Close",
    "連線 / Schema 詳情": "Connection / Schema details", "設定操作": "Configuration",
    "工具": "Tools", "連線設定組": "Connection profile", "儲存連線設定": "Save connection",
    "另存新組": "Save as new profile", "管理…": "Manage…", "斷線後自動重連": "Reconnect after disconnect",
    "停止重連": "Stop reconnect", "模式": "Mode", "連線 / 開始監測": "Connect / Start monitoring",
    "Session keepalive（秒；0 關閉）": "Session keepalive (seconds; 0 disables)",
    "等待秒數": "Wait seconds", "中斷 / 取消等待": "Disconnect / Cancel wait", "NETCONF連線": "NETCONF Connection",
    "瀏覽…": "Browse…", "SSH 帳號組": "SSH account profile", "儲存帳號": "Save account",
    "新增帳號": "New account", "帳號": "Username", "密碼（加密儲存）": "Password (encrypted)",
    "驗證 SSH host key": "Verify SSH host key", "搜尋本機金鑰": "Search local keys", "SSH 認證": "SSH Authentication",
    "驗證名稱": "Verify server name", "TLS 憑證": "TLS Certificate", "跳板 host": "Jump host",
    "認證方式": "Authentication", "啟用（僅 Direct SSH）": "Enable (Direct SSH only)",
    "跳板帳號": "Jump username", "跳板登入密碼": "Jump password", "驗證跳板 host key": "Verify jump host key",
    "跳板 Private key": "Jump private key", "私鑰密碼": "Private key passphrase", "跳板 Known hosts": "Jump known hosts",
    "SSH 跳板": "SSH Jump Host", "更新 YANG schema": "Update YANG schema", "本機 YANG 資料夾（備援）": "Local YANG folder (fallback)",
    "匯出設定 JSON（不含密碼）": "Export settings JSON (without passwords)", "匯出加密備份（含密碼）": "Export encrypted backup (with passwords)",
    "匯入設定…": "Import settings…", "進階": "Advanced", "SSH 認證方式 / 私鑰密碼": "SSH Authentication / Key Passphrase",
    "系統帳號": "System username", "登入密碼": "Login password", "Timeout 秒": "Timeout seconds",
    "sysrepocfg 路徑": "sysrepocfg path", "驗證 host key": "Verify host key", "經 SSH 跳板頁主機": "Use SSH Jump Host page",
    "連線系統 SSH": "Connect system SSH", "Sysrepocfg讀取": "Read via sysrepocfg",
    "中斷 SSH": "Disconnect SSH", "系統SSH": "System SSH",
    "遠端 BASE": "Remote BASE", "sysrepoctl 路徑": "sysrepoctl path", "初始 YANG module": "Initial YANG module",
    "備份 datastore": "Backup datastores", "檢查 YANG 初始化": "Check YANG initialization", "建立遠端備份": "Create remote backup",
    "從遠端備份還原": "Restore from remote backup", "從最新備份還原 running": "Restore running from latest backup", "備份 / 還原": "Backup / Restore",
    "選擇遠端 Sysrepo 備份": "Select remote Sysrepo backup", "確認選擇並繼續": "Confirm selection and continue",
    "搜尋": "Search", "節點名稱、值或路徑": "Node name, value, or path", "包含 YANG default 值": "Include YANG default values",
    "包含 config false（唯讀）": "Include config false (read-only)", "顯示可新增節點": "Show creatable nodes",
    "重新讀取全部": "Read all again", "更新 YANG": "Update YANG", "匯出XML": "Export XML",
    "新增子節點…": "Add child node…", "新增根節點…": "Add root node…", "刪除整個節點…": "Delete entire node…",
    "表單編輯 leaf／引用…": "Edit leaf / reference…", "YANG：未載入": "YANG: not loaded",
    "讀取 / 編輯 XML": "Read / Edit XML", "自動折行": "Wrap lines", "還原": "Revert", "重新讀取": "Read again",
    "請先連線，再選擇左側節點": "Connect first, then select a node on the left",
    "實際送出 XML": "XML to be sent", "NETCONF方式修改": "Modify via NETCONF", "使用系統sysrepocfg修改": "Modify via system sysrepocfg",
    "尚無變更，不會送出任何設定": "No changes; nothing will be sent", "待送出 RPC（唯讀）": "Pending RPC (read-only)",
    "最後 RPC 回應": "Last RPC reply", "修改差異": "Change diff", "匯出紀錄 JSON": "Export log JSON",
    "操作紀錄": "Operation log", "事件通知": "Event notifications", "送出結果核對": "Send result verification",
    "開始訂閱": "Start subscription", "停止（中斷連線）": "Stop (disconnect)", "匯出通知…": "Export notifications…",
    "重新讀回核對（不重送）": "Read back for verification (do not resend)", "節點／instance": "Node / instance",
    "預期值": "Expected value", "讀回值": "Read-back value", "結果": "Result",
    "設定操作：": "Configuration: ", "搜尋 DATA TREE / 路徑…": "Search DATA TREE / path…",
    "搜尋編輯區 XML…": "Search editor XML…", "所選節點 YANG 說明…": "YANG details for selected node…",
    "草稿清單／加密保存與恢復…": "Draft list / encrypted save and restore…", "表單編輯 leaf／leafref 關聯…": "Edit leaf / leafref links…",
    "複製所選 list 項目…": "Copy selected list item…", "保存至個人範本庫…": "Save to personal template library…",
    "開啟個人範本庫…": "Open personal template library…", "匯入連線／SSH 帳號設定…": "Import connection / SSH account settings…",
    "原始值／設備最新值／草稿：三方比對…": "Compare original / latest device value / draft…",
    "一鍵連線診斷／遮蔽問題報告…": "One-click connection diagnostics / redacted report…",
    "建立加密設定備份…": "Create encrypted settings backup…", "開啟備份／選擇性還原…": "Open backup / selective restore…",
    "事件 streams／篩選／回放…": "Event streams / filter / replay…", "告警表格…": "Alarm table…",
    "系統 SSH／sysrepocfg 修改草稿…": "System SSH / sysrepocfg draft edit…", "關閉": "Close",
    "語言已切換為 %s；重新開啟後也會保留此選擇。": "Language changed to %s; this choice will be kept after restart.",
    "確定": "OK", "是": "Yes", "否": "No", "全部": "All",
    "更新 XML 草稿": "Update XML draft", "確認匯入選取項目": "Confirm import selection",
    "目前沒有待選的必填 choice": "No required choice branch is pending",
    "沒有符合條件的可新增節點；需要檢查原因時可勾選「顯示不可新增節點」。": "No creatable nodes match; enable 'Show unavailable nodes' to inspect the reason.",
    "請選擇要新增的節點。": "Select a node to add.",
    "XML 草稿已修改；切換欄位或加入草稿時會重新解析並檢查。": "The XML draft changed; it will be parsed and checked again when fields change or the draft is added.",
    "☑ running（必要）": "☑ running (required)",
    "尚未執行；備份使用 UTC 時間目錄，還原前會驗證 SHA256。": "Not run yet; backups use UTC timestamped folders, and SHA256 is verified before restore.",
    "系統 SSH 未連線（獨立於 NETCONF）": "System SSH not connected (independent of NETCONF)",
    "auto：依序嘗試私鑰／本機金鑰／Agent／登入密碼；其他模式只使用指定方法。\n登入密碼及私鑰路徑在 SSH 認證分頁；私鑰密碼不會當登入密碼送出。": "auto: try private key / local keys / agent / login password in order; other modes use only the selected method.\nLogin password and private-key path are on the SSH Authentication tab; the private-key passphrase is never sent as the login password.",
    "Bind address（選用）": "Bind address (optional)", "CRL（選用）": "CRL (optional)",
    "TLS 驗證名稱預設不勾選；若伺服器憑證 SAN 是 DNS 名稱而非連線 IP，請填名稱後再勾選。": "Server-name verification is off by default; if the server certificate SAN is a DNS name rather than the connection IP, enter that name and enable it.",
    "尚未送出；此頁只保留本次執行的最近一次修改。": "Not sent; this page keeps only the most recent change from this run.",
    "未訂閱；使用目前 session，斷線後不會自動重新訂閱。": "Not subscribed; the current session is used and subscriptions are not restored automatically after disconnect.",
    "本機操作紀錄；密碼與 XML 不寫入此紀錄。": "Local operation log; passwords and XML are not written to this log.",
    "伺服器 default · 等於 schema default · config false · 修改中 · schema 未知": "Server default · schema default · config false · modifying · schema unknown",
    "NETCONF rollback-on-error（需 server 支援；不適用 sysrepocfg）": "NETCONF rollback-on-error (requires server support; not used by sysrepocfg)",
    "搜尋 XML": "Search XML", "搜尋 DATA TREE（目前快照；名稱、module 路徑、值、description）": "Search DATA TREE (current snapshot; names, module paths, values, descriptions)",
    "輸入編輯區要搜尋的文字（不分大小寫）：": "Text to search in the editor (case-insensitive):",
    "找不到符合「%s」的節點": "No nodes match \"%s\"",
    "比較 running / startup": "Compare running / startup", "提交 candidate → running": "Commit candidate → running",
    "捨棄 candidate（恢復為 running）": "Discard candidate (restore running)", "驗證 datastore": "Validate datastore",
    "限時提交 candidate → running": "Confirmed commit candidate → running", "限時提交：確認保留…": "Confirmed commit: keep…",
    "限時提交：取消回復…": "Confirmed commit: cancel and revert…", "限時提交尚未結束。": "The confirmed commit has not ended.",
    "限時提交需為 30–600 秒。": "Confirmed commit must be 30–600 seconds.",
    "限時提交需先連線。": "Connect before starting a confirmed commit.",
    "比較 running / startup…": "Compare running / startup…",
    "提交 candidate → running…": "Commit candidate → running…",
    "捨棄 candidate（恢復為 running）…": "Discard candidate (restore running)…",
    "限時提交 candidate → running…": "Confirmed commit candidate → running…",
    "查看功能停用原因…": "View why this feature is unavailable…",
    "刪除整個選取節點…": "Delete the entire selected node…",
    "匯入 XML 到選取節點…": "Import XML into the selected node…",
    "建立此候選節點…": "Create this candidate node…",
    "新增子節點／list 項目…": "Add child node / list item…",
    "新增根 YANG 節點…": "Add root YANG node…",
    "新增節點": "Add node", "範本檢查": "Template check", "確認刪除": "Confirm deletion",
}

_EN_PHRASES = {
    "目前快照": "current snapshot", "不分大小寫": "case-insensitive", "搜尋": "Search",
    "新增 YANG 節點": "Add YANG node", "新增根 YANG 節點": "Add root YANG node",
    "新增子節點／list 項目": "Add child node / list item", "建立此候選節點": "Create this candidate node",
    "本機 XML 草稿": "local XML draft", "本機草稿": "local draft", "XML 草稿": "XML draft",
    "範本根節點": "template root node", "範本節點": "template node", "範本檢查": "Template check",
    "選擇可建立的節點": "select a creatable node", "請選擇": "Select ", "請先": "Please first ",
    "請確認": "Please confirm", "請填入": "Please enter ", "請指定": "Please specify ",
    "請選取": "Please select ", "請重新": "Please re-", "沒有符合": "No matching ",
    "無法": "Unable to ", "已加入": "Added ", "已儲存": "Saved ", "已另存並儲存": "Saved as ",
    "已載入": "Loaded ", "已匯出": "Exported ", "已匯入": "Imported ", "已還原": "Restored ",
    "已停止": "Stopped ", "已連線": "Connected", "尚未連線": "Not connected",
    "連線已中斷": "Connection disconnected", "連線已變更": "Connection changed",
    "連線、讀取 schema 與資料": "Connect, read schema and data", "設備最新值": "latest device value", "原始值": "original value",
    "草稿": "draft", "資料": "data", "資料庫": "database", "清單": "list", "項目": "item",
    "節點": "node", "父層": "parent", "子節點": "child node", "根節點": "root node",
    "欄位": "field", "值": "value", "狀態": "status", "結果": "result", "說明": "description",
    "型別": "type", "類型": "type", "候選": "candidate", "分支": "branch", "缺少": "Missing ",
    "必填": "required", "衝突": "conflict", "選項": "option", "建議值": "suggested value",
    "帶入": "Use ", "套用": "Apply ", "移除": "Remove ", "刪除": "Delete ", "新增": "Add ",
    "保存": "Save", "儲存": "Save", "還原": "Restore", "匯出": "Export", "匯入": "Import",
    "重新讀取": "Read again", "重新讀回": "Read back", "重新比對": "Reconcile", "重新命名": "Rename",
    "連線設定": "connection settings", "帳號": "username", "密碼": "password", "認證": "authentication",
    "驗證": "Verify", "憑證": "certificate", "私鑰": "private key", "跳板": "jump host",
    "系統 SSH": "system SSH", "系統SSH": "system SSH", "遠端": "remote", "本機": "local",
    "伺服器": "server", "路徑": "path", "資料夾": "folder", "檔案": "file", "命令": "command",
    "連線": "connection", "斷線": "disconnect", "重連": "reconnect", "等待": "wait", "秒": "seconds",
    "設定": "settings", "模式": "mode", "更新": "Update", "讀取": "Read", "寫入": "Write",
    "執行": "Run", "檢查": "Check", "完成": "completed", "失敗": "failed", "警告": "Warning",
    "錯誤": "Error", "提醒": "Notice", "無": "None", "有": "Yes", "沒有": "No",
    "尚無": "No ", "尚未": "Not yet ", "不會": "will not", "自動": "automatically", "目前": "current",
    "完整": "complete", "獨立": "independent", "唯讀": "read-only", "唯讀": "read-only",
    "顯示": "Show", "隱藏": "Hide", "包含": "Include", "不含": "without", "含": "including",
    "使用": "Use", "方式": "via", "實際": "actual", "預覽": "preview", "確認": "Confirm",
    "取消": "Cancel", "關閉": "Close", "開啟": "Open", "選擇": "Select", "選取": "Select",
    "跳到": "Jump to", "共": "Total ", "組": "profiles", "筆": "items", "時間": "Time",
    "版本": "version", "回應": "reply", "操作紀錄": "operation log", "事件通知": "event notification",
    "沒有可": "No available ", "可新增": "creatable", "不代表": "does not mean", "可能": "may",
    "因為": "because", "若": "If", "如果": "If", "仍需": "still requires", "需要": "requires",
    "伺服器驗證": "server validation", "權限": "permission", "資料模型": "data model", "修改中": "modifying",
    "未載入": "not loaded", "未連線": "not connected", "未送出": "not sent", "已送出": "sent",
    "確定": "Confirm", "正在執行": "Running", "停止": "Stop", "重試": "Retry", "繼續": "Continue",
    "輸入": "Enter", "新名稱": "New name", "目前選取範圍": "current selection", "父層": "parent",
    "不會修改設備": "does not modify the device", "不會送出": "will not send", "不會自動": "will not automatically",
    "保存 startup": "save startup", "commit candidate": "commit candidate", "送出": "Send",
    "逐項": "item by item", "上限": "limit", "最多": "up to", "內容": "content",
    "已讀取 ": "Read ", "個根節點": "root nodes", "個根節點 + config false": "root nodes + config false",
    "已標記刪除整個": "Marked entire ", "節點；尚未送出": " node for deletion; not sent",
    "已標記刪除整個根節點；尚未送出。": "Marked the entire root node for deletion; not sent.",
    "目前無法執行設定操作；請確認已連線且沒有限時提交。": "Configuration operation unavailable; confirm that you are connected and no confirmed commit is active.",
    "請先連線並讀取目標 datastore；限時提交進行中不能載入。": "Connect and read the target datastore first; it cannot be loaded during a confirmed commit.",
    "表單已更新本機草稿；尚未送出。": "The form updated the local draft; it has not been sent.",
    "系統 SSH 修改只支援已讀取 running 的選取草稿。": "System SSH edits support only a selected draft read from running.",
    "事件訂閱：": "Event subscription: ", "已訂閱": "Subscribed", "未訂閱": "Not subscribed",
    "已訂閱": "Subscribed", "確認保留這次限時提交？不會保存 startup。": "Keep this confirmed commit? startup will not be saved.",
    "取消這次限時提交並要求 server 回復先前 running？": "Cancel this confirmed commit and ask the server to restore the previous running configuration?",
    "幾秒內必須確認保留？（30–600 秒）": "How many seconds to confirm? (30–600 seconds)",
    "確認限時提交…": "Confirm confirmed commit…", "取消限時提交…": "Cancel confirmed commit…",
    "尚無變更": "No changes", "個變更": " changes", "項變更": " changes", "項移除": " removals",
    "尚未送出；不會自動 commit 或保存 startup": "not sent; commit and save startup will not happen automatically",
    "尚未連線 · 選擇連線方式並填入認證資料": "Not connected · select a connection mode and enter authentication details",
    "（未讀到／可建立）": " (not read / creatable)", "（不完整，唯讀）": " (incomplete, read-only)",
    "（未提供）": " (not provided)", "（不存在）": " (does not exist)", "（移除）": " (removed)",
    "（新增根節點）": " (new root node)", "已存在": "already exists", "數量上限": "maximum count reached",
    "不完整": "incomplete", "待填": "pending", "未讀回": "not read back", "待確認": "pending confirmation",
    "未讀到": "not read", "可建立": "creatable", "未知": "unknown", "唯讀": "read-only",
}
_EN_PHRASES = tuple(sorted(_EN_PHRASES.items(), key=lambda item: len(item[0]), reverse=True))
_HANS_PHRASES = tuple(sorted(_HANS_PHRASES.items(), key=lambda item: len(item[0]), reverse=True))


def _replace(text, phrases):
    for source, target in phrases:
        text = text.replace(source, target)
    return text


def translate(text, language=None):
    if not isinstance(text, str):
        return text
    language = resolve_language(language or _current_language)
    if language == "zh-TW":
        return text
    if language == "zh-CN":
        return _replace(text, _HANS_PHRASES).translate(_HANS_CHARS)
    return _EN_EXACT.get(text, _replace(text, _EN_PHRASES))


def language_label(language):
    return translate(LANGUAGE_LABELS.get(resolve_language(language), "English"))


def retranslate_widget_tree(root):
    """Refresh widgets/actions that record their original source text."""
    try:
        from PySide6.QtWidgets import QMenu, QWidget
        widgets = [root, *root.findChildren(QWidget)]
        for widget in widgets:
            callback = getattr(widget, "_ncc_retranslate", None)
            if callback:
                callback()
        for menu in root.findChildren(QMenu):
            title = menu.property("_ncc_source_title")
            if title is not None:
                menu.setTitle(translate(str(title)))
            for action in menu.actions():
                source = action.property("_ncc_source_text")
                if source is not None:
                    action.setText(translate(str(source)))
    except Exception:
        # Language changes must never interfere with a live NETCONF session.
        return
