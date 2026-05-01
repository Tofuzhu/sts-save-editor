# -*- coding: utf-8 -*-
import base64, json, os, re, shutil, struct, sys, zipfile
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from datetime import datetime

# ── Runtime directory ─────────────────────────────────────────────────────────
if getattr(sys, 'frozen', False):
    APP_DIR = os.path.dirname(sys.executable)
else:
    APP_DIR = os.path.dirname(os.path.abspath(__file__))

CONFIG_FILE = os.path.join(APP_DIR, "sts_editor_config.json")
CACHE_FILE  = os.path.join(APP_DIR, "sts_data_cache.json")
CACHE_VER   = 4

XOR_KEY = b"key"

# ── i18n ──────────────────────────────────────────────────────────────────────
LANG = 'zh'   # 'zh' | 'en'

def T(zh, en):
    return zh if LANG == 'zh' else en

# ── Game data constants ───────────────────────────────────────────────────────
CHAR_CATS_ORDER = [
    ('red',       '铁甲人', 'Ironclad'),
    ('green',     '猎手',   'Silent'),
    ('blue',      '机器人', 'Defect'),
    ('purple',    '守望者', 'Watcher'),
    ('colorless', '无色',   'Colorless'),
    ('curse',     '咒语',   'Curse'),
    ('status',    '状态',   'Status'),
]
CHAR_CATS = {row[0]: row[1] for row in CHAR_CATS_ORDER}  # kept for legacy use
CHAR_CAT_TO_FOLDER = {}
for _f, _zh, _en in CHAR_CATS_ORDER:
    CHAR_CAT_TO_FOLDER[_zh] = _f
    CHAR_CAT_TO_FOLDER[_en] = _f

CARD_TYPE_ORDER = [
    ('ATTACK', '攻击', 'Attack'),
    ('SKILL',  '技能', 'Skill'),
    ('POWER',  '异能', 'Power'),
    ('STATUS', '状态', 'Status'),
    ('CURSE',  '诅咒', 'Curse'),
]
_CARD_TYPES = {row[0] for row in CARD_TYPE_ORDER}
CARD_TYPE_TO_KEY = {}
for _k, _zh, _en in CARD_TYPE_ORDER:
    CARD_TYPE_TO_KEY[_zh] = _k
    CARD_TYPE_TO_KEY[_en] = _k

# ── Config ────────────────────────────────────────────────────────────────────

def load_config():
    try:
        with open(CONFIG_FILE, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}

def save_config(game_dir, lang=None):
    cfg = load_config()
    cfg['game_dir'] = game_dir
    if lang is not None:
        cfg['lang'] = lang
    with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)

def auto_detect_game():
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\Steam App 646570")
        path, _ = winreg.QueryValueEx(key, "InstallLocation")
        if os.path.isfile(os.path.join(path, "desktop-1.0.jar")):
            return path
    except Exception:
        pass
    drives = [d + ":\\" for d in "CDEFGHIJ"]
    suffixes = [
        r"SteamLibrary\steamapps\common\SlayTheSpire",
        r"Steam\steamapps\common\SlayTheSpire",
        r"Program Files (x86)\Steam\steamapps\common\SlayTheSpire",
        r"Program Files\Steam\steamapps\common\SlayTheSpire",
    ]
    for drv in drives:
        for suf in suffixes:
            p = os.path.join(drv, suf)
            if os.path.isfile(os.path.join(p, "desktop-1.0.jar")):
                return p
    return None

def validate_game_dir(path):
    return bool(path) and os.path.isfile(os.path.join(path, "desktop-1.0.jar"))

# ── Paths ─────────────────────────────────────────────────────────────────────
GAME_DIR  = ""
JAR_PATH  = ""
SAVES_DIR = ""

def set_paths(game_dir):
    global GAME_DIR, JAR_PATH, SAVES_DIR
    GAME_DIR  = game_dir
    JAR_PATH  = os.path.join(game_dir, "desktop-1.0.jar")
    SAVES_DIR = os.path.join(game_dir, "saves")

# ── Save encode/decode ────────────────────────────────────────────────────────

def decode_save(path):
    with open(path, "rb") as f:
        data = f.read()
    raw = base64.b64decode(data)
    return json.loads(bytes([b ^ XOR_KEY[i % 3] for i, b in enumerate(raw)]))

def encode_save(d):
    plain = json.dumps(d, separators=(",", ":")).encode("utf-8")
    xored = bytes([b ^ XOR_KEY[i % 3] for i, b in enumerate(plain)])
    return base64.b64encode(xored)

def backup_file(path):
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    dst = path + f".bak_{ts}"
    shutil.copy2(path, dst)
    return dst

def get_save_files():
    return sorted(
        n for n in os.listdir(SAVES_DIR)
        if n.endswith(".autosave") and not n.endswith(".backUp")
    )

# ── JAR parsing ───────────────────────────────────────────────────────────────

def _parse_cp_strings(data):
    if data[:4] != b'\xca\xfe\xba\xbe':
        return []
    cp_count = struct.unpack('>H', data[8:10])[0]
    pos, utf8, refs = 10, {}, []
    i = 1
    while i < cp_count:
        tag = data[pos]; pos += 1
        if tag == 1:
            n = struct.unpack('>H', data[pos:pos+2])[0]; pos += 2
            utf8[i] = data[pos:pos+n].decode('utf-8', errors='replace'); pos += n
        elif tag == 8:
            refs.append(struct.unpack('>H', data[pos:pos+2])[0]); pos += 2
        elif tag in (3, 4, 9, 10, 11, 12, 17, 18): pos += 4
        elif tag in (5, 6):                          pos += 8; i += 1
        elif tag in (7, 16, 19, 20):                 pos += 2
        elif tag == 15:                               pos += 3
        i += 1
    return [utf8[r] for r in refs if r in utf8]

_CP_SKIP = {
    'ConstantValue', 'Code', 'LineNumberTable', 'LocalVariableTable',
    'SourceFile', 'StackMapTable', 'InnerClasses', 'Signature', 'Exceptions',
    'Deprecated', 'this', 'data', 'bytes', 'name', 'type', 'key',
    'true', 'false', 'null',
    'RED', 'GREEN', 'BLUE', 'PURPLE', 'COLORLESS',
    'ATTACK', 'SKILL', 'POWER', 'STATUS', 'CURSE',
    'COMMON', 'UNCOMMON', 'RARE', 'BASIC', 'SPECIAL', 'Potion Slot',
}

def _first_id(data):
    for s in _parse_cp_strings(data):
        if s not in _CP_SKIP and not any(c in s for c in '/.(;<>[') and 2 <= len(s) <= 60:
            return s
    return None

def _card_type(data):
    for s in _parse_cp_strings(data):
        if s in _CARD_TYPES:
            return s
    return ''

