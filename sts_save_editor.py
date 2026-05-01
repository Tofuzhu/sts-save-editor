# -*- coding: utf-8 -*-
import base64, json, os, re, shutil, struct, sys, zipfile
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from datetime import datetime

# ── 运行目录（exe 和 .py 两种情况）────────────────────────────────────────────
if getattr(sys, 'frozen', False):
    APP_DIR = os.path.dirname(sys.executable)
else:
    APP_DIR = os.path.dirname(os.path.abspath(__file__))

CONFIG_FILE = os.path.join(APP_DIR, "sts_editor_config.json")
CACHE_FILE  = os.path.join(APP_DIR, "sts_data_cache.json")
CACHE_VER   = 3

XOR_KEY   = b"key"
CHAR_CATS = {
    'red': '铁甲人', 'green': '猎手', 'blue': '机器人',
    'purple': '守望者', 'colorless': '无色',
    'curse': '咒语', 'status': '状态',
}
_CARD_TYPES    = {'ATTACK', 'SKILL', 'POWER', 'STATUS', 'CURSE'}
_TYPE_ZH_TO_EN = {'攻击': 'ATTACK', '技能': 'SKILL', '异能': 'POWER', '状态': 'STATUS', '诅咒': 'CURSE'}

# ── 配置文件 ──────────────────────────────────────────────────────────────────

def load_config():
    try:
        with open(CONFIG_FILE, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}

def save_config(game_dir):
    with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
        json.dump({"game_dir": game_dir}, f, ensure_ascii=False, indent=2)

def auto_detect_game():
    """尝试从注册表或常见路径自动找到游戏目录。"""
    # Windows 注册表（Steam 已安装的游戏）
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\Steam App 646570")
        path, _ = winreg.QueryValueEx(key, "InstallLocation")
        if os.path.isfile(os.path.join(path, "desktop-1.0.jar")):
            return path
    except Exception:
        pass
    # 常见路径猜测
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

# ── 路径（由 setup 流程赋值）──────────────────────────────────────────────────
GAME_DIR  = ""
JAR_PATH  = ""
SAVES_DIR = ""

def set_paths(game_dir):
    global GAME_DIR, JAR_PATH, SAVES_DIR
    GAME_DIR  = game_dir
    JAR_PATH  = os.path.join(game_dir, "desktop-1.0.jar")
    SAVES_DIR = os.path.join(game_dir, "saves")

# ── 存档编解码 ────────────────────────────────────────────────────────────────

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

# ── JAR 解析工具 ──────────────────────────────────────────────────────────────

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

def _clean(text):
    if isinstance(text, list):
        text = ' '.join(str(t) for t in text)
    text = re.sub(r'#[a-zA-Z]', '', text or '')
    text = re.sub(r'!\w+!', '[数值]', text)
    text = text.replace(' NL ', '\n').replace('NL', '\n')
    text = re.sub(r'\[E\]', '[能量]', text)
    return re.sub(r'  +', ' ', text).strip()

# ── 游戏数据加载（带缓存）────────────────────────────────────────────────────

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
                        'name':    e_zh.get('NAME') or e_en.get('NAME', eid),
                        'name_en': e_en.get('NAME', eid),
                        'desc':    _clean(e_zh.get('DESCRIPTIONS') or e_en.get('DESCRIPTIONS', [])),
                        'flavor':  e_zh.get('FLAVOR') or e_en.get('FLAVOR', '')})
    for eid, e_en in eng.items():
        if eid not in seen:
            e_zh = zhs.get(eid, {})
            out.append({'id': eid,
                        'name':    e_zh.get('NAME') or e_en.get('NAME', eid),
                        'name_en': e_en.get('NAME', eid),
                        'desc':    _clean(e_zh.get('DESCRIPTIONS') or e_en.get('DESCRIPTIONS', [])),
                        'flavor':  e_zh.get('FLAVOR') or e_en.get('FLAVOR', '')})
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
                        'desc':    _clean(e_zh.get('DESCRIPTIONS') or e_en.get('DESCRIPTIONS', []))})
    out.sort(key=lambda r: r['name'].lower())
    return out

