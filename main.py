import sqlite3
import uvicorn
import hashlib
import uuid
import html
from contextlib import asynccontextmanager
from fastapi import FastAPI, Form, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse

DB_FILE = "campus_team_v11_clean.db"


# --- 1. 安全与辅助工具 ---
def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode('utf-8')).hexdigest()


def generate_session_id() -> str:
    return uuid.uuid4().hex


# --- 2. 数据库初始化 ---
def init_db():
    # 优化：增加 timeout 防止轮询时出现 database is locked
    with sqlite3.connect(DB_FILE, timeout=10) as conn:
        cursor = conn.cursor()
        cursor.execute('''CREATE TABLE IF NOT EXISTS users
                          (
                              phone
                              TEXT
                              PRIMARY
                              KEY,
                              name
                              TEXT
                              NOT
                              NULL,
                              password
                              TEXT
                              NOT
                              NULL,
                              session_id
                              TEXT,
                              college
                              TEXT
                              DEFAULT
                              '',
                              major
                              TEXT
                              DEFAULT
                              '',
                              class_name
                              TEXT
                              DEFAULT
                              '',
                              qq
                              TEXT
                              DEFAULT
                              '',
                              wechat
                              TEXT
                              DEFAULT
                              '',
                              bio
                              TEXT
                              DEFAULT
                              '',
                              is_first_login
                              INTEGER
                              DEFAULT
                              1,
                              created_at
                              TIMESTAMP
                              DEFAULT
                              CURRENT_TIMESTAMP
                          )''')

        try:
            cursor.execute("ALTER TABLE users ADD COLUMN skills TEXT DEFAULT ''")
        except sqlite3.OperationalError:
            pass
        try:
            cursor.execute("ALTER TABLE users ADD COLUMN honors TEXT DEFAULT ''")
        except sqlite3.OperationalError:
            pass
        try:
            cursor.execute("ALTER TABLE users ADD COLUMN student_id TEXT DEFAULT ''")
        except sqlite3.OperationalError:
            pass
        try:
            cursor.execute("ALTER TABLE users ADD COLUMN last_seen_proj_id INTEGER DEFAULT 0")
        except sqlite3.OperationalError:
            pass

        cursor.execute("CREATE TABLE IF NOT EXISTS user_sessions (session_id TEXT PRIMARY KEY, phone TEXT UNIQUE)")
        cursor.execute('''CREATE TABLE IF NOT EXISTS projects
        (
            id
            INTEGER
            PRIMARY
            KEY
            AUTOINCREMENT,
            title
            TEXT
            NOT
            NULL,
            leader_phone
            TEXT
            NOT
            NULL,
            description
            TEXT,
            tags
            TEXT,
            base_members
            INTEGER
            NOT
            NULL
            DEFAULT
            1,
            required_members
            INTEGER
            NOT
            NULL
            DEFAULT
            3,
            status
            TEXT
            DEFAULT
            '招募中',
            is_deleted
            INTEGER
            DEFAULT
            0,
            is_hidden
            INTEGER
            DEFAULT
            0,
            FOREIGN
            KEY
                          (
            leader_phone
                          ) REFERENCES users
                          (
                              phone
                          )
            )''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS applications
        (
            id
            INTEGER
            PRIMARY
            KEY
            AUTOINCREMENT,
            proj_id
            INTEGER,
            applicant_phone
            TEXT
            NOT
            NULL,
            status
            TEXT
            DEFAULT
            '待审核',
            applicant_visible
            INTEGER
            DEFAULT
            1,
            leader_visible
            INTEGER
            DEFAULT
            1,
            FOREIGN
            KEY
                          (
            proj_id
                          ) REFERENCES projects
                          (
                              id
                          ),
            FOREIGN KEY
                          (
                              applicant_phone
                          ) REFERENCES users
                          (
                              phone
                          )
            )''')

        try:
            cursor.execute("ALTER TABLE applications ADD COLUMN leader_read INTEGER DEFAULT 0")
        except sqlite3.OperationalError:
            pass
        try:
            cursor.execute("ALTER TABLE applications ADD COLUMN applicant_read INTEGER DEFAULT 0")
        except sqlite3.OperationalError:
            pass

        cursor.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_proj_user ON applications(proj_id, applicant_phone)')
        cursor.execute('''CREATE TABLE IF NOT EXISTS messages
                          (
                              id
                              INTEGER
                              PRIMARY
                              KEY
                              AUTOINCREMENT,
                              sender_phone
                              TEXT
                              NOT
                              NULL,
                              chat_type
                              TEXT
                              NOT
                              NULL,
                              target_id
                              TEXT
                              NOT
                              NULL,
                              content
                              TEXT
                              NOT
                              NULL,
                              created_at
                              TIMESTAMP
                              DEFAULT
                              CURRENT_TIMESTAMP
                          )''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS chat_state
        (
            phone
            TEXT,
            chat_type
            TEXT,
            target_id
            TEXT,
            last_read_msg_id
            INTEGER
            DEFAULT
            0,
            cleared_up_to_msg_id
            INTEGER
            DEFAULT
            0,
            PRIMARY
            KEY
                          (
            phone,
            chat_type,
            target_id
                          )
            )''')

        # ==========================================
        # 🌟 新增：初始化测试数据
        # ==========================================
        # 检查 users 表是否为空，为空则说明是首次建库，插入测试数据
        cursor.execute("SELECT COUNT(*) FROM users")
        if cursor.fetchone()[0] == 0:
            # 1. 插入测试体验账号
            cursor.executemany(
                '''INSERT INTO users (phone, student_id, name, password, college, major, skills, is_first_login)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)''', [
                    ('13800000001', '202300000001', '张三(测试队长)',
                     '8d969eef6ecad3c29a3a629280e686cf0c3f5d5a86aff3ca12020c923adc6c92', '计算机学院', '软件工程',
                     'Python,Vue,后端', 0),
                    ('13800000002', '202300000002', '李四(测试队员)',
                     '8d969eef6ecad3c29a3a629280e686cf0c3f5d5a86aff3ca12020c923adc6c92', '设计学院', '视觉传达',
                     'UI设计,Figma,画图', 0)
                ])

            # 2. 插入一个初始的招募项目
            cursor.execute(
                '''INSERT INTO projects (title, leader_phone, description, tags, base_members, required_members, status)
                   VALUES (?, ?, ?, ?, ?, ?, ?)''',
                ('【创新创业大赛】寻一位靠谱的UI设计师', '13800000001', '项目已经有后端和前端，目前打算做一个校园二手交易平台参加省赛，缺一位能够设计原型图和UI界面的同学，欢迎带作品来聊！',
                 'UI设计,Figma', 2, 3, '招募中'))

            # 获取刚刚插入的项目ID，用于绑定初始系统消息
            new_proj_id = cursor.lastrowid

            # 3. 插入对应的系统群聊创建消息
            cursor.execute('''INSERT INTO messages (sender_phone, chat_type, target_id, content)
                              VALUES (?, ?, ?, ?)''',
                           ('system', 'group', str(new_proj_id), '【系统】项目队伍已创建成功！'))
        # ==========================================

        conn.commit()


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="校园组队系统", lifespan=lifespan)


# --- 3. 核心查询接口 ---
def get_current_user(request: Request):
    session_id = request.cookies.get("session_token")
    if not session_id: return None
    with sqlite3.connect(DB_FILE, timeout=10) as conn:
        conn.row_factory = sqlite3.Row
        return conn.cursor().execute(
            "SELECT u.* FROM users u JOIN user_sessions s ON u.phone = s.phone WHERE s.session_id = ?",
            (session_id,)).fetchone()


@app.get("/api/check_session")
async def check_session(request: Request):
    session_id = request.cookies.get("session_token")
    if not session_id: return {"status": "logged_out"}
    with sqlite3.connect(DB_FILE, timeout=10) as conn:
        return {"status": "valid"} if conn.cursor().execute("SELECT phone FROM user_sessions WHERE session_id = ?",
                                                            (session_id,)).fetchone() else {"status": "logged_out"}


@app.get("/api/user/{phone}")
async def get_user_profile(phone: str, request: Request):
    if not get_current_user(request): return Response(status_code=401)
    with sqlite3.connect(DB_FILE, timeout=10) as conn:
        conn.row_factory = sqlite3.Row
        user = conn.cursor().execute(
            "SELECT phone, name, college, major, class_name, qq, wechat, bio, skills, honors, student_id FROM users WHERE phone = ?",
            (phone,)).fetchone()
        return dict(user) if user else {}


@app.get("/api/poll_new")
async def poll_new(request: Request, since_id: int = 0):
    user = get_current_user(request)
    if not user: return JSONResponse([])
    my_phone = user['phone']
    with sqlite3.connect(DB_FILE, timeout=10) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM projects WHERE leader_phone = ? AND is_deleted=0", (my_phone,))
        my_groups = [str(r['id']) for r in cursor.fetchall()]
        cursor.execute("SELECT proj_id FROM applications WHERE applicant_phone = ? AND status = '已同意'", (my_phone,))
        my_groups.extend([str(r['proj_id']) for r in cursor.fetchall()])
        if my_groups:
            placeholders = ','.join('?' * len(my_groups))
            query = f"""SELECT m.*, u.name as sender_name FROM messages m LEFT JOIN users u ON m.sender_phone = u.phone WHERE m.id > ? AND m.sender_phone != ? AND ((m.chat_type = 'private' AND m.target_id = ?) OR (m.chat_type = 'group' AND m.target_id IN ({placeholders}))) ORDER BY m.id ASC"""
            params = [since_id, my_phone, my_phone] + my_groups
        else:
            query = "SELECT m.*, u.name as sender_name FROM messages m LEFT JOIN users u ON m.sender_phone = u.phone WHERE m.id > ? AND m.sender_phone != ? AND m.chat_type = 'private' AND m.target_id = ? ORDER BY m.id ASC"
            params = [since_id, my_phone, my_phone]
        msgs = cursor.execute(query, params).fetchall()
        return JSONResponse([dict(m) for m in msgs])


# 🌟 修改：轮询推荐项目接口 (包含模糊匹配逻辑)
@app.get("/api/poll_recommend")
async def poll_recommend(request: Request):
    user = get_current_user(request)
    if not user: return JSONResponse({"items": []})

    my_phone = user['phone']
    # 优化：提取技能列表并转小写
    my_skills = [s.strip().lower() for s in (user['skills'] or '').split(',') if s.strip()]

    with sqlite3.connect(DB_FILE, timeout=10) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        last_seen = user['last_seen_proj_id'] or 0
        current_max_id = cursor.execute("SELECT MAX(id) FROM projects").fetchone()[0] or 0

        # 如果没填技能或首次登录，直接同步游标
        if last_seen == 0 or not my_skills:
            cursor.execute("UPDATE users SET last_seen_proj_id = ? WHERE phone = ?", (current_max_id, my_phone))
            conn.commit()
            return JSONResponse({"items": []})

        # 查找新发布的公开招募项目
        new_projects = cursor.execute('''
                                      SELECT id, title, tags
                                      FROM projects
                                      WHERE id > ? AND leader_phone != ? AND status = '招募中' AND is_deleted = 0 AND is_hidden = 0
                                      ORDER BY id ASC
                                      ''', (last_seen, my_phone)).fetchall()

        recommended_items = []
        max_id = last_seen

        for p in new_projects:
            max_id = max(max_id, p['id'])
            proj_tags_str = (p['tags'] or '').lower()
            # 💡 只要包含就推荐
            if any(skill in proj_tags_str for skill in my_skills):
                recommended_items.append({"id": p['id'], "title": p['title']})

        if max_id > last_seen:
            cursor.execute("UPDATE users SET last_seen_proj_id = ? WHERE phone = ?", (max_id, my_phone))
            conn.commit()

        return JSONResponse({"items": recommended_items})