def _clean(text, zh=False):
    if isinstance(text, list):
        text = ' '.join(str(t) for t in text)
    text = re.sub(r'#[a-zA-Z]', '', text or '')
    text = re.sub(r'!\w+!', '[数值]' if zh else '[X]', text)
    text = text.replace(' NL ', '\n').replace('NL', '\n')
    text = re.sub(r'\[E\]', '[能量]' if zh else '[Energy]', text)
    return re.sub(r'  +', ' ', text).strip()

# ── Game data loading (with cache) ────────────────────────────────────────────

def _jar_mtime():
    try:
        return os.path.getmtime(JAR_PATH)
    except OSError:
        return 0

def _load_cache():
    try:
        with open(CACHE_FILE, encoding='utf-8') as f:
            c = json.load(f)
        if c.get("version") == CACHE_VER and abs(c.get("jar_mtime", 0) - _jar_mtime()) < 1:
            return c
    except Exception:
        pass
    return None

def _save_cache(relics, potions, cards):
    try:
        with open(CACHE_FILE, 'w', encoding='utf-8') as f:
            json.dump({"version": CACHE_VER, "jar_mtime": _jar_mtime(),
                       "relics": relics, "potions": potions, "cards": cards},
                      f, ensure_ascii=False)
    except Exception:
        pass

def _parse_relics(jar, eng, zhs):
    paths = [f for f in jar.namelist()
             if f.startswith('com/megacrit/cardcrawl/relics/')
             and f.endswith('.class') and '$' not in f
             and not any(x in f for x in ('Abstract', 'DEPRECATED', 'Test', 'Derp'))]
    out, seen = [], set()
    for path in sorted(paths):
        eid = _first_id(jar.read(path))
        if eid and eid in eng and eid not in seen:
            seen.add(eid)
            e_en, e_zh = eng[eid], zhs.get(eid, {})
            out.append({'id': eid,
                        'name':      e_zh.get('NAME') or e_en.get('NAME', eid),
                        'name_en':   e_en.get('NAME', eid),
                        'desc':      _clean(e_zh.get('DESCRIPTIONS') or e_en.get('DESCRIPTIONS', []), zh=True),
                        'desc_en':   _clean(e_en.get('DESCRIPTIONS', [])),
                        'flavor':    e_zh.get('FLAVOR') or e_en.get('FLAVOR', ''),
                        'flavor_en': e_en.get('FLAVOR', '')})
    for eid, e_en in eng.items():
        if eid not in seen:
            e_zh = zhs.get(eid, {})
            out.append({'id': eid,
                        'name':      e_zh.get('NAME') or e_en.get('NAME', eid),
                        'name_en':   e_en.get('NAME', eid),
                        'desc':      _clean(e_zh.get('DESCRIPTIONS') or e_en.get('DESCRIPTIONS', []), zh=True),
                        'desc_en':   _clean(e_en.get('DESCRIPTIONS', [])),
                        'flavor':    e_zh.get('FLAVOR') or e_en.get('FLAVOR', ''),
                        'flavor_en': e_en.get('FLAVOR', '')})
    out.sort(key=lambda r: r['name'].lower())
    return out

def _parse_potions(jar, eng, zhs):
    paths = [f for f in jar.namelist()
             if f.startswith('com/megacrit/cardcrawl/potions/')
             and f.endswith('.class') and '$' not in f and 'Abstract' not in f]
    out, seen = [], set()
    for path in sorted(paths):
        eid = _first_id(jar.read(path))
        if eid and eid != 'Potion Slot' and eid in eng and eid not in seen:
            seen.add(eid)
            e_en, e_zh = eng[eid], zhs.get(eid, {})
            out.append({'id': eid,
                        'name':    e_zh.get('NAME') or e_en.get('NAME', eid),
                        'name_en': e_en.get('NAME', eid),
                        'desc':    _clean(e_zh.get('DESCRIPTIONS') or e_en.get('DESCRIPTIONS', []), zh=True),
                        'desc_en': _clean(e_en.get('DESCRIPTIONS', []))})
    out.sort(key=lambda r: r['name'].lower())
    return out

def _parse_cards(jar, eng, zhs):
    out, seen = [], set()
    for path in sorted(jar.namelist()):
        m = re.match(r'com/megacrit/cardcrawl/cards/(\w+)/\w+\.class', path)
        if not m or '$' in path or m.group(1) in ('CardGroup', 'CardSave'):
            continue
        folder   = m.group(1)
        cls_data = jar.read(path)
        cid      = _first_id(cls_data)
        if cid and cid in eng and cid not in seen:
            seen.add(cid)
            e_en, e_zh = eng[cid], zhs.get(cid, {})
            out.append({'id': cid,
                        'name':       e_zh.get('NAME') or e_en.get('NAME', cid),
                        'name_en':    e_en.get('NAME', cid),
                        'desc':       _clean(e_zh.get('DESCRIPTION', '') or e_en.get('DESCRIPTION', ''), zh=True),
                        'desc_en':    _clean(e_en.get('DESCRIPTION', '')),
                        'desc_up':    _clean(e_zh.get('UPGRADE_DESCRIPTION', '') or e_en.get('UPGRADE_DESCRIPTION', ''), zh=True),
                        'desc_up_en': _clean(e_en.get('UPGRADE_DESCRIPTION', '')),
                        'category':   folder,
                        'type':       _card_type(cls_data)})
    out.sort(key=lambda r: r['name'].lower())
    return out

def load_all_data():
    cached = _load_cache()
    if cached:
        return cached['relics'], cached['potions'], cached['cards']
    jar = zipfile.ZipFile(JAR_PATH)
    def j(path): return json.loads(jar.read(path).decode('utf-8'))
    relics  = _parse_relics (jar, j('localization/eng/relics.json'),  j('localization/zhs/relics.json'))
    potions = _parse_potions(jar, j('localization/eng/potions.json'), j('localization/zhs/potions.json'))
    cards   = _parse_cards  (jar, j('localization/eng/cards.json'),   j('localization/zhs/cards.json'))
    _save_cache(relics, potions, cards)
    return relics, potions, cards

# ── GUI helpers ───────────────────────────────────────────────────────────────

def _make_tree(parent, cols, headings, widths, height=16):
    f = ttk.Frame(parent)
    f.columnconfigure(0, weight=1); f.rowconfigure(0, weight=1)
    tv = ttk.Treeview(f, columns=cols, show="headings", selectmode="browse", height=height)
    for col, hd, w in zip(cols, headings, widths):
        tv.heading(col, text=hd); tv.column(col, width=w, stretch=True)
    tv.grid(row=0, column=0, sticky="nsew")
    sb = ttk.Scrollbar(f, orient="vertical", command=tv.yview)
    sb.grid(row=0, column=1, sticky="ns")
    tv.configure(yscrollcommand=sb.set)
    return f, tv