def _parse_cards(jar, eng, zhs):
    out, seen = [], set()
    for path in sorted(jar.namelist()):
        m = re.match(r'com/megacrit/cardcrawl/cards/(\w+)/\w+\.class', path)
        if not m or '$' in path or m.group(1) in ('CardGroup', 'CardSave'):
            continue
        category = CHAR_CATS.get(m.group(1), m.group(1))
        cls_data = jar.read(path)
        cid = _first_id(cls_data)
        if cid and cid in eng and cid not in seen:
            seen.add(cid)
            e_en, e_zh = eng[cid], zhs.get(cid, {})
            out.append({'id': cid,
                        'name':    e_zh.get('NAME') or e_en.get('NAME', cid),
                        'name_en': e_en.get('NAME', cid),
                        'desc':    _clean(e_zh.get('DESCRIPTION', '') or e_en.get('DESCRIPTION', '')),
                        'desc_up': _clean(e_zh.get('UPGRADE_DESCRIPTION', '') or e_en.get('UPGRADE_DESCRIPTION', '')),
                        'category': category,
                        'type':     _card_type(cls_data)})
    out.sort(key=lambda r: r['name'].lower())
    return out

def load_all_data():
    """返回 (relics, potions, cards)，优先读缓存。"""
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

# ── GUI 小工具 ────────────────────────────────────────────────────────────────

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
    lf = ttk.LabelFrame(parent, text="描述")
    t = tk.Text(lf, height=height, wrap="word", state="disabled", relief="flat")
    t.pack(fill="x", padx=4, pady=4)
    return lf, t

def _set_desc(widget, text):
    widget.config(state="normal")
    widget.delete("1.0", "end")
    if text: widget.insert("end", text)
    widget.config(state="disabled")

# ── 首次运行设置对话框 ────────────────────────────────────────────────────────

class SetupDialog(tk.Toplevel):
    def __init__(self, master, detected=None):
        super().__init__(master)
        self.title("设置游戏目录")
        self.resizable(False, False)
        self.result = None
        self.grab_set()

        ttk.Label(self, text="请选择杀戮尖塔游戏目录", font=("", 11, "bold")).pack(
            padx=20, pady=(16, 4))
        ttk.Label(self, text="（含 desktop-1.0.jar 的文件夹）",
                  foreground="gray").pack(padx=20, pady=(0, 12))

        row = ttk.Frame(self)
        row.pack(fill="x", padx=20)
        self._path_var = tk.StringVar(value=detected or "")
        ttk.Entry(row, textvariable=self._path_var, width=44).pack(side="left", padx=(0, 6))
        ttk.Button(row, text="浏览…", command=self._browse).pack(side="left")

        self._status = tk.StringVar()
        self._status_lbl = ttk.Label(self, textvariable=self._status, foreground="gray")
        self._status_lbl.pack(padx=20, pady=6)

        btns = ttk.Frame(self)
        btns.pack(pady=(0, 16))
        ttk.Button(btns, text="确认", command=self._confirm, width=10).pack(side="left", padx=6)
        ttk.Button(btns, text="退出", command=self.destroy, width=10).pack(side="left", padx=6)

        self._path_var.trace_add("write", self._validate)
        self._validate()
        self.protocol("WM_DELETE_WINDOW", self.destroy)

        # 居中
        self.update_idletasks()
        x = master.winfo_screenwidth()  // 2 - self.winfo_width()  // 2
        y = master.winfo_screenheight() // 2 - self.winfo_height() // 2
        self.geometry(f"+{x}+{y}")

    def _browse(self):
        d = filedialog.askdirectory(title="选择游戏目录")
        if d:
            self._path_var.set(d)

    def _validate(self, *_):
        p = self._path_var.get().strip()
        if validate_game_dir(p):
            self._status.set("✓ 找到 desktop-1.0.jar")
            self._status_lbl.config(foreground="green")
        elif p:
            self._status.set("✗ 未找到 desktop-1.0.jar")
            self._status_lbl.config(foreground="red")
        else:
            self._status.set("")

    def _confirm(self):
        p = self._path_var.get().strip()
        if not validate_game_dir(p):
            messagebox.showerror("错误", "所选目录无效，未找到 desktop-1.0.jar", parent=self)
            return
        self.result = p
        self.destroy()


def get_or_setup_game_dir():
    """返回有效的游戏目录，找不到时弹出设置对话框。"""
    cfg = load_config()
    game_dir = cfg.get("game_dir", "")
    if validate_game_dir(game_dir):
        return game_dir

    detected = auto_detect_game()
    if detected:
        save_config(detected)
        return detected

    root = tk.Tk()
    root.withdraw()
    dlg = SetupDialog(root, detected)
    root.wait_window(dlg)
    result = dlg.result
    root.destroy()
    if result:
        save_config(result)
    return result