def get_dashboard_panels(user_data, search_q="", search_tag=""):
    my_phone = user_data['phone']
    # 优化：提取技能列表并转小写
    my_skills = [s.strip().lower() for s in (user_data['skills'] or '').split(',') if s.strip()]

    with sqlite3.connect(DB_FILE, timeout=10) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        all_projects = cursor.execute('''SELECT p.*,
                                                u.name                                         as leader_name,
                                                (SELECT status
                                                 FROM applications
                                                 WHERE proj_id = p.id AND applicant_phone = ?) as my_status,
                                                (SELECT COUNT(*)
                                                 FROM applications
                                                 WHERE proj_id = p.id AND status = '已同意')   as approved_count
                                         FROM projects p
                                                  JOIN users u ON p.leader_phone = u.phone
                                         WHERE p.is_deleted = 0
                                           AND (p.is_hidden = 0 OR p.leader_phone = ?)
                                         ORDER BY p.id DESC''', (my_phone, my_phone)).fetchall()
        my_apps = cursor.execute('''SELECT a.id as app_id, p.id as proj_id, p.title, a.status
                                    FROM applications a
                                             JOIN projects p ON a.proj_id = p.id
                                    WHERE a.applicant_phone = ?
                                      AND p.is_deleted = 0
                                      AND a.applicant_visible = 1
                                    ORDER BY a.id DESC''', (my_phone,)).fetchall()
        audits = cursor.execute('''SELECT a.id     as app_id,
                                          p.id     as proj_id,
                                          p.title,
                                          u.phone  as applicant_phone,
                                          u.name   as applicant_name,
                                          u.honors as applicant_honors,
                                          a.status
                                   FROM applications a
                                            JOIN projects p ON a.proj_id = p.id
                                            JOIN users u ON a.applicant_phone = u.phone
                                   WHERE p.leader_phone = ?
                                     AND p.is_deleted = 0
                                     AND a.leader_visible = 1
                                   ORDER BY a.id DESC''', (my_phone,)).fetchall()

        audit_count = cursor.execute('''SELECT COUNT(*)
                                        FROM applications a
                                                 JOIN projects p ON a.proj_id = p.id
                                        WHERE p.leader_phone = ?
                                          AND p.is_deleted = 0
                                          AND a.status = '待审核'
                                          AND a.leader_read = 0''', (my_phone,)).fetchone()[0]
        apply_count = cursor.execute('''SELECT COUNT(*)
                                        FROM applications
                                        WHERE applicant_phone = ?
                                          AND status IN ('已同意', '已拒绝', '已移出')
                                          AND applicant_visible = 1
                                          AND applicant_read = 0''', (my_phone,)).fetchone()[0]

        members_data = cursor.execute('''SELECT a.proj_id, u.phone, u.name
                                         FROM applications a
                                                  JOIN users u ON a.applicant_phone = u.phone
                                         WHERE a.status = '已同意' ''').fetchall()
        members_by_proj = {}
        for m in members_data: members_by_proj.setdefault(m['proj_id'], []).append(m)

    projects_html, recommend_html, my_projects_html = "", "", ""
    scored_projects = []

    for p in all_projects:
        total_current = p["base_members"] + p["approved_count"]
        is_full = total_current >= p["required_members"]

        safe_title = html.escape(p["title"])

        if p['leader_phone'] == my_phone:
            hide_text = "取消隐藏" if p['is_hidden'] else "隐藏招募"
            stop_text = "重新开放" if p['status'] != "招募中" else "停止招募"
            my_projects_html += f'''
            <li class="border border-gray-100 p-4 rounded-xl mb-3 text-sm bg-gray-50 hover:shadow-md transition-shadow">
                <div class="flex justify-between items-center mb-2">
                    <span class="font-bold text-gray-800 text-base">{safe_title}</span>
                    <div class="flex gap-2">
                        <span class="bg-gray-200 text-gray-600 px-2 py-1 rounded text-xs font-bold">已入: {total_current}/{p["required_members"]}</span>
                        <span class="text-xs font-bold px-2 py-1 rounded {"bg-green-100 text-green-700" if p["status"] == "招募中" else "bg-red-100 text-red-600"}">{p["status"]}</span>
                        {f'<span class="text-xs font-bold px-2 py-1 rounded bg-purple-100 text-purple-600">已隐藏</span>' if p["is_hidden"] else ''}
                    </div>
                </div>
                <div class="mt-3 flex gap-2 justify-end border-t pt-3">
                    <form action="/toggle_hide" method="post" class="m-0 ajax-form"><input type="hidden" name="proj_id" value="{p["id"]}"><button type="submit" class="px-3 py-1.5 rounded-lg bg-purple-100 hover:bg-purple-600 hover:text-white transition-colors text-purple-700 font-bold">{hide_text}</button></form>
                    <form action="/toggle_status" method="post" class="m-0 ajax-form"><input type="hidden" name="proj_id" value="{p["id"]}"><button type="submit" class="px-3 py-1.5 rounded-lg bg-orange-100 hover:bg-orange-600 hover:text-white transition-colors text-orange-700 font-bold">{stop_text}</button></form>
                    <form action="/delete_project" method="post" class="m-0 ajax-form" data-confirm="确定永久删除吗？"><input type="hidden" name="proj_id" value="{p["id"]}"><button type="submit" class="px-3 py-1.5 rounded-lg bg-red-100 hover:bg-red-600 hover:text-white transition-colors text-red-600 font-bold">删除</button></form>
                </div>
            </li>'''

        if search_q and search_q.lower() not in p['title'].lower() and search_q.lower() not in p[
            'description'].lower(): continue
        if search_tag and search_tag.lower() not in p['tags'].lower(): continue
        if is_full and p['leader_phone'] != my_phone and not p['my_status']: continue

        status_badge = '<span class="bg-red-500 text-white text-xs px-2 py-1 rounded">已满员/截止</span>' if is_full or \
                                                                                                             p[
                                                                                                                 'status'] != '招募中' else '<span class="bg-green-500 text-white text-xs px-2 py-1 rounded shadow-sm">招募中</span>'
        if p[
            'is_hidden']: status_badge += '<span class="bg-gray-600 text-white text-xs px-2 py-1 rounded shadow-sm ml-2">🙈 仅自己可见</span>'

        if p['leader_phone'] == my_phone:
            action_btn = '<span class="text-indigo-600 bg-indigo-50 px-3 py-1.5 rounded-lg font-bold text-sm">💡 这是您发布的项目</span>'
        elif p['my_status'] == '已同意':
            action_btn = f'''<a href="/chat?type=group&id={p['id']}" class="bg-indigo-100 hover:bg-indigo-600 hover:text-white text-indigo-700 transition-colors px-4 py-1.5 rounded-lg text-sm font-bold shadow-sm">💬 群聊交流</a>'''
        elif p['my_status'] == '待审核':
            action_btn = f'<span class="text-indigo-600 font-bold text-sm bg-indigo-50 px-3 py-1.5 rounded-lg">申请状态: 待审核</span>'
        elif p['status'] == '招募中' and not is_full:
            action_btn = f'''<form action="/apply" method="post" class="inline m-0 ajax-form"><input type="hidden" name="proj_id" value="{p["id"]}"><button type="submit" class="bg-blue-100 hover:bg-blue-600 hover:text-white text-blue-700 transition-colors px-5 py-1.5 rounded-lg text-sm font-bold shadow-sm">申请加入</button></form>'''
        else:
            action_btn = '<span class="text-gray-400 text-sm font-bold">无法申请</span>'

        team_avatars_html = ""
        proj_members = members_by_proj.get(p['id'], [])
        if proj_members:
            avatars = "".join([
                f'''<div onclick="viewUserProfile('{m['phone']}')" title="{html.escape(m['name'])}" class="w-8 h-8 rounded-full bg-blue-500 border-2 border-white text-white flex items-center justify-center text-xs font-bold cursor-pointer hover:z-10 hover:scale-110 transition shadow-sm relative">{html.escape(m['name'])[0]}</div>'''
                for m
                in proj_members])
            team_avatars_html = f'''<div class="mt-4 pt-3 border-t border-dashed flex items-center"><span class="text-xs text-gray-500 font-bold mr-3">已入组员:</span><div class="flex -space-x-2">{avatars}</div></div>'''

        safe_desc = html.escape(p["description"])
        projects_html += f'''<div class="bg-white p-5 rounded-xl shadow-sm border border-gray-100 mb-5 relative group"><div class="flex justify-between items-center mb-3"><h3 class="text-xl font-extrabold text-gray-800">{safe_title}</h3><div>{status_badge}</div></div><div class="flex flex-wrap items-center gap-3 text-xs mb-3"><div class="flex items-center gap-1.5 bg-gray-100 hover:bg-indigo-100 px-2 py-1 rounded cursor-pointer transition" onclick="viewUserProfile('{p["leader_phone"]}')"><div class="w-5 h-5 bg-indigo-500 rounded-full flex items-center justify-center text-white text-[10px] font-bold shadow-sm">{html.escape(p["leader_name"])[0]}</div><span class="text-gray-700 font-bold">队长: {html.escape(p["leader_name"])}</span></div><span class="bg-orange-50 text-orange-600 px-2 py-1 rounded font-bold">进度: {total_current} / {p["required_members"]} 人</span><span class="bg-blue-50 text-blue-600 px-2 py-1 rounded">标签: {html.escape(p["tags"])}</span></div><div class="prose max-w-none text-sm text-gray-700 bg-gray-50 p-4 rounded-lg border border-gray-100 mb-4 markdown-content" data-md="{safe_desc}">正在渲染...</div><div class="flex justify-end">{action_btn}</div>{team_avatars_html}</div>'''

        if p['leader_phone'] != my_phone and p['status'] == '招募中' and not is_full:
            # 💡 优化权重计算：包含就给分
            proj_tags_str = (p['tags'] or '').lower()
            score = sum(1 for skill in my_skills if skill in proj_tags_str)
            if score > 0: scored_projects.append((score, p))

    scored_projects.sort(key=lambda x: x[0], reverse=True)
    for score, p in scored_projects[:5]:
        recommend_html += f'''<div class="p-2 border-b text-sm"><span class="font-bold text-indigo-600">🔥 {html.escape(p['title'])}</span><div class="text-xs text-gray-500 mt-1">匹配权重: {score}个标签关联</div></div>'''

    my_apps_html = "".join([
        f'''<li class="flex justify-between items-center border-b border-gray-100 pb-3 mb-3 text-sm"><div class="flex flex-col"><span class="font-bold text-gray-700">{html.escape(a["title"])}</span><span class="text-xs font-bold {"text-yellow-600" if a["status"] == "待审核" else "text-green-600" if a["status"] == "已同意" else "text-red-500"} mt-1">状态: {a["status"]}</span></div><div>{f'<form action="/cancel_apply" method="post" class="inline m-0 ajax-form"><input type="hidden" name="app_id" value="{a["app_id"]}"><button type="submit" class="text-xs bg-orange-100 hover:bg-orange-600 hover:text-white text-orange-700 font-bold px-3 py-1.5 rounded-lg transition-colors">撤销申请</button></form>' if a["status"] == "待审核" else f'<form action="/hide_record" method="post" class="inline m-0 ajax-form"><input type="hidden" name="app_id" value="{a["app_id"]}"><input type="hidden" name="role" value="applicant"><button type="submit" class="text-xs bg-red-100 hover:bg-red-600 hover:text-white text-red-600 font-bold px-3 py-1.5 rounded-lg transition-colors">删除记录</button></form>'}{f'<a href="/chat?type=group&id={a["proj_id"]}" class="text-xs bg-indigo-100 hover:bg-indigo-600 hover:text-white text-indigo-700 font-bold px-3 py-1.5 rounded-lg ml-2 transition-colors">进群聊</a>' if a["status"] == "已同意" else ""}</div></li>'''
        for a in my_apps])

    audits_html = "".join([
        f'''<li class="bg-yellow-50 p-3 rounded-lg border border-yellow-200 mb-3 text-sm"><div class="flex items-center gap-2 mb-2 text-yellow-900 cursor-pointer hover:underline" onclick="viewUserProfile('{a["applicant_phone"]}')"><div class="w-6 h-6 bg-yellow-500 rounded-full flex items-center justify-center text-white font-bold text-xs">{html.escape(a["applicant_name"])[0]}</div><strong class="text-lg">{html.escape(a["applicant_name"])}</strong></div><span class="text-xs opacity-75 block mb-1">申请: 《{html.escape(a["title"])}》</span>{f'<div class="text-[10px] text-orange-600 bg-orange-100 px-2 py-0.5 rounded-full inline-block mb-2 font-bold truncate max-w-full" title="{html.escape(a["applicant_honors"])}">🏆 荣誉: {html.escape(a["applicant_honors"])}</div>' if a["applicant_honors"] else ''}{f'<div class="flex gap-2 m-0 mt-1"><button type="button" onclick="submitAudit({a["app_id"]}, {a["proj_id"]}, \'{html.escape(a["applicant_name"])}\', \'accept\')" class="w-1/2 bg-green-100 hover:bg-green-600 hover:text-white text-green-700 transition-colors py-2 rounded-lg text-sm font-bold shadow-sm">同意进组</button><button type="button" onclick="submitAudit({a["app_id"]}, {a["proj_id"]}, \'{html.escape(a["applicant_name"])}\', \'reject\')" class="w-1/2 bg-red-100 hover:bg-red-600 hover:text-white text-red-600 transition-colors py-2 rounded-lg text-sm font-bold shadow-sm">婉拒</button></div>' if a["status"] == "待审核" else f'<div class="mt-2 flex justify-between items-center"><span class="text-xs font-bold text-gray-500">已处理 ({a["status"]})</span><form action="/hide_record" method="post" class="m-0 ajax-form"><input type="hidden" name="app_id" value="{a["app_id"]}"><input type="hidden" name="role" value="leader"><button type="submit" class="text-xs bg-gray-200 hover:bg-red-500 hover:text-white transition-colors px-2 py-1 rounded font-bold text-gray-600">删除记录</button></form></div>'}</li>'''
        for a in audits])

    return {
        "projects": projects_html or '<div class="text-center py-10 text-gray-400 bg-white rounded-xl">大厅空空如也 / 未找到匹配项目</div>',
        "my_projects": my_projects_html or '<div class="text-center py-10 text-gray-400">您暂未发布任何招募项目</div>',
        "recommend": recommend_html or '<div class="text-sm text-gray-400 py-2">完善技能标签后，即可解锁精准推荐！</div>',
        "my_apps": my_apps_html or '<li class="text-gray-400 text-sm text-center py-4">暂无记录</li>',
        "audits": audits_html or '<li class="text-gray-400 text-sm text-center py-4">暂无待处理</li>',
        "audit_count": audit_count,
        "apply_count": apply_count
    }