def _make_listbox(parent, width=22, height=16):
    f = ttk.Frame(parent)
    f.columnconfigure(0, weight=1); f.rowconfigure(0, weight=1)
    lb = tk.Listbox(f, width=width, height=height, selectmode="browse", activestyle="dotbox")
    lb.grid(row=0, column=0, sticky="nsew")
    sb = ttk.Scrollbar(f, orient="vertical", command=lb.yview)
    sb.grid(row=0, column=1, sticky="ns")
    lb.configure(yscrollcommand=sb.set)
    return f, lb

def _make_desc(parent, height=4):
    lf = ttk.LabelFrame(parent, text=T("描述", "Description"))
    t = tk.Text(lf, height=height, wrap="word", state="disabled", relief="flat")
    t.pack(fill="x", padx=4, pady=4)
    return lf, t

def _set_desc(widget, text):
    widget.config(state="normal")
    widget.delete("1.0", "end")
    if text: widget.insert("end", text)
    widget.config(state="disabled")

# ── Setup dialog ──────────────────────────────────────────────────────────────

class SetupDialog(tk.Toplevel):
    def __init__(self, master, detected=None):
        super().__init__(master)
        self.title(T("设置游戏目录", "Game Directory Setup"))
        self.resizable(False, False)
        self.result = None
        self.grab_set()

        ttk.Label(self, text=T("请选择杀戮尖塔游戏目录", "Select Slay the Spire directory"),
                  font=("", 11, "bold")).pack(padx=20, pady=(16, 4))
        ttk.Label(self, text=T("（含 desktop-1.0.jar 的文件夹）",
                               "(folder containing desktop-1.0.jar)"),
                  foreground="gray").pack(padx=20, pady=(0, 12))

        row = ttk.Frame(self); row.pack(fill="x", padx=20)
        self._path_var = tk.StringVar(value=detected or "")
        ttk.Entry(row, textvariable=self._path_var, width=44).pack(side="left", padx=(0, 6))
        ttk.Button(row, text=T("浏览…", "Browse…"), command=self._browse).pack(side="left")

        self._status = tk.StringVar()
        self._status_lbl = ttk.Label(self, textvariable=self._status, foreground="gray")
        self._status_lbl.pack(padx=20, pady=6)

        btns = ttk.Frame(self); btns.pack(pady=(0, 16))
        ttk.Button(btns, text=T("确认", "Confirm"), command=self._confirm, width=10).pack(side="left", padx=6)
        ttk.Button(btns, text=T("退出", "Exit"),    command=self.destroy,  width=10).pack(side="left", padx=6)

        self._path_var.trace_add("write", self._validate)
        self._validate()
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.update_idletasks()
        x = master.winfo_screenwidth()  // 2 - self.winfo_width()  // 2
        y = master.winfo_screenheight() // 2 - self.winfo_height() // 2
        self.geometry(f"+{x}+{y}")

    def _browse(self):
        d = filedialog.askdirectory(title=T("选择游戏目录", "Select game directory"))
        if d: self._path_var.set(d)

    def _validate(self, *_):
        p = self._path_var.get().strip()
        if validate_game_dir(p):
            self._status.set(T("✓ 找到 desktop-1.0.jar", "✓ Found desktop-1.0.jar"))
            self._status_lbl.config(foreground="green")
        elif p:
            self._status.set(T("✗ 未找到 desktop-1.0.jar", "✗ desktop-1.0.jar not found"))
            self._status_lbl.config(foreground="red")
        else:
            self._status.set("")

    def _confirm(self):
        p = self._path_var.get().strip()
        if not validate_game_dir(p):
            messagebox.showerror(T("错误", "Error"),
                                 T("所选目录无效，未找到 desktop-1.0.jar",
                                   "Invalid directory: desktop-1.0.jar not found"), parent=self)
            return
        self.result = p
        self.destroy()


def get_or_setup_game_dir():
    cfg = load_config()
    game_dir = cfg.get("game_dir", "")
    if validate_game_dir(game_dir):
        return game_dir
    detected = auto_detect_game()
    if detected:
        save_config(detected)
        return detected
    root = tk.Tk(); root.withdraw()
    dlg = SetupDialog(root, detected)
    root.wait_window(dlg)
    result = dlg.result
    root.destroy()
    if result:
        save_config(result)
    return result

