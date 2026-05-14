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