@app.get("/api/dashboard_html")
async def api_dashboard_html(request: Request, q: str = "", tag: str = ""):
    user = get_current_user(request)
    if not user: return JSONResponse({"error": "unauthorized"})
    return JSONResponse(get_dashboard_panels(dict(user), search_q=q, search_tag=tag))


@app.post("/api/mark_read")
async def mark_read(request: Request, type: str = Form(...)):
    user = get_current_user(request)
    if not user: return JSONResponse({"msg": "请先登录"})
    with sqlite3.connect(DB_FILE, timeout=10) as conn:
        if type == 'audit':
            conn.execute(
                "UPDATE applications SET leader_read=1 WHERE id IN (SELECT a.id FROM applications a JOIN projects p ON a.proj_id=p.id WHERE p.leader_phone=? AND a.status='待审核')",
                (user['phone'],))
        elif type == 'apply':
            conn.execute(
                "UPDATE applications SET applicant_read=1 WHERE applicant_phone=? AND status IN ('已同意', '已拒绝', '已移出')",
                (user['phone'],))
        conn.commit()
    return JSONResponse({"status": "ok"})


def alert_and_redirect(msg: str, url: str = "/"):
    return HTMLResponse(f"<script>alert('{msg}'); window.location.href='{url}';</script>")


GLOBAL_JS = """
    let myPhone = '{myPhone}';
    let lastMsgId = localStorage.getItem(`lastMsgId_${myPhone}`) || 0;
    let isLoggedOut = false; // 防抖标志

    function viewUserProfile(phone) {
        fetch('/api/user/' + phone).then(res => res.json()).then(data => {
            document.getElementById('view-avatar').innerText = data.name[0];
            document.getElementById('view-name').innerText = data.name;
            if(data.name === data.student_id) { document.getElementById('view-name').innerText += " (默认昵称)"; }
            document.getElementById('view-edu').innerText = (data.college + ' ' + data.major + ' ' + data.class_name).trim() || '未填写教育信息';
            document.getElementById('view-skills').innerText = data.skills ? ('标签: ' + data.skills) : '暂未填写技能标签';

            let honNode = document.getElementById('view-honors');
            if(data.honors) { honNode.innerText = '🏆 荣誉: ' + data.honors; honNode.style.display = 'inline-block'; } 
            else { honNode.style.display = 'none'; }

            let chatBtn = document.getElementById('btn-start-chat');
            if (phone === myPhone) { chatBtn.style.display = 'none'; } else { chatBtn.style.display = 'block'; chatBtn.onclick = () => window.location.href = `/chat?type=private&id=${phone}`; }
            document.getElementById('view-profile-modal').classList.remove('hidden');
        });
    }

    window.openModalAndMarkRead = function(modalId, type) {
        document.getElementById(modalId).classList.remove('hidden');
        if(type) {
            let fd = new FormData(); fd.append('type', type);
            fetch('/api/mark_read', {method: 'POST', body: fd}).then(() => refreshDashboard());
        }
    };

    window.closePwdModal = function() {
        document.getElementById('pwd-modal').classList.add('hidden');
        document.getElementById('pwd-form').reset();
    };

    function showToast(sender, content, type) {
        let container = document.getElementById('toast-container');
        let toast = document.createElement('div');
        toast.className = 'bg-white p-4 rounded-xl shadow-2xl border-l-4 border-indigo-500 mb-3 transform transition-all duration-300 translate-x-full';
        toast.innerHTML = `<div class="font-bold text-sm text-gray-800">${sender}</div><div class="text-xs text-gray-600 mt-1">${content}</div>`;
        container.appendChild(toast);
        setTimeout(() => toast.classList.remove('translate-x-full'), 50);
        setTimeout(() => { toast.classList.add('opacity-0'); setTimeout(() => toast.remove(), 300); }, 3500);
    }

    function renderMarkdown() {
        if(typeof marked !== 'undefined') {
            document.querySelectorAll('.markdown-content').forEach(el => { el.innerHTML = marked.parse(el.getAttribute('data-md')); });
        }
    }

    document.addEventListener('submit', async function(e) {
        let form = e.target;
        if(form.tagName === 'FORM' && form.classList.contains('ajax-form')) {
            e.preventDefault();
            if(form.hasAttribute('data-confirm') && !confirm(form.getAttribute('data-confirm'))) return;

            let formData = new FormData(form);
            let res = await fetch(form.action, { method: 'POST', body: formData });
            let data = await res.json();
            if(data.msg) {
                showToast('🚀 系统提示', data.msg, 'system');
                if(form.action.includes('/create')) document.getElementById('create-modal').classList.add('hidden');
                if(form.action.includes('/update_profile')) document.getElementById('profile-modal').classList.add('hidden');
                if(form.action.includes('/change_password') && data.msg.includes('成功')) {
                    closePwdModal(); 
                }
                refreshDashboard();
            }
        }
    });

    window.submitAudit = async function(appId, projId, name, action) {
        let fd = new FormData();
        fd.append('app_id', appId); fd.append('proj_id', projId); fd.append('applicant_name', name); fd.append('action', action);
        let res = await fetch('/audit', { method: 'POST', body: fd });
        let data = await res.json();
        showToast('🚀 系统审核提示', data.msg, 'system');
        refreshDashboard();
    };

    function refreshDashboard() {
        if(isLoggedOut) return;
        let searchQ = document.getElementById('search-q') ? document.getElementById('search-q').value : "";
        let searchTag = document.getElementById('search-tag') ? document.getElementById('search-tag').value : "";
        fetch(`/api/dashboard_html?q=${searchQ}&tag=${searchTag}`).then(res => res.json()).then(data => {
            if(data.error) return;
            let p = document.getElementById('projects-container'), a = document.getElementById('audits-container'), m = document.getElementById('apply-container'), r = document.getElementById('recommend-container'), my = document.getElementById('my-projects-container');
            if (p) { p.innerHTML = data.projects; renderMarkdown(); }
            if (a) a.innerHTML = data.audits;
            if (m) m.innerHTML = data.my_apps;
            if (r) r.innerHTML = data.recommend;
            if (my) my.innerHTML = data.my_projects;

            let ab = document.getElementById('audit-badge'), mb = document.getElementById('apply-badge');
            if(ab) { if(data.audit_count > 0) { ab.innerText = data.audit_count; ab.classList.remove('hidden'); } else { ab.classList.add('hidden'); } }
            if(mb) { if(data.apply_count > 0) { mb.innerText = data.apply_count; mb.classList.remove('hidden'); } else { mb.classList.add('hidden'); } }
        });
    }

    setInterval(() => {
        if(isLoggedOut) return;

        fetch('/api/check_session').then(res => res.json()).then(data => {
            if (data.status === 'logged_out') { 
                isLoggedOut = true;
                document.cookie = 'session_token=; Max-Age=0; path=/'; 
                alert('🚨 登录已过期或在别处登录，请重新登录！');
                window.location.href = '/login'; 
            }
        });

        fetch(`/api/poll_new?since_id=${lastMsgId}`).then(res => res.json()).then(msgs => {
            if (msgs.length > 0) {
                let maxId = parseInt(lastMsgId);
                msgs.forEach(m => {
                    if (m.id > maxId) maxId = m.id;
                    let isWatchingCurrent = window.location.href.includes(`/chat?type=${m.chat_type}&id=${m.chat_type === 'group' ? m.target_id : m.sender_phone}`);
                    if(!isWatchingCurrent) {
                        showToast(m.sender_name || '系统', m.content, m.chat_type);
                        let badge = document.getElementById('msg-badge'); if(badge) badge.classList.remove('hidden');
                    }
                });
                lastMsgId = maxId; localStorage.setItem(`lastMsgId_${myPhone}`, lastMsgId);
                if(typeof loadMessages === "function") loadMessages();
                if(typeof loadChatList === "function") loadChatList();
            }
        });

        // 🌟 修改：轮询智能推荐更新 (纯净后端驱动版)
        if (window.location.pathname === '/') {
            fetch(`/api/poll_recommend`).then(res => res.json()).then(data => {
                if (data.items && data.items.length > 0) {
                    data.items.forEach(proj => {
                        showToast('🤖 智能推荐', `刚刚发布了与您技能匹配的新项目：<br><span class="text-indigo-600 font-bold">《${proj.title}》</span>`, 'system');
                    });
                    refreshDashboard(); // 发现新项目才刷新面板
                }
            });
        }
    }, 2500);
"""