# ── 主界面 ────────────────────────────────────────────────────────────────────

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("杀戮尖塔存档编辑器")
        self.minsize(920, 640)
        self.save_data = None
        self.save_path = None
        self.all_relics = self.all_potions = self.all_cards = []
        self._relic_idx = {}; self._potion_idx = {}; self._card_idx = {}
        self._deck_label_var = tk.StringVar(value="当前牌组")

        self._build_ui()
        self._load_all_libraries()
        self._load_file_list()

    # ── 顶层结构 ──────────────────────────────────────────────────────────────

    def _build_ui(self):
        self.columnconfigure(0, weight=1); self.rowconfigure(1, weight=1)

        bar = ttk.Frame(self, padding=(8, 6))
        bar.grid(row=0, column=0, sticky="ew")
        ttk.Label(bar, text="存档文件：").pack(side="left")
        self.file_var = tk.StringVar()
        self.file_combo = ttk.Combobox(bar, textvariable=self.file_var,
                                       state="readonly", width=26)
        self.file_combo.pack(side="left", padx=(0, 6))
        self.file_combo.bind("<<ComboboxSelected>>", self._on_file_selected)
        ttk.Button(bar, text="刷新", command=self._load_file_list).pack(side="left")
        ttk.Button(bar, text="切换目录", command=self._change_dir).pack(side="left", padx=(8, 0))
        self._status = tk.StringVar(value="正在加载游戏数据…")
        ttk.Label(bar, textvariable=self._status, foreground="gray").pack(side="right", padx=8)

        nb = ttk.Notebook(self, padding=(6, 0, 6, 6))
        nb.grid(row=1, column=0, sticky="nsew")
        self._build_tab_stats(nb)
        self._build_tab_relics(nb)
        self._build_tab_potions(nb)
        self._build_tab_cards(nb)

    # ── Tab 1：基本数值 ───────────────────────────────────────────────────────

    def _build_tab_stats(self, nb):
        tab = ttk.Frame(nb, padding=10)
        nb.add(tab, text="  基本数值  ")
        tab.columnconfigure(0, weight=1); tab.columnconfigure(1, weight=1)

        info = ttk.LabelFrame(tab, text="存档信息（只读）")
        info.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        self._info_vars = {}
        for i, (lbl, key) in enumerate([("角色", "name"), ("第几幕", "act_num"),
                                         ("楼层", "floor_num"), ("当前 HP", "current_health"),
                                         ("最大 HP", "max_health")]):
            c = (i % 3) * 2
            ttk.Label(info, text=lbl + "：", width=9, anchor="e").grid(
                row=i // 3, column=c, padx=(12, 2), pady=3, sticky="e")
            v = tk.StringVar(value="—")
            ttk.Label(info, textvariable=v, width=14, anchor="w").grid(
                row=i // 3, column=c + 1, pady=3, sticky="w")
            self._info_vars[key] = v

        nums = ttk.LabelFrame(tab, text="数值修改")
        nums.grid(row=1, column=0, sticky="nsew", padx=(0, 6), pady=(0, 8))
        self._edit_vars, self._edit_entries = {}, {}
        for i, (lbl, key) in enumerate([("金币", "gold"), ("最大 HP", "max_health"),
                                          ("当前 HP", "current_health"), ("手牌上限", "hand_size"),
                                          ("药水槽数", "potion_slots"), ("净化费用", "purgeCost")]):
            ttk.Label(nums, text=lbl + "：", width=10, anchor="e").grid(
                row=i, column=0, padx=(8, 2), pady=4, sticky="e")
            v = tk.StringVar()
            e = ttk.Entry(nums, textvariable=v, width=12, state="disabled")
            e.grid(row=i, column=1, pady=4, padx=(0, 12), sticky="w")
            self._edit_vars[key] = v; self._edit_entries[key] = e

        switches = ttk.LabelFrame(tab, text="开关 / 钥匙")
        switches.grid(row=1, column=1, sticky="nsew", pady=(0, 8))
        self._key_vars = {}
        for i, (lbl, key) in enumerate([("红钥匙", "has_ruby_key"),
                                         ("绿钥匙", "has_emerald_key"),
                                         ("蓝钥匙", "has_sapphire_key")]):
            v = tk.BooleanVar()
            cb = ttk.Checkbutton(switches, text=lbl, variable=v, state="disabled")
            cb.grid(row=i, column=0, sticky="w", padx=12, pady=4)
            self._key_vars[key] = (v, cb)

        ttk.Separator(switches, orient="horizontal").grid(
            row=3, column=0, sticky="ew", padx=8, pady=6)
        ttk.Label(switches, text="灼热模式：").grid(row=4, column=0, sticky="w", padx=12)
        self._ascension_var = tk.BooleanVar()
        self._ascension_cb = ttk.Checkbutton(switches, text="开启",
                                              variable=self._ascension_var, state="disabled")
        self._ascension_cb.grid(row=5, column=0, sticky="w", padx=12, pady=2)
        ttk.Label(switches, text="灼热等级：").grid(row=6, column=0, sticky="w", padx=12)
        self._ascension_level = tk.StringVar(value="0")
        self._ascension_spin = ttk.Spinbox(switches, from_=0, to=20, width=6,
                                            textvariable=self._ascension_level, state="disabled")
        self._ascension_spin.grid(row=7, column=0, sticky="w", padx=12, pady=2)

        self.btn_stats_save = ttk.Button(tab, text="保存以上修改",
                                         command=self._save_stats, state="disabled")
        self.btn_stats_save.grid(row=2, column=0, columnspan=2, pady=4)

    # ── Tab 2：遗物管理 ───────────────────────────────────────────────────────

    def _build_tab_relics(self, nb):
        tab = ttk.Frame(nb, padding=10)
        nb.add(tab, text="  遗物管理  ")
        tab.columnconfigure(0, weight=3); tab.columnconfigure(2, weight=1); tab.rowconfigure(1, weight=1)

        sf = ttk.Frame(tab); sf.grid(row=0, column=0, sticky="ew", pady=(0, 2))
        ttk.Label(sf, text="遗物库  搜索：").pack(side="left")
        self._relic_search = tk.StringVar()
        self._relic_search.trace_add("write", self._on_relic_search)
        ttk.Entry(sf, textvariable=self._relic_search, width=22).pack(side="left")
        ttk.Button(sf, text="✕", width=2,
                   command=lambda: self._relic_search.set("")).pack(side="left", padx=2)

        lf, self.relic_lib_tree = _make_tree(
            tab, ("name", "name_en"), ("中文名称", "English Name"), (160, 160))
        lf.grid(row=1, column=0, sticky="nsew")
        self.relic_lib_tree.bind("<<TreeviewSelect>>", self._on_relic_lib_select)

        mid = ttk.Frame(tab, padding=(6, 0)); mid.grid(row=1, column=1, sticky="ns")
        mid.rowconfigure(0, weight=1)
        ba = ttk.Frame(mid); ba.grid(row=0, column=0)
        self.btn_relic_add    = ttk.Button(ba, text="添加 →", width=10, state="disabled", command=self._add_relic)
        self.btn_relic_remove = ttk.Button(ba, text="← 移除", width=10, state="disabled", command=self._remove_relic)
        self.btn_relic_save   = ttk.Button(ba, text="保存遗物", width=10, state="disabled",
                                           command=lambda: self._do_write("遗物"))
        self.btn_relic_add.pack(pady=6); self.btn_relic_remove.pack(pady=6)
        self.btn_relic_save.pack(pady=(40, 6))

        ttk.Label(tab, text="存档当前遗物").grid(row=0, column=2, sticky="w")
        cf, self.relic_cur_list = _make_listbox(tab, width=26)
        cf.grid(row=1, column=2, sticky="nsew")
        self.relic_cur_list.bind("<<ListboxSelect>>", self._on_relic_cur_select)

        dlf, self._relic_desc = _make_desc(tab, height=4)
        dlf.grid(row=2, column=0, columnspan=3, sticky="ew", pady=(6, 0))

    # ── Tab 3：药水管理 ───────────────────────────────────────────────────────

    def _build_tab_potions(self, nb):
        tab = ttk.Frame(nb, padding=10)
        nb.add(tab, text="  药水管理  ")
        tab.columnconfigure(0, weight=2); tab.columnconfigure(2, weight=1); tab.rowconfigure(1, weight=1)

        sf = ttk.Frame(tab); sf.grid(row=0, column=0, sticky="ew", pady=(0, 2))
        ttk.Label(sf, text="药水库  搜索：").pack(side="left")
        self._potion_search = tk.StringVar()
        self._potion_search.trace_add("write", self._on_potion_search)
        ttk.Entry(sf, textvariable=self._potion_search, width=22).pack(side="left")
        ttk.Button(sf, text="✕", width=2,
                   command=lambda: self._potion_search.set("")).pack(side="left", padx=2)

        lf, self.potion_lib_tree = _make_tree(
            tab, ("name", "name_en"), ("中文名称", "English Name"), (150, 150))
        lf.grid(row=1, column=0, sticky="nsew")
        self.potion_lib_tree.bind("<<TreeviewSelect>>", self._on_potion_lib_select)

        mid = ttk.Frame(tab, padding=(6, 0)); mid.grid(row=1, column=1, sticky="ns")
        mid.rowconfigure(0, weight=1)
        ba = ttk.Frame(mid); ba.grid(row=0, column=0)
        self.btn_potion_set   = ttk.Button(ba, text="放入槽 →", width=10, state="disabled", command=self._set_potion)
        self.btn_potion_clear = ttk.Button(ba, text="← 清空槽", width=10, state="disabled", command=self._clear_potion)
        self.btn_potion_save  = ttk.Button(ba, text="保存药水", width=10, state="disabled",
                                           command=lambda: self._do_write("药水"))
        self.btn_potion_set.pack(pady=6); self.btn_potion_clear.pack(pady=6)
        self.btn_potion_save.pack(pady=(40, 6))

        ttk.Label(tab, text="当前药水槽").grid(row=0, column=2, sticky="w")
        cf, self.potion_slot_list = _make_listbox(tab, width=28)
        cf.grid(row=1, column=2, sticky="nsew")
        self.potion_slot_list.bind("<<ListboxSelect>>", self._on_potion_slot_select)

        dlf, self._potion_desc = _make_desc(tab, height=4)
        dlf.grid(row=2, column=0, columnspan=3, sticky="ew", pady=(6, 0))

    # ── Tab 4：牌组管理 ───────────────────────────────────────────────────────

    def _build_tab_cards(self, nb):
        tab = ttk.Frame(nb, padding=10)
        nb.add(tab, text="  牌组管理  ")
        tab.columnconfigure(0, weight=3); tab.columnconfigure(2, weight=1); tab.rowconfigure(1, weight=1)

        ff = ttk.Frame(tab); ff.grid(row=0, column=0, sticky="ew", pady=(0, 2))
        ttk.Label(ff, text="职业：").pack(side="left")
        self._card_cat_var = tk.StringVar(value="全部")
        cat_cb = ttk.Combobox(ff, textvariable=self._card_cat_var, state="readonly", width=8,
                               values=["全部"] + list(CHAR_CATS.values()))
        cat_cb.pack(side="left", padx=(0, 8))
        cat_cb.bind("<<ComboboxSelected>>", self._on_card_filter)
        ttk.Label(ff, text="类型：").pack(side="left")
        self._card_type_var = tk.StringVar(value="全部")
        type_cb = ttk.Combobox(ff, textvariable=self._card_type_var, state="readonly", width=6,
                                values=["全部", "攻击", "技能", "异能", "状态", "诅咒"])
        type_cb.pack(side="left", padx=(0, 8))
        type_cb.bind("<<ComboboxSelected>>", self._on_card_filter)
        ttk.Label(ff, text="搜索：").pack(side="left")
        self._card_search = tk.StringVar()
        self._card_search.trace_add("write", self._on_card_filter)
        ttk.Entry(ff, textvariable=self._card_search, width=22).pack(side="left")
        ttk.Button(ff, text="✕", width=2,
                   command=lambda: self._card_search.set("")).pack(side="left", padx=2)

        lf, self.card_lib_tree = _make_tree(
            tab, ("name", "name_en", "cat"), ("中文名称", "English Name", "职业"), (150, 150, 60))
        lf.grid(row=1, column=0, sticky="nsew")
        self.card_lib_tree.bind("<<TreeviewSelect>>", self._on_card_lib_select)

        mid = ttk.Frame(tab, padding=(6, 0)); mid.grid(row=1, column=1, sticky="ns")
        mid.rowconfigure(0, weight=1)
        ba = ttk.Frame(mid); ba.grid(row=0, column=0)
        ttk.Label(ba, text="升级次数：").pack()
        self._card_upgrade_var = tk.StringVar(value="0")
        ttk.Spinbox(ba, from_=0, to=20, width=5, textvariable=self._card_upgrade_var).pack(pady=(0, 4))
        self.btn_card_add    = ttk.Button(ba, text="添加 →",  width=10, state="disabled", command=self._add_card)
        self.btn_card_remove = ttk.Button(ba, text="← 移除", width=10, state="disabled", command=self._remove_card)
        self.btn_card_add.pack(pady=4); self.btn_card_remove.pack(pady=4)
        ttk.Separator(ba, orient="horizontal").pack(fill="x", pady=8)
        ttk.Label(ba, text="选中的牌：").pack()
        self.btn_card_up = ttk.Button(ba, text="升级 +1", width=10, state="disabled", command=self._upgrade_card)
        self.btn_card_dn = ttk.Button(ba, text="降级 -1", width=10, state="disabled", command=self._downgrade_card)
        self.btn_card_up.pack(pady=4); self.btn_card_dn.pack(pady=4)
        self.btn_card_save = ttk.Button(ba, text="保存牌组", width=10, state="disabled",
                                        command=lambda: self._do_write("牌组"))
        self.btn_card_save.pack(pady=(20, 4))

        ttk.Label(tab, textvariable=self._deck_label_var).grid(row=0, column=2, sticky="w")
        cf, self.card_deck_list = _make_listbox(tab, width=26)
        cf.grid(row=1, column=2, sticky="nsew")
        self.card_deck_list.bind("<<ListboxSelect>>", self._on_deck_select)

        dlf, self._card_desc = _make_desc(tab, height=5)
        dlf.grid(row=2, column=0, columnspan=3, sticky="ew", pady=(6, 0))

    # ── 数据加载 ──────────────────────────────────────────────────────────────

    def _load_all_libraries(self):
        try:
            cached = _load_cache()
            if cached:
                self._status.set("从缓存加载完成")
            else:
                self._status.set("首次加载，解析游戏数据（约10秒）…")
                self.update()
            self.all_relics, self.all_potions, self.all_cards = load_all_data()
        except Exception as e:
            messagebox.showwarning("数据加载失败", str(e))
            self.all_relics = self.all_potions = self.all_cards = []

        self._fill_relic_tree(self.all_relics)
        self._fill_potion_tree(self.all_potions)
        self._fill_card_tree(self.all_cards)
        self._relic_idx  = {r['id']: r for r in self.all_relics}
        self._potion_idx = {p['id']: p for p in self.all_potions}
        self._card_idx   = {c['id']: c for c in self.all_cards}
        self._status.set("就绪")

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
                                      values=(c['name'], c.get('name_en', ''), c.get('category', '')))

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
            self._status.set("未找到存档文件")

    def _on_file_selected(self, _=None):
        name = self.file_var.get()
        if not name: return
        path = os.path.join(SAVES_DIR, name)
        try:
            self.save_data = decode_save(path)
            self.save_path = path
        except Exception as e:
            messagebox.showerror("读取失败", str(e)); return

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
        self._status.set(f"已加载：{name}")

    def _change_dir(self):
        dlg = SetupDialog(self, GAME_DIR)
        self.wait_window(dlg)
        if dlg.result:
            save_config(dlg.result)
            set_paths(dlg.result)
            # 缓存可能对新路径无效，删掉重新生成
            try: os.remove(CACHE_FILE)
            except OSError: pass
            self._load_all_libraries()
            self._load_file_list()

    # ── 遗物逻辑 ──────────────────────────────────────────────────────────────

    def _refresh_relic_list(self):
        self.relic_cur_list.delete(0, "end")
        for rid in (self.save_data or {}).get("relics", []):
            r = self._relic_idx.get(rid)
            self.relic_cur_list.insert("end", f"{r['name']}  ({rid})" if r else rid)

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
        header = r['name']
        if r.get('name_en') and r['name_en'] != r['name']:
            header += f"  /  {r['name_en']}"
        text = f"[{header}]\n{r['desc']}"
        if r.get('flavor'): text += f"\n\n{r['flavor']}"
        _set_desc(self._relic_desc, text)

    def _add_relic(self):
        sel = self.relic_lib_tree.selection()
        if not sel or not self.save_data: return
        rid = sel[0]
        relics   = self.save_data.setdefault("relics", [])
        counters = self.save_data.setdefault("relic_counters", [])
        if rid in relics:
            messagebox.showinfo("提示", f"存档中已有：{rid}"); return
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

    # ── 药水逻辑 ──────────────────────────────────────────────────────────────

    def _refresh_potion_slots(self):
        self.potion_slot_list.delete(0, "end")
        for i, pid in enumerate((self.save_data or {}).get("potions", [])):
            p = self._potion_idx.get(pid)
            label = f"槽{i+1}：{p['name']}  ({pid})" if p else f"槽{i+1}：[空]"
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
        if p: _set_desc(self._potion_desc, f"[{p['name']}  /  {p.get('name_en','')}]\n{p['desc']}")

    def _on_potion_slot_select(self, _=None):
        idx = self.potion_slot_list.curselection()
        if not idx or not self.save_data: return
        potions = self.save_data.get("potions", [])
        i = idx[0]
        if i < len(potions) and potions[i] != "Potion Slot":
            p = self._potion_idx.get(potions[i])
            if p: _set_desc(self._potion_desc, f"[{p['name']}  /  {p.get('name_en','')}]\n{p['desc']}")
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

    # ── 牌组逻辑 ──────────────────────────────────────────────────────────────

    def _refresh_deck(self):
        self.card_deck_list.delete(0, "end")
        cards_data = (self.save_data or {}).get("cards", [])
        self._deck_label_var.set(f"当前牌组 ({len(cards_data)}张)")
        for entry in cards_data:
            cid = entry.get("id", "")
            ups = entry.get("upgrades", 0)
            c = self._card_idx.get(cid)
            name = c['name'] if c else cid
            self.card_deck_list.insert("end", f"{name}{' [+'+str(ups)+']' if ups else ''}  ({cid})")

    def _on_card_filter(self, *_):
        cat     = self._card_cat_var.get()
        type_en = _TYPE_ZH_TO_EN.get(self._card_type_var.get(), '')
        kw      = self._card_search.get().lower()
        self._fill_card_tree([c for c in self.all_cards
            if (cat == "全部" or c.get('category') == cat)
            and (not type_en or c.get('type') == type_en)
            and (not kw or kw in c['name'].lower()
                 or kw in c.get('name_en', '').lower() or kw in c['desc'].lower())])

    def _on_card_lib_select(self, _=None):
        sel = self.card_lib_tree.selection()
        if not sel: return
        c = self._card_idx.get(sel[0])
        if not c: return
        text = f"[{c['name']}  /  {c.get('name_en','')}]  ({c.get('category','')})\n{c['desc']}"
        if c.get('desc_up') and c['desc_up'] != c['desc']:
            text += f"\n\n升级后：{c['desc_up']}"
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
            ups = entry.get("upgrades", 0)
            desc = c['desc_up'] if ups > 0 and c.get('desc_up') else c['desc']
            _set_desc(self._card_desc,
                      f"[{c['name']}  /  {c.get('name_en','')}]  升级次数: {ups}\n{desc}")
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

    # ── 保存 ─────────────────────────────────────────────────────────────────

    def _save_stats(self):
        if not self.save_data: return
        changes = {}
        for key, var in self._edit_vars.items():
            raw = var.get().strip()
            try: val = int(raw)
            except ValueError:
                messagebox.showerror("输入错误", f"[{key}] 必须是整数，当前：{raw!r}"); return
            if val < 0:
                messagebox.showerror("输入错误", f"[{key}] 不能为负数"); return
            changes[key] = val
        if changes["current_health"] > changes["max_health"]:
            messagebox.showerror("输入错误", "当前 HP 不能超过最大 HP"); return
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
        self._do_write("数值")

    def _do_write(self, label):
        if not self.save_data or not self.save_path: return
        backup = backup_file(self.save_path)
        try:
            with open(self.save_path, "wb") as f:
                f.write(encode_save(self.save_data))
        except Exception as e:
            messagebox.showerror("保存失败", str(e)); return
        self._status.set(f"✓ {label}已保存，备份：{os.path.basename(backup)}")
        self._on_file_selected()


# ── 入口 ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    game_dir = get_or_setup_game_dir()
    if not game_dir:
        sys.exit(0)
    set_paths(game_dir)
    App().mainloop()