# ── Main UI ───────────────────────────────────────────────────────────────────

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(T("杀戮尖塔存档编辑器", "Slay the Spire Save Editor"))
        self.minsize(960, 640)
        self.save_data = None
        self.save_path = None
        self.all_relics = self.all_potions = self.all_cards = []
        self._relic_idx = {}; self._potion_idx = {}; self._card_idx = {}
        self._deck_label_var = tk.StringVar(value=T("当前牌组", "Current Deck"))
        self._i18n_vars    = []   # (StringVar, zh, en)
        self._i18n_lframes = []   # (LabelFrame, zh, en)
        self._i18n_treecols = []  # (tree, col, zh, en)

        self._build_ui()
        self._load_all_libraries()
        self._load_file_list()

    # ── i18n ─────────────────────────────────────────────────────────────────

    def _tv(self, zh, en):
        v = tk.StringVar(value=T(zh, en))
        self._i18n_vars.append((v, zh, en))
        return v

    def _reg_lf(self, widget, zh, en):
        self._i18n_lframes.append((widget, zh, en))
        return widget

    def _apply_lang(self):
        self.title(T("杀戮尖塔存档编辑器", "Slay the Spire Save Editor"))
        for v, zh, en in self._i18n_vars:
            v.set(T(zh, en))
        for w, zh, en in self._i18n_lframes:
            w.config(text=T(zh, en))
        for tree, col, zh, en in self._i18n_treecols:
            tree.heading(col, text=T(zh, en))
        for i, (zh, en) in enumerate([
            ("  基本数值  ", "  Stats  "),
            ("  遗物管理  ", "  Relics  "),
            ("  药水管理  ", "  Potions  "),
            ("  牌组管理  ", "  Cards  "),
        ]):
            self._nb.tab(i, text=T(zh, en))
        self._update_filter_values()
        self._fill_card_tree(self._current_card_filter())
        n = len((self.save_data or {}).get("cards", []))
        self._deck_label_var.set(
            T(f"当前牌组 ({n}张)", f"Current Deck ({n})") if self.save_data
            else T("当前牌组", "Current Deck"))

    def _update_filter_values(self):
        all_val   = T("全部", "All")
        cat_vals  = [all_val] + [T(zh, en) for _, zh, en in CHAR_CATS_ORDER]
        type_vals = [all_val] + [T(zh, en) for _, zh, en in CARD_TYPE_ORDER]
        if self._card_cat_var.get() not in cat_vals:
            self._card_cat_var.set(all_val)
        if self._card_type_var.get() not in type_vals:
            self._card_type_var.set(all_val)
        self._cat_cb['values']  = cat_vals
        self._type_cb['values'] = type_vals

    def _toggle_lang(self):
        global LANG
        LANG = 'en' if LANG == 'zh' else 'zh'
        self._lang_btn.config(text='EN' if LANG == 'zh' else 'ZH')
        save_config(GAME_DIR, LANG)
        self._apply_lang()
        if self.save_data:
            self._refresh_relic_list()
            self._refresh_potion_slots()
            self._refresh_deck()

    def _cat_display(self, folder):
        for f, zh, en in CHAR_CATS_ORDER:
            if f == folder:
                return T(zh, en)
        return folder

    # ── Layout ────────────────────────────────────────────────────────────────

    def _build_ui(self):
        self.columnconfigure(0, weight=1); self.rowconfigure(1, weight=1)

        bar = ttk.Frame(self, padding=(8, 6))
        bar.grid(row=0, column=0, sticky="ew")
        ttk.Label(bar, textvariable=self._tv("存档文件：", "Save file:")).pack(side="left")
        self.file_var = tk.StringVar()
        self.file_combo = ttk.Combobox(bar, textvariable=self.file_var, state="readonly", width=26)
        self.file_combo.pack(side="left", padx=(0, 6))
        self.file_combo.bind("<<ComboboxSelected>>", self._on_file_selected)
        ttk.Button(bar, textvariable=self._tv("刷新", "Refresh"),
                   command=self._load_file_list).pack(side="left")
        ttk.Button(bar, textvariable=self._tv("切换目录", "Change Dir"),
                   command=self._change_dir).pack(side="left", padx=(8, 0))
        self._lang_btn = ttk.Button(bar, text="EN", width=4, command=self._toggle_lang)
        self._lang_btn.pack(side="left", padx=(8, 0))
        self._status = tk.StringVar(value=T("正在加载游戏数据…", "Loading game data…"))
        ttk.Label(bar, textvariable=self._status, foreground="gray").pack(side="right", padx=8)

        self._nb = ttk.Notebook(self, padding=(6, 0, 6, 6))
        self._nb.grid(row=1, column=0, sticky="nsew")
        self._build_tab_stats(self._nb)
        self._build_tab_relics(self._nb)
        self._build_tab_potions(self._nb)
        self._build_tab_cards(self._nb)

    # ── Tab 1: Stats ──────────────────────────────────────────────────────────

    def _build_tab_stats(self, nb):
        tab = ttk.Frame(nb, padding=10)
        nb.add(tab, text=T("  基本数值  ", "  Stats  "))
        tab.columnconfigure(0, weight=1); tab.columnconfigure(1, weight=1)

        info = self._reg_lf(
            ttk.LabelFrame(tab, text=T("存档信息（只读）", "Save Info (read-only)")),
            "存档信息（只读）", "Save Info (read-only)")
        info.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        self._info_vars = {}
        for i, (zh, en, key) in enumerate([
            ("角色",    "Character",  "name"),
            ("第几幕",  "Act",        "act_num"),
            ("楼层",    "Floor",      "floor_num"),
            ("当前 HP", "Current HP", "current_health"),
            ("最大 HP", "Max HP",     "max_health"),
        ]):
            c = (i % 3) * 2
            ttk.Label(info, textvariable=self._tv(zh + "：", en + ":"),
                      width=11, anchor="e").grid(row=i // 3, column=c, padx=(12, 2), pady=3, sticky="e")
            v = tk.StringVar(value="—")
            ttk.Label(info, textvariable=v, width=14, anchor="w").grid(
                row=i // 3, column=c + 1, pady=3, sticky="w")
            self._info_vars[key] = v

        nums = self._reg_lf(
            ttk.LabelFrame(tab, text=T("数值修改", "Edit Values")),
            "数值修改", "Edit Values")
        nums.grid(row=1, column=0, sticky="nsew", padx=(0, 6), pady=(0, 8))
        self._edit_vars, self._edit_entries = {}, {}
        for i, (zh, en, key) in enumerate([
            ("金币",     "Gold",         "gold"),
            ("最大 HP",  "Max HP",       "max_health"),
            ("当前 HP",  "Current HP",   "current_health"),
            ("手牌上限", "Hand Size",    "hand_size"),
            ("药水槽数", "Potion Slots", "potion_slots"),
            ("净化费用", "Purge Cost",   "purgeCost"),
        ]):
            ttk.Label(nums, textvariable=self._tv(zh + "：", en + ":"),
                      width=12, anchor="e").grid(row=i, column=0, padx=(8, 2), pady=4, sticky="e")
            v = tk.StringVar()
            e = ttk.Entry(nums, textvariable=v, width=12, state="disabled")
            e.grid(row=i, column=1, pady=4, padx=(0, 12), sticky="w")
            self._edit_vars[key] = v; self._edit_entries[key] = e

        switches = self._reg_lf(
            ttk.LabelFrame(tab, text=T("开关 / 钥匙", "Keys & Switches")),
            "开关 / 钥匙", "Keys & Switches")
        switches.grid(row=1, column=1, sticky="nsew", pady=(0, 8))
        self._key_vars = {}
        for i, (zh, en, key) in enumerate([
            ("红钥匙", "Ruby Key",     "has_ruby_key"),
            ("绿钥匙", "Emerald Key",  "has_emerald_key"),
            ("蓝钥匙", "Sapphire Key", "has_sapphire_key"),
        ]):
            v = tk.BooleanVar()
            cb = ttk.Checkbutton(switches, textvariable=self._tv(zh, en),
                                  variable=v, state="disabled")
            cb.grid(row=i, column=0, sticky="w", padx=12, pady=4)
            self._key_vars[key] = (v, cb)

        ttk.Separator(switches, orient="horizontal").grid(
            row=3, column=0, sticky="ew", padx=8, pady=6)
        ttk.Label(switches, textvariable=self._tv("灼热模式：", "Ascension Mode:")).grid(
            row=4, column=0, sticky="w", padx=12)
        self._ascension_var = tk.BooleanVar()
        self._ascension_cb = ttk.Checkbutton(
            switches, textvariable=self._tv("开启", "Enabled"),
            variable=self._ascension_var, state="disabled")
        self._ascension_cb.grid(row=5, column=0, sticky="w", padx=12, pady=2)
        ttk.Label(switches, textvariable=self._tv("灼热等级：", "Ascension Level:")).grid(
            row=6, column=0, sticky="w", padx=12)
        self._ascension_level = tk.StringVar(value="0")
        self._ascension_spin = ttk.Spinbox(switches, from_=0, to=20, width=6,
                                            textvariable=self._ascension_level, state="disabled")
        self._ascension_spin.grid(row=7, column=0, sticky="w", padx=12, pady=2)

        self.btn_stats_save = ttk.Button(
            tab, textvariable=self._tv("保存以上修改", "Save Changes"),
            command=self._save_stats, state="disabled")
        self.btn_stats_save.grid(row=2, column=0, columnspan=2, pady=4)

    # ── Tab 2: Relics ─────────────────────────────────────────────────────────

    def _build_tab_relics(self, nb):
        tab = ttk.Frame(nb, padding=10)
        nb.add(tab, text=T("  遗物管理  ", "  Relics  "))
        tab.columnconfigure(0, weight=3); tab.columnconfigure(2, weight=1); tab.rowconfigure(1, weight=1)

        sf = ttk.Frame(tab); sf.grid(row=0, column=0, sticky="ew", pady=(0, 2))
        ttk.Label(sf, textvariable=self._tv("遗物库  搜索：", "Relic Library  Search:")).pack(side="left")
        self._relic_search = tk.StringVar()
        self._relic_search.trace_add("write", self._on_relic_search)
        ttk.Entry(sf, textvariable=self._relic_search, width=22).pack(side="left")
        ttk.Button(sf, text="✕", width=2,
                   command=lambda: self._relic_search.set("")).pack(side="left", padx=2)

        lf, self.relic_lib_tree = _make_tree(
            tab, ("name", "name_en"),
            (T("中文名称", "ZH Name"), "EN Name"), (160, 160))
        self._i18n_treecols.append((self.relic_lib_tree, "name", "中文名称", "ZH Name"))
        lf.grid(row=1, column=0, sticky="nsew")
        self.relic_lib_tree.bind("<<TreeviewSelect>>", self._on_relic_lib_select)

        mid = ttk.Frame(tab, padding=(6, 0)); mid.grid(row=1, column=1, sticky="ns")
        mid.rowconfigure(0, weight=1)
        ba = ttk.Frame(mid); ba.grid(row=0, column=0)
        self.btn_relic_add    = ttk.Button(ba, textvariable=self._tv("添加 →", "Add →"),
                                            width=10, state="disabled", command=self._add_relic)
        self.btn_relic_remove = ttk.Button(ba, textvariable=self._tv("← 移除", "← Remove"),
                                            width=10, state="disabled", command=self._remove_relic)
        self.btn_relic_save   = ttk.Button(ba, textvariable=self._tv("保存遗物", "Save Relics"),
                                            width=10, state="disabled",
                                            command=lambda: self._do_write(T("遗物", "Relics")))
        self.btn_relic_add.pack(pady=6); self.btn_relic_remove.pack(pady=6)
        self.btn_relic_save.pack(pady=(40, 6))

        ttk.Label(tab, textvariable=self._tv("存档当前遗物", "Current Relics")).grid(
            row=0, column=2, sticky="w")
        cf, self.relic_cur_list = _make_listbox(tab, width=26)
        cf.grid(row=1, column=2, sticky="nsew")
        self.relic_cur_list.bind("<<ListboxSelect>>", self._on_relic_cur_select)

        dlf, self._relic_desc = _make_desc(tab, height=4)
        self._reg_lf(dlf, "描述", "Description")
        dlf.grid(row=2, column=0, columnspan=3, sticky="ew", pady=(6, 0))

    # ── Tab 3: Potions ────────────────────────────────────────────────────────

    def _build_tab_potions(self, nb):
        tab = ttk.Frame(nb, padding=10)
        nb.add(tab, text=T("  药水管理  ", "  Potions  "))
        tab.columnconfigure(0, weight=2); tab.columnconfigure(2, weight=1); tab.rowconfigure(1, weight=1)

        sf = ttk.Frame(tab); sf.grid(row=0, column=0, sticky="ew", pady=(0, 2))
        ttk.Label(sf, textvariable=self._tv("药水库  搜索：", "Potion Library  Search:")).pack(side="left")
        self._potion_search = tk.StringVar()
        self._potion_search.trace_add("write", self._on_potion_search)
        ttk.Entry(sf, textvariable=self._potion_search, width=22).pack(side="left")
        ttk.Button(sf, text="✕", width=2,
                   command=lambda: self._potion_search.set("")).pack(side="left", padx=2)

        lf, self.potion_lib_tree = _make_tree(
            tab, ("name", "name_en"),
            (T("中文名称", "ZH Name"), "EN Name"), (150, 150))
        self._i18n_treecols.append((self.potion_lib_tree, "name", "中文名称", "ZH Name"))
        lf.grid(row=1, column=0, sticky="nsew")
        self.potion_lib_tree.bind("<<TreeviewSelect>>", self._on_potion_lib_select)

        mid = ttk.Frame(tab, padding=(6, 0)); mid.grid(row=1, column=1, sticky="ns")
        mid.rowconfigure(0, weight=1)
        ba = ttk.Frame(mid); ba.grid(row=0, column=0)
        self.btn_potion_set   = ttk.Button(ba, textvariable=self._tv("放入槽 →", "Set Slot →"),
                                            width=10, state="disabled", command=self._set_potion)
        self.btn_potion_clear = ttk.Button(ba, textvariable=self._tv("← 清空槽", "← Clear Slot"),
                                            width=10, state="disabled", command=self._clear_potion)
        self.btn_potion_save  = ttk.Button(ba, textvariable=self._tv("保存药水", "Save Potions"),
                                            width=10, state="disabled",
                                            command=lambda: self._do_write(T("药水", "Potions")))
        self.btn_potion_set.pack(pady=6); self.btn_potion_clear.pack(pady=6)
        self.btn_potion_save.pack(pady=(40, 6))

        ttk.Label(tab, textvariable=self._tv("当前药水槽", "Potion Slots")).grid(
            row=0, column=2, sticky="w")
        cf, self.potion_slot_list = _make_listbox(tab, width=28)
        cf.grid(row=1, column=2, sticky="nsew")
        self.potion_slot_list.bind("<<ListboxSelect>>", self._on_potion_slot_select)

        dlf, self._potion_desc = _make_desc(tab, height=4)
        self._reg_lf(dlf, "描述", "Description")
        dlf.grid(row=2, column=0, columnspan=3, sticky="ew", pady=(6, 0))

    # ── Tab 4: Cards ──────────────────────────────────────────────────────────

    def _build_tab_cards(self, nb):
        tab = ttk.Frame(nb, padding=10)
        nb.add(tab, text=T("  牌组管理  ", "  Cards  "))
        tab.columnconfigure(0, weight=3); tab.columnconfigure(2, weight=1); tab.rowconfigure(1, weight=1)

        ff = ttk.Frame(tab); ff.grid(row=0, column=0, sticky="ew", pady=(0, 2))
        ttk.Label(ff, textvariable=self._tv("职业：", "Class:")).pack(side="left")
        self._card_cat_var = tk.StringVar(value=T("全部", "All"))
        self._cat_cb = ttk.Combobox(ff, textvariable=self._card_cat_var, state="readonly", width=9,
                                     values=[T("全部", "All")] + [T(zh, en) for _, zh, en in CHAR_CATS_ORDER])
        self._cat_cb.pack(side="left", padx=(0, 8))
        self._cat_cb.bind("<<ComboboxSelected>>", self._on_card_filter)

        ttk.Label(ff, textvariable=self._tv("类型：", "Type:")).pack(side="left")
        self._card_type_var = tk.StringVar(value=T("全部", "All"))
        self._type_cb = ttk.Combobox(ff, textvariable=self._card_type_var, state="readonly", width=7,
                                      values=[T("全部", "All")] + [T(zh, en) for _, zh, en in CARD_TYPE_ORDER])
        self._type_cb.pack(side="left", padx=(0, 8))
        self._type_cb.bind("<<ComboboxSelected>>", self._on_card_filter)

        ttk.Label(ff, textvariable=self._tv("搜索：", "Search:")).pack(side="left")
        self._card_search = tk.StringVar()
        self._card_search.trace_add("write", self._on_card_filter)
        ttk.Entry(ff, textvariable=self._card_search, width=22).pack(side="left")
        ttk.Button(ff, text="✕", width=2,
                   command=lambda: self._card_search.set("")).pack(side="left", padx=2)

        lf, self.card_lib_tree = _make_tree(
            tab, ("name", "name_en", "cat"),
            (T("中文名称", "ZH Name"), "EN Name", T("职业", "Class")), (150, 150, 70))
        self._i18n_treecols.append((self.card_lib_tree, "name", "中文名称", "ZH Name"))
        self._i18n_treecols.append((self.card_lib_tree, "cat",  "职业",    "Class"))
        lf.grid(row=1, column=0, sticky="nsew")
        self.card_lib_tree.bind("<<TreeviewSelect>>", self._on_card_lib_select)

        mid = ttk.Frame(tab, padding=(6, 0)); mid.grid(row=1, column=1, sticky="ns")
        mid.rowconfigure(0, weight=1)
        ba = ttk.Frame(mid); ba.grid(row=0, column=0)
        ttk.Label(ba, textvariable=self._tv("升级次数：", "Upgrades:")).pack()
        self._card_upgrade_var = tk.StringVar(value="0")
        ttk.Spinbox(ba, from_=0, to=20, width=5, textvariable=self._card_upgrade_var).pack(pady=(0, 4))
        self.btn_card_add    = ttk.Button(ba, textvariable=self._tv("添加 →",  "Add →"),
                                           width=10, state="disabled", command=self._add_card)
        self.btn_card_remove = ttk.Button(ba, textvariable=self._tv("← 移除", "← Remove"),
                                           width=10, state="disabled", command=self._remove_card)
        self.btn_card_add.pack(pady=4); self.btn_card_remove.pack(pady=4)
        ttk.Separator(ba, orient="horizontal").pack(fill="x", pady=8)
        ttk.Label(ba, textvariable=self._tv("选中的牌：", "Selected:")).pack()
        self.btn_card_up = ttk.Button(ba, textvariable=self._tv("升级 +1", "Upgrade +1"),
                                       width=10, state="disabled", command=self._upgrade_card)
        self.btn_card_dn = ttk.Button(ba, textvariable=self._tv("降级 -1", "Downgrade -1"),
                                       width=10, state="disabled", command=self._downgrade_card)
        self.btn_card_up.pack(pady=4); self.btn_card_dn.pack(pady=4)
        self.btn_card_save = ttk.Button(ba, textvariable=self._tv("保存牌组", "Save Deck"),
                                         width=10, state="disabled",
                                         command=lambda: self._do_write(T("牌组", "Deck")))
        self.btn_card_save.pack(pady=(20, 4))

        ttk.Label(tab, textvariable=self._deck_label_var).grid(row=0, column=2, sticky="w")
        cf, self.card_deck_list = _make_listbox(tab, width=26)
        cf.grid(row=1, column=2, sticky="nsew")
        self.card_deck_list.bind("<<ListboxSelect>>", self._on_deck_select)

        dlf, self._card_desc = _make_desc(tab, height=5)
        self._reg_lf(dlf, "描述", "Description")
        dlf.grid(row=2, column=0, columnspan=3, sticky="ew", pady=(6, 0))

    # ── Data loading ──────────────────────────────────────────────────────────

    def _load_all_libraries(self):
        try:
            cached = _load_cache()
            if cached:
                self._status.set(T("从缓存加载完成", "Loaded from cache"))
            else:
                self._status.set(T("首次加载，解析游戏数据（约10秒）…",
                                    "First run: parsing game data (~10s)…"))
                self.update()
            self.all_relics, self.all_potions, self.all_cards = load_all_data()
        except Exception as e:
            messagebox.showwarning(T("数据加载失败", "Data Load Failed"), str(e))
            self.all_relics = self.all_potions = self.all_cards = []

        self._fill_relic_tree(self.all_relics)
        self._fill_potion_tree(self.all_potions)
        self._fill_card_tree(self.all_cards)
        self._relic_idx  = {r['id']: r for r in self.all_relics}
        self._potion_idx = {p['id']: p for p in self.all_potions}
        self._card_idx   = {c['id']: c for c in self.all_cards}
        self._status.set(T("就绪", "Ready"))

    def _fill_relic_tree(self, items):
        self.relic_lib_tree.delete(*self.relic_lib_tree.get_children())
        for r in items:
            self.relic_lib_tree.insert("", "end", iid=r['id'],
                                       values=(r['name'], r.get('name_en', '')))

    def _fill_potion_tree(self, items):
        self.potion_lib_tree.delete(*self.potion_lib_tree.get_children())
        for p in items:
            self.potion_lib_tree.insert("", "end", iid=p['id'],
                                        values=(p['name'], p.get('name_en', '')))

    def _fill_card_tree(self, items):
        self.card_lib_tree.delete(*self.card_lib_tree.get_children())
        for c in items:
            self.card_lib_tree.insert("", "end", iid=c['id'],
                                      values=(c['name'], c.get('name_en', ''),
                                              self._cat_display(c.get('category', ''))))

    def _current_card_filter(self):
        all_val    = T("全部", "All")
        cat_folder = CHAR_CAT_TO_FOLDER.get(self._card_cat_var.get())
        type_key   = CARD_TYPE_TO_KEY.get(self._card_type_var.get(), '')
        kw         = self._card_search.get().lower()
        return [c for c in self.all_cards
                if (self._card_cat_var.get() == all_val or c.get('category') == cat_folder)
                and (not type_key or c.get('type') == type_key)
                and (not kw or kw in c['name'].lower()
                     or kw in c.get('name_en', '').lower() or kw in c['desc'].lower())]

    def _load_file_list(self):
        try:
            files = get_save_files()
        except Exception:
            files = []
        self.file_combo["values"] = files
        if files:
            self.file_combo.current(0)
            self._on_file_selected()
        else:
            self._status.set(T("未找到存档文件", "No save files found"))

    def _on_file_selected(self, _=None):
        name = self.file_var.get()
        if not name: return
        path = os.path.join(SAVES_DIR, name)
        try:
            self.save_data = decode_save(path)
            self.save_path = path
        except Exception as e:
            messagebox.showerror(T("读取失败", "Load Failed"), str(e)); return

        sd = self.save_data
        for key in ("name", "act_num", "floor_num", "current_health", "max_health"):
            self._info_vars[key].set(str(sd.get(key, "—")))
        for key in ("gold", "max_health", "current_health", "hand_size", "potion_slots", "purgeCost"):
            self._edit_vars[key].set(str(sd.get(key, 0)))
            self._edit_entries[key].config(state="normal")
        for key, (v, cb) in self._key_vars.items():
            v.set(bool(sd.get(key, False))); cb.config(state="normal")
        self._ascension_var.set(bool(sd.get("is_ascension_mode", False)))
        self._ascension_level.set(str(sd.get("ascension_level", 0)))
        self._ascension_cb.config(state="normal"); self._ascension_spin.config(state="normal")

        for btn in (self.btn_stats_save, self.btn_relic_add, self.btn_relic_save,
                    self.btn_potion_save, self.btn_card_add, self.btn_card_save):
            btn.config(state="normal")

        self._refresh_relic_list()
        self._refresh_potion_slots()
        self._refresh_deck()
        self._status.set(T(f"已加载：{name}", f"Loaded: {name}"))

    def _change_dir(self):
        dlg = SetupDialog(self, GAME_DIR)
        self.wait_window(dlg)
        if dlg.result:
            save_config(dlg.result)
            set_paths(dlg.result)
            try: os.remove(CACHE_FILE)
            except OSError: pass
            self._load_all_libraries()
            self._load_file_list()

    # ── Relic logic ───────────────────────────────────────────────────────────

    def _refresh_relic_list(self):
        self.relic_cur_list.delete(0, "end")
        for rid in (self.save_data or {}).get("relics", []):
            r = self._relic_idx.get(rid)
            name = (r['name_en'] if LANG == 'en' else r['name']) if r else rid
            self.relic_cur_list.insert("end", f"{name}  ({rid})" if r else rid)

    def _on_relic_search(self, *_):
        kw = self._relic_search.get().lower()
        self._fill_relic_tree([r for r in self.all_relics
            if not kw or kw in r['name'].lower()
            or kw in r.get('name_en', '').lower() or kw in r['desc'].lower()])

    def _on_relic_lib_select(self, _=None):
        sel = self.relic_lib_tree.selection()
        if sel: self._show_relic_desc(sel[0])

    def _on_relic_cur_select(self, _=None):
        idx = self.relic_cur_list.curselection()
        if not idx or not self.save_data: return
        relics = self.save_data.get("relics", [])
        if idx[0] < len(relics):
            self._show_relic_desc(relics[idx[0]])
            self.btn_relic_remove.config(state="normal")

    def _show_relic_desc(self, rid):
        r = self._relic_idx.get(rid)
        if not r: return
        name   = r['name_en'] if LANG == 'en' else r['name']
        other  = r['name'] if LANG == 'en' else r.get('name_en', '')
        header = name if not other or other == name else f"{name}  /  {other}"
        desc   = r.get('desc_en', r['desc']) if LANG == 'en' else r['desc']
        flavor = r.get('flavor_en', '') if LANG == 'en' else r.get('flavor', '')
        text   = f"[{header}]\n{desc}"
        if flavor: text += f"\n\n{flavor}"
        _set_desc(self._relic_desc, text)

    def _add_relic(self):
        sel = self.relic_lib_tree.selection()
        if not sel or not self.save_data: return
        rid = sel[0]
        relics   = self.save_data.setdefault("relics", [])
        counters = self.save_data.setdefault("relic_counters", [])
        if rid in relics:
            messagebox.showinfo(T("提示", "Info"),
                                T(f"存档中已有：{rid}", f"Already in save: {rid}")); return
        relics.append(rid); counters.append(-1)
        self._refresh_relic_list()

    def _remove_relic(self):
        idx = self.relic_cur_list.curselection()
        if not idx or not self.save_data: return
        i = idx[0]
        relics   = self.save_data.get("relics", [])
        counters = self.save_data.get("relic_counters", [])
        if i >= len(relics): return
        relics.pop(i)
        if i < len(counters): counters.pop(i)
        self.btn_relic_remove.config(state="disabled")
        _set_desc(self._relic_desc, "")
        self._refresh_relic_list()

    # ── Potion logic ──────────────────────────────────────────────────────────

    def _refresh_potion_slots(self):
        self.potion_slot_list.delete(0, "end")
        for i, pid in enumerate((self.save_data or {}).get("potions", [])):
            p = self._potion_idx.get(pid)
            name = (p['name_en'] if LANG == 'en' else p['name']) if p else None
            slot = T(f"槽{i+1}", f"Slot {i+1}")
            empty = T("[空]", "[Empty]")
            label = f"{slot}：{name}  ({pid})" if p else f"{slot}：{empty}"
            self.potion_slot_list.insert("end", label)

    def _on_potion_search(self, *_):
        kw = self._potion_search.get().lower()
        self._fill_potion_tree([p for p in self.all_potions
            if not kw or kw in p['name'].lower()
            or kw in p.get('name_en', '').lower() or kw in p['desc'].lower()])

    def _on_potion_lib_select(self, _=None):
        sel = self.potion_lib_tree.selection()
        if not sel: return
        p = self._potion_idx.get(sel[0])
        if not p: return
        name = p['name_en'] if LANG == 'en' else p['name']
        desc = p.get('desc_en', p['desc']) if LANG == 'en' else p['desc']
        _set_desc(self._potion_desc, f"[{name}  /  {p.get('name_en','')}]\n{desc}")

    def _on_potion_slot_select(self, _=None):
        idx = self.potion_slot_list.curselection()
        if not idx or not self.save_data: return
        potions = self.save_data.get("potions", [])
        i = idx[0]
        if i < len(potions) and potions[i] != "Potion Slot":
            p = self._potion_idx.get(potions[i])
            if p:
                name = p['name_en'] if LANG == 'en' else p['name']
                desc = p.get('desc_en', p['desc']) if LANG == 'en' else p['desc']
                _set_desc(self._potion_desc, f"[{name}  /  {p.get('name_en','')}]\n{desc}")
        self.btn_potion_set.config(state="normal")
        self.btn_potion_clear.config(state="normal")

    def _set_potion(self):
        lib_sel = self.potion_lib_tree.selection()
        slot_sel = self.potion_slot_list.curselection()
        if not lib_sel or not slot_sel or not self.save_data: return
        i = slot_sel[0]
        potions = self.save_data.get("potions", [])
        if i < len(potions):
            potions[i] = lib_sel[0]
            self._refresh_potion_slots()
            self.potion_slot_list.selection_set(i)

    def _clear_potion(self):
        idx = self.potion_slot_list.curselection()
        if not idx or not self.save_data: return
        potions = self.save_data.get("potions", [])
        i = idx[0]
        if i < len(potions):
            potions[i] = "Potion Slot"
            self._refresh_potion_slots()

    # ── Card/deck logic ───────────────────────────────────────────────────────

    def _refresh_deck(self):
        self.card_deck_list.delete(0, "end")
        cards_data = (self.save_data or {}).get("cards", [])
        n = len(cards_data)
        self._deck_label_var.set(T(f"当前牌组 ({n}张)", f"Current Deck ({n})"))
        for entry in cards_data:
            cid  = entry.get("id", "")
            ups  = entry.get("upgrades", 0)
            c    = self._card_idx.get(cid)
            name = (c['name_en'] if LANG == 'en' else c['name']) if c else cid
            self.card_deck_list.insert("end", f"{name}{' [+'+str(ups)+']' if ups else ''}  ({cid})")

    def _on_card_filter(self, *_):
        self._fill_card_tree(self._current_card_filter())

    def _on_card_lib_select(self, _=None):
        sel = self.card_lib_tree.selection()
        if not sel: return
        c = self._card_idx.get(sel[0])
        if not c: return
        name   = c['name_en'] if LANG == 'en' else c['name']
        desc   = c.get('desc_en', c['desc']) if LANG == 'en' else c['desc']
        desc_up = c.get('desc_up_en', c.get('desc_up', '')) if LANG == 'en' else c.get('desc_up', '')
        cat    = self._cat_display(c.get('category', ''))
        text   = f"[{name}  /  {c.get('name_en','')}]  ({cat})\n{desc}"
        if desc_up and desc_up != desc:
            text += f"\n\n{T('升级后：', 'Upgraded: ')}{desc_up}"
        _set_desc(self._card_desc, text)

    def _on_deck_select(self, _=None):
        idx = self.card_deck_list.curselection()
        if not idx or not self.save_data: return
        cards = self.save_data.get("cards", [])
        i = idx[0]
        if i >= len(cards): return
        entry = cards[i]
        c = self._card_idx.get(entry.get("id", ""))
        if c:
            ups  = entry.get("upgrades", 0)
            name = c['name_en'] if LANG == 'en' else c['name']
            if LANG == 'en':
                desc = (c.get('desc_up_en') or c.get('desc_en', c['desc'])) if ups > 0 else c.get('desc_en', c['desc'])
            else:
                desc = c['desc_up'] if ups > 0 and c.get('desc_up') else c['desc']
            _set_desc(self._card_desc,
                      f"[{name}  /  {c.get('name_en','')}]  {T('升级次数', 'Upgrades')}: {ups}\n{desc}")
        for btn in (self.btn_card_remove, self.btn_card_up, self.btn_card_dn):
            btn.config(state="normal")

    def _add_card(self):
        sel = self.card_lib_tree.selection()
        if not sel or not self.save_data: return
        try: ups = max(0, int(self._card_upgrade_var.get()))
        except ValueError: ups = 0
        self.save_data.setdefault("cards", []).append(
            {"id": sel[0], "upgrades": ups, "misc": 0})
        self._refresh_deck()

    def _remove_card(self):
        idx = self.card_deck_list.curselection()
        if not idx or not self.save_data: return
        i = idx[0]
        cards = self.save_data.get("cards", [])
        if i < len(cards): cards.pop(i)
        for btn in (self.btn_card_remove, self.btn_card_up, self.btn_card_dn):
            btn.config(state="disabled")
        self._refresh_deck()

    def _upgrade_card(self):   self._adjust_card_upgrade(+1)
    def _downgrade_card(self): self._adjust_card_upgrade(-1)

    def _adjust_card_upgrade(self, delta):
        idx = self.card_deck_list.curselection()
        if not idx or not self.save_data: return
        i = idx[0]
        cards = self.save_data.get("cards", [])
        if i >= len(cards): return
        cards[i]["upgrades"] = max(0, cards[i].get("upgrades", 0) + delta)
        self._refresh_deck()
        self.card_deck_list.selection_set(i)
        self._on_deck_select()

    # ── Save ──────────────────────────────────────────────────────────────────

    def _save_stats(self):
        if not self.save_data: return
        changes = {}
        for key, var in self._edit_vars.items():
            raw = var.get().strip()
            try: val = int(raw)
            except ValueError:
                messagebox.showerror(T("输入错误", "Input Error"),
                    T(f"[{key}] 必须是整数，当前：{raw!r}", f"[{key}] must be an integer, got: {raw!r}")); return
            if val < 0:
                messagebox.showerror(T("输入错误", "Input Error"),
                    T(f"[{key}] 不能为负数", f"[{key}] cannot be negative")); return
            changes[key] = val
        if changes["current_health"] > changes["max_health"]:
            messagebox.showerror(T("输入错误", "Input Error"),
                T("当前 HP 不能超过最大 HP", "Current HP cannot exceed Max HP")); return
        for k, v in changes.items():
            self.save_data[k] = v
        new_slots = changes["potion_slots"]
        potions = self.save_data.setdefault("potions", [])
        while len(potions) < new_slots: potions.append("Potion Slot")
        while len(potions) > new_slots: potions.pop()
        for key, (v, _) in self._key_vars.items():
            self.save_data[key] = v.get()
        self.save_data["is_ascension_mode"] = self._ascension_var.get()
        try: self.save_data["ascension_level"] = int(self._ascension_level.get())
        except ValueError: pass
        self._do_write(T("数值", "Stats"))

    def _do_write(self, label):
        if not self.save_data or not self.save_path: return
        backup = backup_file(self.save_path)
        try:
            with open(self.save_path, "wb") as f:
                f.write(encode_save(self.save_data))
        except Exception as e:
            messagebox.showerror(T("保存失败", "Save Failed"), str(e)); return
        self._status.set(T(f"✓ {label}已保存，备份：{os.path.basename(backup)}",
                            f"✓ {label} saved, backup: {os.path.basename(backup)}"))
        self._on_file_selected()


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    cfg  = load_config()
    LANG = cfg.get('lang', 'zh')
    game_dir = get_or_setup_game_dir()
    if not game_dir:
        sys.exit(0)
    set_paths(game_dir)
    App().mainloop()