# --- 4. 账号操作路由 ---
@app.get("/login", response_class=HTMLResponse)
async def login_page():
    return """
    <!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8"><title>通行证</title><script src="https://cdn.tailwindcss.com"></script>
    <script>
        function toggleForm(id) { document.querySelectorAll('form').forEach(f=>f.classList.add('hidden')); document.getElementById(id).classList.remove('hidden'); }
        function toggleEye(inputId, iconId) {
            let p = document.getElementById(inputId); let i = document.getElementById(iconId);
            if(p.type==='password'){ p.type='text'; i.innerHTML='🙈'; } else { p.type='password'; i.innerHTML='👁️'; }
        }
    </script>
    </head><body class="bg-slate-50 h-screen flex items-center justify-center font-sans p-4">
        <div class="bg-white p-8 rounded-2xl shadow-xl w-full max-w-md border border-gray-100 relative overflow-hidden">
            <div class="absolute top-0 left-0 w-full h-2 bg-indigo-500"></div>

            <form id="login-form" action="/do_login" method="post" class="space-y-4">
                <h1 class="text-3xl font-black text-center text-indigo-600 mb-6">校园组队平台</h1>
                <input type="text" name="username" placeholder="学号 (12位) / 手机号" required class="w-full border p-3 rounded-lg outline-none focus:ring-2 ring-indigo-400">
                <div class="relative">
                    <input type="password" id="lp" name="password" placeholder="密码" required class="w-full border p-3 rounded-lg outline-none pr-10 focus:ring-2 ring-indigo-400">
                    <span id="le" onclick="toggleEye('lp','le')" class="absolute right-3 top-3 cursor-pointer opacity-60 hover:opacity-100 transition text-xl">👁️</span>
                </div>
                <button type="submit" class="w-full bg-indigo-600 text-white py-3 rounded-lg font-bold shadow-md hover:bg-indigo-700 transition">立即登录</button>
                <div class="flex justify-end text-sm mt-4">
                    <a href="#" onclick="toggleForm('reg-form')" class="text-indigo-600 font-bold hover:underline">没有账号？去注册</a>
                </div>
            </form>

            <form id="reg-form" action="/do_register" method="post" class="space-y-3 hidden">
                <h2 class="text-xl font-bold text-center mb-4 border-b pb-2">新用户注册</h2>
                <input type="text" name="student_id" placeholder="学号 (必须为12位数字)" minlength="12" maxlength="12" pattern="\d{12}" required class="w-full border p-2.5 rounded-lg outline-none focus:ring-2 ring-green-400">
                <input type="text" name="name" placeholder="真实姓名 / 昵称" required class="w-full border p-2.5 rounded-lg outline-none focus:ring-2 ring-green-400">
                <input type="tel" name="phone" placeholder="手机号码" required class="w-full border p-2.5 rounded-lg outline-none focus:ring-2 ring-green-400">
                <div class="relative">
                    <input type="password" id="rp1" name="password" placeholder="设置密码" required class="w-full border p-2.5 rounded-lg outline-none pr-10 focus:ring-2 ring-green-400">
                    <span id="re1" onclick="toggleEye('rp1','re1')" class="absolute right-3 top-2.5 cursor-pointer opacity-60 hover:opacity-100 transition text-xl">👁️</span>
                </div>
                <div class="relative">
                    <input type="password" id="rp2" name="confirm_password" placeholder="再次确认密码" required class="w-full border p-2.5 rounded-lg outline-none pr-10 focus:ring-2 ring-green-400">
                    <span id="re2" onclick="toggleEye('rp2','re2')" class="absolute right-3 top-2.5 cursor-pointer opacity-60 hover:opacity-100 transition text-xl">👁️</span>
                </div>
                <button type="submit" class="w-full bg-green-600 text-white py-2.5 rounded-lg font-bold hover:bg-green-700 transition mt-2">注册并登录</button>
                <div class="text-center text-sm mt-2"><a href="#" onclick="toggleForm('login-form')" class="text-gray-500 hover:underline">返回登录</a></div>
            </form>
        </div>
    </body></html>
    """


@app.post("/do_register")
async def do_register(student_id: str = Form(...), name: str = Form(...), phone: str = Form(...),
                      password: str = Form(...), confirm_password: str = Form(...)):
    if not student_id.isdigit() or len(student_id) != 12: return alert_and_redirect("学号必须严格为12位纯数字！",
                                                                                    "/login")
    if password != confirm_password: return alert_and_redirect("两次输入的密码不一致！", "/login")
    new_sess = generate_session_id()
    with sqlite3.connect(DB_FILE, timeout=10) as conn:
        try:
            conn.execute("INSERT INTO users (phone, name, password, student_id, is_first_login) VALUES (?, ?, ?, ?, 1)",
                         (phone, name, hash_password(password), student_id))
            conn.execute("DELETE FROM user_sessions WHERE phone = ?", (phone,))
            conn.execute("INSERT INTO user_sessions (session_id, phone) VALUES (?, ?)", (new_sess, phone))
            conn.commit()
        except sqlite3.IntegrityError:
            return alert_and_redirect("手机号或学号已被注册过！", "/login")
    res = RedirectResponse(url="/", status_code=303);
    res.set_cookie("session_token", new_sess, max_age=604800);
    return res


@app.post("/do_login")
async def do_login(username: str = Form(...), password: str = Form(...)):
    new_sess = generate_session_id()
    with sqlite3.connect(DB_FILE, timeout=10) as conn:
        user = conn.cursor().execute("SELECT phone FROM users WHERE (phone=? OR student_id=?) AND password=?",
                                     (username, username, hash_password(password))).fetchone()
        if user:
            phone = user[0]
            conn.execute("DELETE FROM user_sessions WHERE phone = ?", (phone,))
            conn.execute("INSERT INTO user_sessions (session_id, phone) VALUES (?, ?)", (new_sess, phone))
            conn.commit()
            res = RedirectResponse(url="/", status_code=303);
            res.set_cookie("session_token", new_sess, max_age=604800);
            return res
        return alert_and_redirect("❌ 账号或密码错误！", "/login")


# 🌟 修复遗漏的核心路由：内部验证并修改密码
@app.post("/change_password")
async def change_password(request: Request, student_id: str = Form(...), phone: str = Form(...),
                          old_password: str = Form(...), new_password: str = Form(...),
                          confirm_new_password: str = Form(...)):
    user = get_current_user(request)
    if not user: return JSONResponse({"msg": "登录已过期，请刷新页面！"})
    if new_password != confirm_new_password: return JSONResponse({"msg": "两次输入的新密码不一致！"})
    if user['phone'] != phone or user['student_id'] != student_id: return JSONResponse(
        {"msg": "填写的学号或手机号与当前登录账号不匹配，无法修改！"})

    with sqlite3.connect(DB_FILE, timeout=10) as conn:
        curr = conn.cursor().execute("SELECT phone FROM users WHERE phone=? AND password=?",
                                     (phone, hash_password(old_password))).fetchone()
        if not curr: return JSONResponse({"msg": "❌ 您输入的旧密码错误！"})

        conn.execute("UPDATE users SET password=? WHERE phone=?", (hash_password(new_password), phone))
        conn.commit()
    return JSONResponse({"msg": "✅ 密码修改成功！下次请使用新密码登录。"})


@app.get("/logout")
async def logout(request: Request):
    session_id = request.cookies.get("session_token")
    if session_id:
        with sqlite3.connect(DB_FILE, timeout=10) as conn:
            conn.execute("DELETE FROM user_sessions WHERE session_id = ?", (session_id,));
            conn.commit()
    res = alert_and_redirect("已安全退出本设备！", "/login")
    res.delete_cookie("session_token")
    return res


