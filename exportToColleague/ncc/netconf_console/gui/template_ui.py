"""Template-library and clone entry points, reusing the existing creation form."""
from pathlib import Path
from tkinter import filedialog

from . import templates
from .creation_ui import CreationDialog
from .model import EditError, Selection, parse_editor


class TemplateFeatures:
    def _build_templates(self, tools):
        tools.add_separator()
        tools.add_command(label="複製所選 list 項目…", command=self.clone_list)
        tools.add_command(label="保存至個人範本庫（排除敏感值）…", command=self.save_personal_template)
        tools.add_command(label="開啟個人範本庫…", command=self.load_personal_template)

    def _template_directory(self):
        return self.preferences_store.path.parent / "templates" if self.preferences_store else Path.cwd()

    def _open_template(self, template):
        if not self._creation_ready() or not self._preserve_current_draft():
            return
        path = template.path[:-1]
        if not path:
            parent = self.snapshot.data
        else:
            if self.selection and self.selection.path == template.path and self.selection.ancestors:
                selected = Selection(self.selection.ancestors[-1], self.selection.ancestors[:-1])
            elif self.selection and self.selection.path == path:
                selected = self.selection
            else:
                raise EditError("請先選取範本的父節點（含正確 list instance）：" + "/".join(path))
            scope, source = self._draft_scope(), self.snapshot.options.source
            overlap = self.drafts.overlapping(scope, source, selected, self.client.schema)
            if overlap:
                raise EditError("與現有草稿重疊；請先處理草稿清單：" + overlap.label)
            self.selection, self.selection_iid = selected, None
            self.baseline_text = selected.text()
            self.editor.set(self.baseline_text)
            self._restore_selected_draft()
            self._update_preview()
            parent = parse_editor(self.editor.get())
        info = self.client.schema.lookup(template.path)
        return CreationDialog(self, parent, path, info, preset=template)

    def clone_list(self):
        if not self._creation_ready() or not self.selection:
            return
        try:
            info = self.client.schema.lookup(self.selection.path)
            if not info or info.kind != "list":
                raise EditError("請選取要複製的 list 項目，例如 netconf-client[name='client0']")
            template, removed = templates.from_node(self.client.schema, self.selection, parse_editor(self.editor.get()))
            dialog = self._open_template(template)
            if dialog:
                dialog.window.title("複製 list — 請重新填寫所有新 key")
                dialog.status.set("已排除 state／敏感值；須重填新 key。" + "；".join(removed[:8]))
        except Exception as exc:
            self._error(exc)

    def save_personal_template(self):
        if not self.selection or not self.snapshot or self.busy or self.lifecycle_dialog:
            return
        try:
            template, _ = templates.from_node(self.client.schema, self.selection, parse_editor(self.editor.get()))
            directory = self._template_directory()
            directory.mkdir(parents=True, exist_ok=True)
            name = filedialog.asksaveasfilename(parent=self.root, initialdir=str(directory),
                defaultextension=".ncctemplate", filetypes=[("加密個人範本", "*.ncctemplate")])
            if name:
                templates.save_template(name, template, self.client.schema)
                self.status.set("已保存個人範本；不含 state／密碼／私鑰內容，新 key 載入後須重填。")
        except Exception as exc:
            self._error(exc)

    def load_personal_template(self):
        if not self._creation_ready():
            self.status.set("請先連線、讀取 running/candidate 並載入完整 schema")
            return
        name = filedialog.askopenfilename(parent=self.root, initialdir=str(self._template_directory()),
            title="個人範本庫 — 選擇範本", filetypes=[("加密個人範本", "*.ncctemplate")])
        if name:
            try:
                return self._open_template(templates.load_template(name, self.client.schema))
            except Exception as exc:
                self._error(exc)