# --- 5. 主大厅 ---
@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    user = get_current_user(request)
    if not user: return RedirectResponse(url="/login", status_code=303)
    my_phone, my_name = user['phone'], user['name']

    with sqlite3.connect(DB_FILE, timeout=10) as conn:
        if user['is_first_login']: conn.execute("UPDATE users SET is_first_login=0 WHERE phone=?",
                                                (my_phone,)); conn.commit()

    college, major, class_name = user['college'] or "", user['major'] or "", user['class_name'] or ""
    qq, wechat, bio, skills, honors = user['qq'] or "", user['wechat'] or "", user['bio'] or "", user['skills'] or "", \
                                      user['honors'] or ""

    panels = get_dashboard_panels(dict(user))

    html_template = f"""
    <!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8"><title>校园组队平台</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
    <script>{GLOBAL_JS.replace('{myPhone}', my_phone)}</script>
    <script>
        window.onload = () => {{ 
            renderMarkdown(); 
            if ({user['is_first_login']} === 1) document.getElementById('profile-modal').classList.remove('hidden'); 
        }};
        function toggleEye(inputId, iconId) {{
            let p = document.getElementById(inputId); let i = document.getElementById(iconId);
            if(p.type==='password'){{ p.type='text'; i.innerHTML='🙈'; }} else {{ p.type='password'; i.innerHTML='👁️'; }}
        }}
    </script>
    </head>
    <body class="bg-slate-50 p-4 md:p-8 font-sans">
        <div id="toast-container" class="fixed bottom-5 right-5 z-50 w-80 flex flex-col justify-end pointer-events-none"></div>

        <div class="max-w-6xl mx-auto space-y-6">
            <header class="bg-white p-4 rounded-2xl shadow-sm border border-gray-100 flex justify-between items-center relative overflow-hidden">
                <div class="absolute top-0 left-0 w-2 h-full bg-indigo-500"></div>
                <h1 class="text-2xl font-black text-transparent bg-clip-text bg-gradient-to-r from-blue-600 to-indigo-600 ml-4 hidden md:block">🚀 校园组队平台</h1>

                <div class="flex items-center gap-2 bg-gray-50 p-2 pr-4 rounded-xl border border-gray-200 ml-auto flex-wrap justify-end">
                    <button onclick="document.getElementById('create-modal').classList.remove('hidden')" class="text-sm font-bold bg-indigo-50 text-indigo-600 hover:bg-indigo-500 hover:text-white px-3 py-1.5 rounded-lg transition-colors">➕ 发布招募</button>
                    <button onclick="openModalAndMarkRead('audit-modal', 'audit')" class="relative text-sm font-bold bg-blue-50 text-blue-600 hover:bg-blue-500 hover:text-white px-3 py-1.5 rounded-lg transition-colors">
                        🛡️ 队长审批<span id="audit-badge" class="absolute -top-1 -right-1 bg-red-500 text-white text-[10px] px-1.5 py-0.5 rounded-full {'hidden' if panels['audit_count'] == 0 else ''}">{panels['audit_count']}</span>
                    </button>
                    <button onclick="openModalAndMarkRead('apply-modal', 'apply')" class="relative text-sm font-bold bg-green-50 text-green-600 hover:bg-green-500 hover:text-white px-3 py-1.5 rounded-lg transition-colors">
                        🙋‍♂️ 我的申请<span id="apply-badge" class="absolute -top-1 -right-1 bg-red-500 text-white text-[10px] px-1.5 py-0.5 rounded-full {'hidden' if panels['apply_count'] == 0 else ''}">{panels['apply_count']}</span>
                    </button>
                    <button onclick="document.getElementById('my-projects-modal').classList.remove('hidden')" class="text-sm font-bold bg-purple-50 text-purple-600 hover:bg-purple-500 hover:text-white px-3 py-1.5 rounded-lg transition-colors">📝 我的招募</button>
                    <button onclick="document.getElementById('profile-modal').classList.remove('hidden')" class="text-sm font-bold bg-gray-100 text-gray-600 hover:bg-gray-500 hover:text-white px-3 py-1.5 rounded-lg transition-colors">👤 个人信息</button>

                    <a href="/chat" class="relative text-sm font-bold bg-gray-100 text-gray-600 hover:bg-gray-500 hover:text-white px-3 py-1.5 rounded-lg transition-colors">
                        💬 消息<span id="msg-badge" class="absolute -top-1 -right-1 flex h-3 w-3 hidden"><span class="animate-ping absolute inline-flex h-full w-full rounded-full bg-red-400 opacity-75"></span><span class="relative inline-flex rounded-full h-3 w-3 bg-red-500 border border-white"></span></span>
                    </a>

                    <div class="w-px h-5 bg-gray-300 mx-2"></div>

                    <div class="w-8 h-8 bg-indigo-500 rounded-full flex items-center justify-center text-white font-bold cursor-pointer hover:ring-4 ring-indigo-200 shadow-sm transition-all" onclick="document.getElementById('profile-modal').classList.remove('hidden')">{html.escape(my_name)[0]}</div>

                    <button onclick="document.getElementById('pwd-modal').classList.remove('hidden')" class="text-sm font-bold text-gray-600 hover:bg-indigo-500 hover:text-white px-3 py-1.5 rounded-lg transition-colors ml-2">修改密码</button>
                    <a href="/logout" class="text-sm font-bold text-gray-400 hover:bg-red-500 hover:text-white px-3 py-1.5 rounded-lg transition-colors ml-1">退出</a>
                </div>
            </header>

            <div class="bg-white p-4 rounded-xl shadow-sm flex gap-4 border border-gray-100">
                <input type="text" id="search-q" placeholder="🔍 搜索项目名称或详情..." class="border p-2 rounded flex-1 text-sm outline-none focus:ring-2 focus:ring-indigo-300">
                <input type="text" id="search-tag" placeholder="🏷️ 标签筛选 (如: Python)" class="border p-2 rounded w-1/4 text-sm outline-none focus:ring-2 focus:ring-indigo-300">
                <button onclick="refreshDashboard()" class="bg-indigo-100 text-indigo-700 hover:bg-indigo-600 hover:text-white px-6 py-2 rounded-lg font-bold transition-colors shadow-sm">快速搜索</button>
            </div>

            <div class="grid grid-cols-1 md:grid-cols-12 gap-6 items-start">
                <div class="md:col-span-8">
                    <h2 class="text-xl font-bold mb-4 border-l-4 border-indigo-500 pl-3">公共招募大厅</h2>
                    <div id="projects-container">{panels['projects']}</div>
                </div>
                <div class="md:col-span-4 space-y-4 sticky top-6">
                    <div class="bg-gradient-to-br from-yellow-50 to-orange-50 p-6 rounded-2xl shadow-sm border border-yellow-200">
                        <h2 class="font-bold mb-4 border-b border-yellow-300 pb-2 text-yellow-800">💡 猜你喜欢 (智能推荐)</h2>
                        <div id="recommend-container" class="space-y-2">{panels['recommend']}</div>
                    </div>
                </div>
            </div>
        </div>

        <div id="create-modal" class="hidden fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50 p-4">
            <div class="bg-white p-6 rounded-2xl shadow-2xl w-full max-w-lg relative">
                <button onclick="document.getElementById('create-modal').classList.add('hidden')" class="absolute top-4 right-4 text-gray-400 hover:text-gray-800 transition-colors font-bold text-2xl z-10">&times;</button>
                <h2 class="text-xl font-bold mb-4 text-gray-800 border-b pb-3">➕ 发布新队伍招募</h2>
                <form action="/create" method="post" class="space-y-3 m-0 ajax-form">
                    <input type="text" name="title" required placeholder="项目名称" class="w-full p-2.5 bg-gray-50 border rounded text-sm outline-none focus:ring-2 focus:ring-indigo-300">
                    <div class="flex gap-3">
                        <input type="number" name="base_members" required placeholder="已有人数" min="1" class="w-1/2 p-2.5 bg-gray-50 border rounded text-sm outline-none focus:ring-2 focus:ring-indigo-300">
                        <input type="number" name="required_members" required placeholder="共需人数" min="2" class="w-1/2 p-2.5 bg-gray-50 border rounded text-sm outline-none focus:ring-2 focus:ring-indigo-300">
                    </div>
                    <input type="text" name="tags" placeholder="技术标签 (逗号分隔)" class="w-full p-2.5 bg-gray-50 border rounded text-sm outline-none focus:ring-2 focus:ring-indigo-300">
                    <textarea name="description" required placeholder="支持 Markdown 语法排版..." class="w-full p-2.5 bg-gray-50 border rounded text-sm h-32 outline-none resize-none focus:ring-2 focus:ring-indigo-300"></textarea>
                    <button type="submit" class="w-full bg-indigo-100 text-indigo-700 hover:bg-indigo-600 hover:text-white transition-colors py-2.5 rounded-lg font-bold mt-2">立即发布</button>
                </form>
            </div>
        </div>

        <div id="audit-modal" class="hidden fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50 p-4">
            <div class="bg-white p-6 rounded-2xl shadow-2xl w-full max-w-xl max-h-[80vh] flex flex-col relative">
                <button onclick="document.getElementById('audit-modal').classList.add('hidden')" class="absolute top-4 right-4 text-gray-400 hover:text-gray-800 transition-colors font-bold text-2xl z-10">&times;</button>
                <h2 class="text-xl font-bold mb-4 text-gray-800 border-b pb-3">🛡️ 队长审批与记录</h2>
                <ul id="audits-container" class="flex-1 overflow-y-auto m-0 p-0 pr-2 space-y-3">{panels['audits']}</ul>
            </div>
        </div>

        <div id="apply-modal" class="hidden fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50 p-4">
            <div class="bg-white p-6 rounded-2xl shadow-2xl w-full max-w-xl max-h-[80vh] flex flex-col relative">
                <button onclick="document.getElementById('apply-modal').classList.add('hidden')" class="absolute top-4 right-4 text-gray-400 hover:text-gray-800 transition-colors font-bold text-2xl z-10">&times;</button>
                <h2 class="text-xl font-bold mb-4 text-gray-800 border-b pb-3">🙋‍♂️ 我的申请记录</h2>
                <ul id="apply-container" class="flex-1 overflow-y-auto m-0 p-0 pr-2 space-y-3">{panels['my_apps']}</ul>
            </div>
        </div>

        <div id="my-projects-modal" class="hidden fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50 p-4">
            <div class="bg-white p-6 rounded-2xl shadow-2xl w-full max-w-2xl max-h-[80vh] flex flex-col relative">
                <button onclick="document.getElementById('my-projects-modal').classList.add('hidden')" class="absolute top-4 right-4 text-gray-400 hover:text-gray-800 transition-colors font-bold text-2xl z-10">&times;</button>
                <h2 class="text-xl font-bold mb-4 text-gray-800 border-b pb-3">📝 我的招募管理</h2>
                <ul id="my-projects-container" class="flex-1 overflow-y-auto m-0 p-0 pr-2 space-y-3">{panels['my_projects']}</ul>
            </div>
        </div>

        <div id="profile-modal" class="hidden fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50 p-4">
            <div class="bg-white p-6 rounded-2xl shadow-2xl w-full max-w-lg relative">
                <button onclick="document.getElementById('profile-modal').classList.add('hidden')" class="absolute top-4 right-4 text-gray-400 hover:text-gray-800 transition-colors font-bold text-2xl z-10">&times;</button>
                <h2 class="text-xl font-bold mb-4 text-gray-800 border-b pb-3">👤 完善个人资料与标签</h2>
                <form action="/update_profile" method="post" class="space-y-4 ajax-form">
                    <div class="grid grid-cols-2 gap-4">
                        <div><label class="block text-xs font-bold text-gray-500 mb-1">真实姓名/昵称</label><input type="text" name="name" value="{html.escape(my_name)}" required class="w-full border p-2 rounded outline-none text-sm focus:ring-2 focus:ring-indigo-300"></div>
                        <div><label class="block text-xs font-bold text-gray-500 mb-1">学院</label><input type="text" name="college" value="{html.escape(college)}" class="w-full border p-2 rounded outline-none text-sm focus:ring-2 focus:ring-indigo-300"></div>
                    </div>
                    <div class="grid grid-cols-2 gap-4">
                        <div><label class="block text-xs font-bold text-gray-500 mb-1">专业</label><input type="text" name="major" value="{html.escape(major)}" class="w-full border p-2 rounded outline-none text-sm focus:ring-2 focus:ring-indigo-300"></div>
                        <div><label class="block text-xs font-bold text-gray-500 mb-1">班级</label><input type="text" name="class_name" value="{html.escape(class_name)}" class="w-full border p-2 rounded outline-none text-sm focus:ring-2 focus:ring-indigo-300"></div>
                    </div>
                    <div class="grid grid-cols-2 gap-4">
                        <div><label class="block text-xs font-bold text-indigo-500 mb-1">技能/兴趣标签 (逗号分隔)</label><input type="text" name="skills" value="{html.escape(skills)}" placeholder="例如: Python, 写作" class="w-full border border-indigo-300 bg-indigo-50 p-2 rounded outline-none text-sm focus:ring-2 focus:ring-indigo-500"></div>
                        <div><label class="block text-xs font-bold text-gray-500 mb-1">QQ号</label><input type="text" name="qq" value="{html.escape(qq)}" class="w-full border p-2 rounded outline-none text-sm focus:ring-2 focus:ring-indigo-300"></div>
                    </div>
                    <div class="grid grid-cols-2 gap-4">
                        <div><label class="block text-xs font-bold text-gray-500 mb-1">微信号</label><input type="text" name="wechat" value="{html.escape(wechat)}" class="w-full border p-2 rounded outline-none text-sm focus:ring-2 focus:ring-indigo-300"></div>
                        <div><label class="block text-xs font-bold text-orange-500 mb-1">个人荣誉 (选填)</label><input type="text" name="honors" value="{html.escape(honors)}" placeholder="如: 国家奖学金" class="w-full border border-orange-300 bg-orange-50 p-2 rounded outline-none text-sm focus:ring-2 focus:ring-orange-400"></div>
                    </div>
                    <div><label class="block text-xs font-bold text-gray-500 mb-1">个性签名</label><textarea name="bio" class="w-full border p-2 rounded outline-none h-12 resize-none text-sm focus:ring-2 focus:ring-indigo-300">{html.escape(bio)}</textarea></div>

                    <div class="flex justify-end gap-3 mt-4 pt-4 border-t">
                        <button type="button" onclick="document.getElementById('profile-modal').classList.add('hidden')" class="px-5 py-2 bg-gray-100 hover:bg-gray-300 text-gray-600 transition-colors rounded-lg font-bold">稍后再说</button>
                        <button type="submit" class="px-5 py-2 bg-indigo-100 text-indigo-700 hover:bg-indigo-600 hover:text-white transition-colors rounded-lg font-bold shadow-sm">保存并关闭</button>
                    </div>
                </form>
            </div>
        </div>

        <div id="pwd-modal" class="hidden fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50 p-4">
            <div class="bg-white p-6 rounded-2xl shadow-2xl w-full max-w-sm relative">
                <button onclick="closePwdModal()" class="absolute top-4 right-4 text-gray-400 hover:text-gray-800 transition-colors font-bold text-2xl z-10">&times;</button>
                <h2 class="text-xl font-bold mb-4 text-gray-800 border-b pb-3">🔒 修改密码</h2>
                <form id="pwd-form" action="/change_password" method="post" class="space-y-4 ajax-form">
                    <input type="text" name="student_id" placeholder="当前账号验证: 学号" required class="w-full border p-2.5 rounded-lg outline-none text-sm focus:ring-2 focus:ring-indigo-300">
                    <input type="tel" name="phone" placeholder="当前账号验证: 手机号" required class="w-full border p-2.5 rounded-lg outline-none text-sm focus:ring-2 focus:ring-indigo-300">

                    <div class="relative">
                        <input type="password" id="cp0" name="old_password" placeholder="原密码" required class="w-full border p-2.5 rounded-lg outline-none text-sm pr-10 focus:ring-2 ring-indigo-300">
                        <span id="ce0" onclick="toggleEye('cp0','ce0')" class="absolute right-3 top-2.5 cursor-pointer opacity-60 hover:opacity-100 transition text-lg">👁️</span>
                    </div>
                    <div class="relative">
                        <input type="password" id="cp1" name="new_password" placeholder="新密码" required class="w-full border p-2.5 rounded-lg outline-none text-sm pr-10 focus:ring-2 focus:ring-indigo-300">
                        <span id="ce1" onclick="toggleEye('cp1','ce1')" class="absolute right-3 top-2.5 cursor-pointer opacity-60 hover:opacity-100 transition text-lg">👁️</span>
                    </div>
                    <div class="relative">
                        <input type="password" id="cp2" name="confirm_new_password" placeholder="再次确认新密码" required class="w-full border p-2.5 rounded-lg outline-none text-sm pr-10 focus:ring-2 focus:ring-indigo-300">
                        <span id="ce2" onclick="toggleEye('cp2','ce2')" class="absolute right-3 top-2.5 cursor-pointer opacity-60 hover:opacity-100 transition text-lg">👁️</span>
                    </div>
                    <button type="submit" class="w-full bg-indigo-500 hover:bg-indigo-600 text-white transition-colors py-2.5 rounded-lg font-bold mt-2">确认修改</button>
                </form>
            </div>
        </div>

        <div id="view-profile-modal" class="hidden fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50 p-4">
            <div class="bg-white p-8 rounded-2xl shadow-2xl w-full max-w-sm text-center relative overflow-hidden"><button onclick="document.getElementById('view-profile-modal').classList.add('hidden')" class="absolute top-3 right-4 text-gray-400 hover:text-gray-800 transition-colors font-bold text-2xl z-10">&times;</button><div class="w-20 h-20 bg-indigo-500 rounded-full flex items-center justify-center mx-auto shadow-lg mb-4 mt-4 text-white text-4xl font-black cursor-pointer hover:ring-4 ring-indigo-200 transition-all" id="view-avatar">?</div><h2 class="text-2xl font-black text-gray-800 mb-1" id="view-name">加载中...</h2><p class="text-sm text-gray-500 font-bold mb-2" id="view-edu"></p><p class="text-xs text-blue-600 bg-blue-50 inline-block px-3 py-1 rounded-full mb-2 font-bold" id="view-skills"></p><br><p class="text-xs text-orange-600 bg-orange-50 inline-block px-3 py-1 rounded-full mb-6 font-bold" id="view-honors"></p><button id="btn-start-chat" class="w-full bg-indigo-100 text-indigo-700 hover:bg-indigo-600 hover:text-white transition-colors font-bold py-3 rounded-xl shadow-md mb-2">💬 发起聊天</button></div>
        </div>
    </body></html>
    """
    return HTMLResponse(content=html_template)


@app.post("/update_profile")
async def update_profile(request: Request, name: str = Form(...), college: str = Form(""), major: str = Form(""),
                         class_name: str = Form(""), qq: str = Form(""), wechat: str = Form(""), bio: str = Form(""),
                         skills: str = Form(""), honors: str = Form("")):
    user = get_current_user(request)
    if not user: return JSONResponse({"msg": "登录已过期，请刷新页面重新登录！"})
    with sqlite3.connect(DB_FILE, timeout=10) as conn:
        conn.execute(
            'UPDATE users SET name=?, college=?, major=?, class_name=?, qq=?, wechat=?, bio=?, skills=?, honors=? WHERE phone=?',
            (name, college, major, class_name, qq, wechat, bio, skills, honors, user['phone']))
        conn.commit()
    return JSONResponse({"msg": "✅ 资料更新成功！"})


# --- 6. 聊天与业务逻辑 (全部增加空 user 拦截防崩) ---
@app.get("/api/chat_list")
async def get_chat_list(request: Request):
    user = get_current_user(request)
    if not user: return JSONResponse([])
    my_phone = user['phone']
    with sqlite3.connect(DB_FILE, timeout=10) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute('''SELECT id as target_id, title as name, 'group' as type
                          FROM projects
                          WHERE leader_phone = ?
                            AND is_deleted = 0
                          UNION
                          SELECT p.id, p.title, 'group'
                          FROM projects p
                                   JOIN applications a ON p.id = a.proj_id
                          WHERE a.applicant_phone = ?
                            AND a.status = '已同意'
                            AND p.is_deleted = 0''', (my_phone, my_phone))
        groups = [dict(c) for c in cursor.fetchall()]
        cursor.execute('''SELECT DISTINCT u.phone as target_id, u.name as name, 'private' as type
                          FROM messages m
                                   JOIN users u ON (u.phone = m.target_id AND m.sender_phone = ?) OR
                                                   (u.phone = m.sender_phone AND m.target_id = ?)
                          WHERE m.chat_type = 'private'
                            AND u.phone != ?''', (my_phone, my_phone, my_phone))
        privates = [dict(c) for c in cursor.fetchall()]
        all_chats = groups + privates
        for c in all_chats:
            state = cursor.execute(
                "SELECT last_read_msg_id, cleared_up_to_msg_id FROM chat_state WHERE phone=? AND chat_type=? AND target_id=?",
                (my_phone, c['type'], c['target_id'])).fetchone()
            last_read, cleared = state['last_read_msg_id'] if state else 0, state[
                'cleared_up_to_msg_id'] if state else 0
            if c['type'] == 'group':
                c['unread'] = cursor.execute(
                    "SELECT COUNT(*) FROM messages WHERE chat_type='group' AND target_id=? AND sender_phone!=? AND id>? AND id>?",
                    (c['target_id'], my_phone, last_read, cleared)).fetchone()[0]
            else:
                c['unread'] = cursor.execute(
                    "SELECT COUNT(*) FROM messages WHERE chat_type='private' AND sender_phone=? AND target_id=? AND id>? AND id>?",
                    (c['target_id'], my_phone, last_read, cleared)).fetchone()[0]
        return JSONResponse(all_chats)


@app.get("/chat", response_class=HTMLResponse)
async def chat_page(request: Request, type: str = "none", id: str = ""):
    user = get_current_user(request)
    if not user: return RedirectResponse("/login", status_code=303)
    my_phone = user['phone']
    members_html = ""
    with sqlite3.connect(DB_FILE, timeout=10) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        if type == 'group' and id:
            proj = cursor.execute("SELECT leader_phone FROM projects WHERE id = ?", (id,)).fetchone()
            is_leader = (proj and str(proj['leader_phone']) == str(my_phone))
            cursor.execute('''SELECT u.phone, u.name, '队长' as role
                              FROM projects p
                                       JOIN users u ON p.leader_phone = u.phone
                              WHERE p.id = ?
                              UNION
                              SELECT u.phone, u.name, '队员' as role
                              FROM applications a
                                       JOIN users u ON a.applicant_phone = u.phone
                              WHERE a.proj_id = ?
                                AND a.status = '已同意' ''', (id, id))
            for m in cursor.fetchall():
                kick_btn = f'''<button onclick="kickMember({id}, '{m["phone"]}')" class="text-[10px] bg-red-100 text-red-600 px-2 py-1 rounded hover:bg-red-600 hover:text-white transition-colors">移出</button>''' if is_leader and \
                                                                                                                                                                                                                        m[
                                                                                                                                                                                                                            'role'] != '队长' else ''
                members_html += f'''<li class="flex justify-between items-center bg-gray-50 p-2 rounded mb-2"><div class="flex items-center gap-2 cursor-pointer hover:underline" onclick="viewUserProfile('{m["phone"]}')"><div class="w-6 h-6 bg-indigo-200 text-indigo-700 rounded-full flex items-center justify-center text-xs font-bold shadow-sm">{html.escape(m["name"])[0]}</div><span class="text-sm font-bold text-gray-800">{html.escape(m["name"])}</span> <span class="text-[10px] bg-gray-200 px-1 rounded text-gray-500">{m["role"]}</span></div>{kick_btn}</li>'''

    html_template = f"""
    <!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8"><title>消息大厅</title><script src="https://cdn.tailwindcss.com"></script>
    <script>
        let myPhone = '{my_phone}'; let chatType = '{type}', targetId = '{id}'; let lastMsgId = localStorage.getItem(`lastMsgId_${{myPhone}}`) || 0;
        function viewUserProfile(phone) {{
            fetch('/api/user/' + phone).then(res => res.json()).then(data => {{
                document.getElementById('view-avatar').innerText = data.name[0];
                document.getElementById('view-name').innerText = data.name;
                document.getElementById('view-edu').innerText = (data.college + ' ' + data.major + ' ' + data.class_name).trim() || '未填写教育信息';
                document.getElementById('view-skills').innerText = data.skills ? ('标签: ' + data.skills) : '暂未填写技能标签';
                let honNode = document.getElementById('view-honors');
                if(data.honors) {{ honNode.innerText = '🏆 荣誉: ' + data.honors; honNode.style.display = 'inline-block'; }} else {{ honNode.style.display = 'none'; }}
                let chatBtn = document.getElementById('btn-start-chat');
                if (phone === myPhone) {{ chatBtn.style.display = 'none'; }} else {{ chatBtn.style.display = 'block'; chatBtn.onclick = () => window.location.href = `/chat?type=private&id=${{phone}}`; }}
                document.getElementById('view-profile-modal').classList.remove('hidden');
            }});
        }}
        function formatChatTime(dateString) {{
            let d = new Date(dateString.replace(' ', 'T') + 'Z'); let now = new Date();
            let isSameYear = d.getFullYear() === now.getFullYear();
            let isSameDay = isSameYear && d.getMonth() === now.getMonth() && d.getDate() === now.getDate();
            let yest = new Date(now); yest.setDate(now.getDate() - 1);
            let isYest = d.getFullYear() === yest.getFullYear() && d.getMonth() === yest.getMonth() && d.getDate() === yest.getDate();
            let timePart = d.getHours().toString().padStart(2, '0') + ':' + d.getMinutes().toString().padStart(2, '0');
            if (isSameDay) return '今天 ' + timePart; if (isYest) return '昨天 ' + timePart;
            let md = (d.getMonth() + 1).toString().padStart(2, '0') + '-' + d.getDate().toString().padStart(2, '0');
            if (isSameYear) return md + ' ' + timePart; return d.getFullYear() + '-' + md + ' ' + timePart;
        }}
        function loadChatList() {{
            fetch('/api/chat_list').then(res => res.json()).then(chats => {{
                if (chatType === 'private' && targetId && !chats.some(c => c.target_id == targetId)) chats.push({{target_id: targetId, name: '新会话', type: 'private', unread: 0}});
                let html = '';
                chats.forEach(c => {{
                    let active = (c.target_id == targetId && c.type == chatType) ? 'bg-indigo-100 border-l-4 border-indigo-500' : 'hover:bg-gray-100 border-l-4 border-transparent';
                    let icon = c.type === 'group' ? '👥' : '👤';
                    let badge = c.unread > 0 ? `<span class="bg-red-500 text-white text-[10px] px-1.5 py-0.5 rounded-full ml-2">${{c.unread}}</span>` : '';
                    let delBtn = `<button onclick="clearChat('${{c.type}}', '${{c.target_id}}'); event.preventDefault(); event.stopPropagation();" class="text-gray-400 hover:text-red-500 transition-colors ml-auto" title="清空记录">🗑️</button>`;
                    html += `<a href="/chat?type=${{c.type}}&id=${{c.target_id}}" class="p-4 ${{active}} transition-all border-b flex items-center justify-between group"><div class="font-bold text-gray-800 flex items-center">${{icon}} ${{c.name}} ${{badge}}</div><div class="opacity-0 group-hover:opacity-100">${{delBtn}}</div></a>`;
                }});
                document.getElementById('chat-list').innerHTML = html || '<div class="p-4 text-gray-400 text-sm text-center">暂无对话</div>';
            }});
        }}
        function loadMessages() {{
            if (chatType === 'none') return;
            fetch(`/api/messages?type=${{chatType}}&id=${{targetId}}`).then(res => res.json()).then(msgs => {{
                let box = document.getElementById('msg-box'); let html = '';
                msgs.forEach(m => {{
                    let tStr = formatChatTime(m.created_at);
                    if (m.sender_phone === 'system') {{ html += `<div class="text-center text-xs text-gray-400 my-4"><span class="bg-gray-100 px-3 py-1 rounded-full">${{tStr}} | 系统：${{m.content}}</span></div>`; }}
                    else if (m.sender_phone === myPhone) {{ html += `<div class="flex justify-end mb-4"><div class="flex flex-col items-end"><span class="text-[10px] text-gray-400 mb-1 mr-12">${{tStr}}</span><div class="flex items-start gap-2"><div class="bg-indigo-500 text-white p-3 rounded-xl rounded-tr-sm shadow-sm max-w-[280px] break-words text-sm">${{m.content}}</div><div class="w-9 h-9 bg-indigo-200 text-indigo-700 rounded-full flex items-center justify-center font-bold text-sm flex-shrink-0 cursor-pointer shadow-sm hover:ring-2 ring-indigo-300 transition" onclick="viewUserProfile('${{m.sender_phone}}')">${{m.sender_name[0]}}</div></div></div></div>`; }}
                    else {{ html += `<div class="flex justify-start mb-4"><div class="flex flex-col items-start"><div class="flex items-center gap-2 mb-1 ml-12"><span class="text-xs text-gray-500 font-bold">${{m.sender_name}}</span><span class="text-[10px] text-gray-400">${{tStr}}</span></div><div class="flex items-start gap-2"><div class="w-9 h-9 bg-white text-indigo-700 rounded-full flex items-center justify-center font-bold text-sm flex-shrink-0 cursor-pointer shadow-sm border hover:ring-2 ring-indigo-300 transition" onclick="viewUserProfile('${{m.sender_phone}}')">${{m.sender_name[0]}}</div><div class="bg-white border text-gray-800 p-3 rounded-xl rounded-tl-sm shadow-sm max-w-[280px] break-words text-sm">${{m.content}}</div></div></div></div>`; }}
                }});
                if (box.innerHTML !== html) {{ box.innerHTML = html; box.scrollTop = box.scrollHeight; }}
            }});
        }}
        function sendMessage() {{
            let input = document.getElementById('msg-input'), content = input.value.trim();
            if (!content || chatType === 'none') return;
            let fd = new FormData(); fd.append('type', chatType); fd.append('id', targetId); fd.append('content', content);
            fetch('/api/send_message', {{ method: 'POST', body: fd }}).then(() => {{ input.value = ''; loadMessages(); loadChatList(); }});
        }}
        function kickMember(projId, phone) {{ if(confirm("确定移出该成员吗？")) {{ let fd = new FormData(); fd.append('proj_id', projId); fd.append('target_phone', phone); fetch('/api/kick_member', {{ method: 'POST', body: fd }}).then(() => window.location.reload()); }} }}
        function clearChat(type, id) {{ if(confirm("确定清空此记录吗？(仅自己不可见)")) {{ let fd = new FormData(); fd.append('type', type); fd.append('id', id); fetch('/api/clear_chat', {{ method: 'POST', body: fd }}).then(() => {{ if (chatType === type && targetId === id) loadMessages(); loadChatList(); }}); }} }}
        window.onload = () => {{ loadChatList(); loadMessages(); document.getElementById('msg-input')?.addEventListener('keypress', function (e) {{ if (e.key === 'Enter') sendMessage(); }}); setInterval(() => {{ loadChatList(); loadMessages(); }}, 2500); }};
    </script>
    </head>
    <body class="bg-slate-100 h-screen flex flex-col font-sans overflow-hidden">
        <header class="bg-indigo-600 text-white p-4 flex justify-between items-center shadow-md z-10"><h1 class="text-xl font-bold flex items-center gap-2"><a href="/" class="hover:text-indigo-200 transition-colors">⬅ 返回大厅</a> | 💬 消息控制台</h1><div class="text-sm font-bold bg-indigo-700 px-3 py-1 rounded-full cursor-pointer hover:bg-indigo-800 transition" onclick="viewUserProfile('{my_phone}')">{html.escape(user['name'])}</div></header>
        <div class="flex flex-1 overflow-hidden">
            <div class="w-1/4 bg-white border-r flex flex-col h-full overflow-y-auto"><div class="p-3 bg-gray-50 text-xs font-bold text-gray-500 tracking-wider">会话列表</div><div id="chat-list" class="flex-1 overflow-y-auto"></div></div>
            <div class="w-2/4 bg-slate-50 flex flex-col relative">
                {'''<div class="flex-1 flex items-center justify-center text-gray-400 font-bold">请在左侧选择聊天</div>''' if type == 'none' else f'''
                <div class="bg-white border-b p-3 flex justify-between items-center shadow-sm z-10"><span class="font-bold text-gray-700 text-sm">当前会话</span><button onclick="clearChat('{type}', '{id}')" class="text-xs text-gray-400 hover:text-red-500 font-bold transition-colors flex items-center gap-1">🗑️ 清空记录</button></div>
                <div id="msg-box" class="flex-1 overflow-y-auto p-6 scroll-smooth"></div><div class="p-4 bg-white border-t flex gap-2"><input type="text" id="msg-input" placeholder="输入消息 (Enter 发送)..." class="flex-1 bg-gray-100 border-none rounded-xl px-4 py-3 outline-none focus:ring-2 ring-indigo-500"><button onclick="sendMessage()" class="bg-indigo-100 text-indigo-700 hover:bg-indigo-600 hover:text-white transition-colors px-6 rounded-xl font-bold">发送</button></div>'''}
            </div>
            {f'''<div class="w-1/4 bg-white border-l p-4 flex flex-col"><h3 class="font-bold text-gray-800 border-b pb-2 mb-4">👥 成员面板</h3><ul class="flex-1 overflow-y-auto m-0 p-0">{members_html}</ul></div>''' if type == 'group' else '<div class="w-1/4 bg-white border-l bg-gray-50 flex items-center justify-center text-gray-300">私聊模式下无侧边栏</div>'}
        </div>
        <div id="view-profile-modal" class="hidden fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50 p-4"><div class="bg-white p-8 rounded-2xl shadow-2xl w-full max-w-sm text-center relative overflow-hidden"><button onclick="document.getElementById('view-profile-modal').classList.add('hidden')" class="absolute top-3 right-4 text-gray-400 hover:text-gray-800 transition-colors font-bold text-2xl z-10">&times;</button><div class="w-20 h-20 bg-indigo-500 rounded-full flex items-center justify-center mx-auto shadow-lg mb-4 mt-4 text-white text-4xl font-black cursor-pointer hover:ring-4 ring-indigo-200 transition-all" id="view-avatar">?</div><h2 class="text-2xl font-black text-gray-800 mb-1" id="view-name">加载中...</h2><p class="text-sm text-gray-500 font-bold mb-2" id="view-edu"></p><p class="text-xs text-blue-600 bg-blue-50 inline-block px-3 py-1 rounded-full mb-2 font-bold" id="view-skills"></p><br><p class="text-xs text-orange-600 bg-orange-50 inline-block px-3 py-1 rounded-full mb-6 font-bold" id="view-honors"></p><button id="btn-start-chat" class="w-full bg-indigo-100 text-indigo-700 hover:bg-indigo-600 hover:text-white transition-colors font-bold py-3 rounded-xl shadow-md mb-2">💬 发起聊天</button></div></div>
    </body></html>
    """
    return HTMLResponse(content=html_template)


@app.get("/api/messages")
async def get_messages(request: Request, type: str, id: str):
    user = get_current_user(request)
    if not user: return JSONResponse([])
    with sqlite3.connect(DB_FILE, timeout=10) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        state = cursor.execute(
            "SELECT cleared_up_to_msg_id FROM chat_state WHERE phone=? AND chat_type=? AND target_id=?",
            (user['phone'], type, id)).fetchone()
        cleared_id = state['cleared_up_to_msg_id'] if state else 0
        if type == 'group':
            msgs = cursor.execute(
                "SELECT m.*, u.name as sender_name FROM messages m LEFT JOIN users u ON m.sender_phone = u.phone WHERE chat_type = 'group' AND target_id = ? AND m.id > ? ORDER BY created_at ASC",
                (id, cleared_id)).fetchall()
        else:
            msgs = cursor.execute(
                "SELECT m.*, u.name as sender_name FROM messages m LEFT JOIN users u ON m.sender_phone = u.phone WHERE chat_type = 'private' AND ((sender_phone = ? AND target_id = ?) OR (sender_phone = ? AND target_id = ?)) AND m.id > ? ORDER BY created_at ASC",
                (user['phone'], id, id, user['phone'], cleared_id)).fetchall()
        if msgs:
            max_id = msgs[-1]['id']
            exists = cursor.execute("SELECT 1 FROM chat_state WHERE phone=? AND chat_type=? AND target_id=?",
                                    (user['phone'], type, id)).fetchone()
            if exists:
                cursor.execute("UPDATE chat_state SET last_read_msg_id=? WHERE phone=? AND chat_type=? AND target_id=?",
                               (max_id, user['phone'], type, id))
            else:
                cursor.execute(
                    "INSERT INTO chat_state (phone, chat_type, target_id, last_read_msg_id) VALUES (?, ?, ?, ?)",
                    (user['phone'], type, id, max_id))
            conn.commit()

        # 安全转义处理
        res = []
        for m in msgs:
            d = dict(m)
            if d['sender_phone'] != 'system':
                d['content'] = html.escape(d['content'])
                d['sender_name'] = html.escape(d['sender_name'])
            res.append(d)
        return JSONResponse(res)


@app.post("/api/send_message")
async def send_message(request: Request, type: str = Form(...), id: str = Form(...), content: str = Form(...)):
    user = get_current_user(request)
    if not user: return JSONResponse({"msg": "登录已过期，请刷新！"})
    with sqlite3.connect(DB_FILE, timeout=10) as conn:
        conn.execute("INSERT INTO messages (sender_phone, chat_type, target_id, content) VALUES (?, ?, ?, ?)",
                     (user['phone'], type, id, content))
        conn.commit()
    return JSONResponse({"msg": "发送成功"})


@app.post("/api/clear_chat")
async def clear_chat(request: Request, type: str = Form(...), id: str = Form(...)):
    user = get_current_user(request)
    if not user: return JSONResponse({"msg": "登录已过期！"})
    with sqlite3.connect(DB_FILE, timeout=10) as conn:
        cursor = conn.cursor()
        max_id = \
            cursor.execute("SELECT MAX(id) FROM messages WHERE chat_type='group' AND target_id=?", (id,)).fetchone()[
                0] or 0 if type == 'group' else cursor.execute(
                "SELECT MAX(id) FROM messages WHERE chat_type='private' AND ((sender_phone=? AND target_id=?) OR (sender_phone=? AND target_id=?))",
                (user['phone'], id, id, user['phone'])).fetchone()[0] or 0
        exists = cursor.execute("SELECT 1 FROM chat_state WHERE phone=? AND chat_type=? AND target_id=?",
                                (user['phone'], type, id)).fetchone()
        if exists:
            cursor.execute(
                "UPDATE chat_state SET cleared_up_to_msg_id=?, last_read_msg_id=? WHERE phone=? AND chat_type=? AND target_id=?",
                (max_id, max_id, user['phone'], type, id))
        else:
            cursor.execute(
                "INSERT INTO chat_state (phone, chat_type, target_id, cleared_up_to_msg_id, last_read_msg_id) VALUES (?, ?, ?, ?, ?)",
                (user['phone'], type, id, max_id, max_id))
        conn.commit()
    return JSONResponse({"status": "cleared"})


@app.post("/toggle_hide")
async def toggle_hide(request: Request, proj_id: int = Form(...)):
    user = get_current_user(request)
    if not user: return JSONResponse({"msg": "登录已过期！"})
    with sqlite3.connect(DB_FILE, timeout=10) as conn:
        res = conn.cursor().execute("SELECT is_hidden FROM projects WHERE id = ? AND leader_phone = ?",
                                    (proj_id, user['phone'])).fetchone()
        if res: conn.execute("UPDATE projects SET is_hidden = ? WHERE id = ?",
                             (0 if res[0] == 1 else 1, proj_id)); conn.commit()
    return JSONResponse({"msg": "👁️ 可见性已更新"})


@app.post("/delete_project")
async def delete_project(request: Request, proj_id: int = Form(...)):
    user = get_current_user(request)
    if not user: return JSONResponse({"msg": "登录已过期！"})
    with sqlite3.connect(DB_FILE, timeout=10) as conn: conn.execute(
        "UPDATE projects SET is_deleted=1 WHERE id=? AND leader_phone=?", (proj_id, user['phone'])); conn.commit()
    return JSONResponse({"msg": "🗑️ 项目已永久删除"})


@app.post("/hide_record")
async def hide_record(request: Request, app_id: int = Form(...), role: str = Form(...)):
    user = get_current_user(request)
    if not user: return JSONResponse({"msg": "登录已过期！"})
    with sqlite3.connect(DB_FILE, timeout=10) as conn:
        if role == 'applicant':
            conn.execute("UPDATE applications SET applicant_visible=0 WHERE id=? AND applicant_phone=?",
                         (app_id, user['phone']))
        else:
            conn.execute("UPDATE applications SET leader_visible=0 WHERE id=?", (app_id,))
        conn.commit()
    return JSONResponse({"msg": "🗑️ 记录已移除"})


@app.post("/api/kick_member")
async def kick_member(request: Request, proj_id: int = Form(...), target_phone: str = Form(...)):
    user = get_current_user(request)
    if not user: return JSONResponse({"msg": "登录已过期！"})
    with sqlite3.connect(DB_FILE, timeout=10) as conn:
        cursor = conn.cursor()
        t_name = cursor.execute("SELECT name FROM users WHERE phone = ?", (target_phone,)).fetchone()[0]
        cursor.execute(
            "UPDATE applications SET status='已移出', applicant_read=0 WHERE proj_id=? AND applicant_phone=?",
            (proj_id, target_phone))
        cursor.execute(
            "INSERT INTO messages (sender_phone, chat_type, target_id, content) VALUES ('system', 'group', ?, ?)",
            (proj_id, f"管理操作：队长已将 【{t_name}】 移出队伍。"))
        conn.commit()
    return JSONResponse({"msg": "成功移出该成员"})


@app.post("/create")
async def create_project(request: Request, title: str = Form(...), description: str = Form(...), tags: str = Form(...),
                         base_members: int = Form(...), required_members: int = Form(...)):
    user = get_current_user(request)
    if not user: return JSONResponse({"msg": "登录已过期，请刷新！"})
    with sqlite3.connect(DB_FILE, timeout=10) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO projects (title, leader_phone, description, tags, base_members, required_members) VALUES (?, ?, ?, ?, ?, ?)",
            (title, user['phone'], description, tags, base_members, required_members))
        cursor.execute(
            "INSERT INTO messages (sender_phone, chat_type, target_id, content) VALUES ('system', 'group', ?, ?)",
            (cursor.lastrowid, "【系统】项目队伍已创建成功！"))
        conn.commit()
    return JSONResponse({"msg": "🎉 发布成功！"})


@app.post("/apply")
async def apply_project(request: Request, proj_id: int = Form(...)):
    user = get_current_user(request)
    if not user: return JSONResponse({"msg": "登录已过期，请刷新！"})
    with sqlite3.connect(DB_FILE, timeout=10) as conn:
        cursor = conn.cursor()
        exist = cursor.execute("SELECT id, status FROM applications WHERE proj_id=? AND applicant_phone=?",
                               (proj_id, user['phone'])).fetchone()
        if exist:
            if exist[1] in ('已移出', '已拒绝', '已取消'):
                cursor.execute(
                    "UPDATE applications SET status='待审核', applicant_visible=1, leader_visible=1, leader_read=0 WHERE id=?",
                    (exist[0],));
                conn.commit()
                return JSONResponse({"msg": "✅ 已重新发起申请！"})
            else:
                return JSONResponse({"msg": "⚠️ 您已申请过此项目"})
        else:
            cursor.execute(
                "INSERT INTO applications (proj_id, applicant_phone, leader_read, applicant_read) VALUES (?, ?, 0, 1)",
                (proj_id, user['phone']));
            conn.commit()
            return JSONResponse({"msg": "✅ 申请已发出！"})


@app.post("/cancel_apply")
async def cancel_apply(request: Request, app_id: int = Form(...)):
    user = get_current_user(request)
    if not user: return JSONResponse({"msg": "登录已过期，请刷新！"})
    with sqlite3.connect(DB_FILE, timeout=10) as conn: conn.execute(
        "UPDATE applications SET status='已取消', applicant_visible=0 WHERE id=? AND applicant_phone=? AND status='待审核'",
        (app_id, user['phone'])); conn.commit()
    return JSONResponse({"msg": "🗑️ 申请已撤销"})


@app.post("/audit")
async def audit_application(request: Request, app_id: int = Form(...), proj_id: int = Form(...),
                            applicant_name: str = Form(...), action: str = Form(...)):
    user = get_current_user(request)
    if not user: return JSONResponse({"msg": "登录已过期，请刷新！"})
    new_status = "已同意" if action == "accept" else "已拒绝"
    with sqlite3.connect(DB_FILE, timeout=10) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("UPDATE applications SET status=?, applicant_read=0 WHERE id=?", (new_status, app_id))
        if action == "accept":
            cursor.execute(
                "INSERT INTO messages (sender_phone, chat_type, target_id, content) VALUES ('system', 'group', ?, ?)",
                (proj_id, f"🎉 欢迎新成员 【{applicant_name}】 加入队伍！"))
            proj = cursor.execute("SELECT base_members, required_members FROM projects WHERE id = ?",
                                  (proj_id,)).fetchone()
            app_cnt = cursor.execute("SELECT COUNT(*) FROM applications WHERE proj_id=? AND status='已同意'",
                                     (proj_id,)).fetchone()[0]
            if proj['base_members'] + app_cnt >= proj['required_members']: cursor.execute(
                "UPDATE projects SET status='已截止', is_hidden=1 WHERE id=?", (proj_id,))
        conn.commit()
    return JSONResponse({"msg": f"审批成功！结果为：{new_status}"})


@app.post("/toggle_status")
async def toggle_status(request: Request, proj_id: int = Form(...)):
    user = get_current_user(request)
    if not user: return JSONResponse({"msg": "登录已过期，请刷新！"})
    with sqlite3.connect(DB_FILE, timeout=10) as conn:
        ns = "招募中" if conn.cursor().execute("SELECT status FROM projects WHERE id=?", (proj_id,)).fetchone()[
                             0] != "招募中" else "已截止"
        conn.execute("UPDATE projects SET status=? WHERE id=?", (ns, proj_id));
        conn.commit()
    return JSONResponse({"msg": "🔄 状态已切换"})


if __name__ == "__main__":
    print("===================================================")
    print("🚀 校园组队系统启动成功！")
    print("🌐 访问地址: http://127.0.0.1:8002")
    print("===================================================")
    uvicorn.run(app, host="0.0.0.0", port=8002, log_level="warning")
