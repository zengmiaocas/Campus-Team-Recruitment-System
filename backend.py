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
    with sqlite3.connect(DB_FILE, timeout=10) as conn:
        cursor = conn.cursor()
        # [DDL] 创建用户表
        cursor.execute('''CREATE TABLE IF NOT EXISTS users (phone TEXT PRIMARY KEY, name TEXT NOT NULL, password TEXT NOT NULL, session_id TEXT, college TEXT DEFAULT '', major TEXT DEFAULT '', class_name TEXT DEFAULT '', qq TEXT DEFAULT '', wechat TEXT DEFAULT '', bio TEXT DEFAULT '', is_first_login INTEGER DEFAULT 1, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
        try: cursor.execute("ALTER TABLE users ADD COLUMN skills TEXT DEFAULT ''")
        except sqlite3.OperationalError: pass
        try: cursor.execute("ALTER TABLE users ADD COLUMN honors TEXT DEFAULT ''")
        except sqlite3.OperationalError: pass
        try: cursor.execute("ALTER TABLE users ADD COLUMN student_id TEXT DEFAULT ''")
        except sqlite3.OperationalError: pass

        cursor.execute("CREATE TABLE IF NOT EXISTS user_sessions (session_id TEXT PRIMARY KEY, phone TEXT UNIQUE)")
        
        # [DDL] 创建项目表
        cursor.execute('''CREATE TABLE IF NOT EXISTS projects (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, leader_phone TEXT NOT NULL, description TEXT, tags TEXT, base_members INTEGER NOT NULL DEFAULT 1, required_members INTEGER NOT NULL DEFAULT 3, status TEXT DEFAULT '招募中', is_deleted INTEGER DEFAULT 0, is_hidden INTEGER DEFAULT 0, FOREIGN KEY (leader_phone) REFERENCES users (phone))''')
        
        # [DDL] 创建申请表
        cursor.execute('''CREATE TABLE IF NOT EXISTS applications (id INTEGER PRIMARY KEY AUTOINCREMENT, proj_id INTEGER, applicant_phone TEXT NOT NULL, status TEXT DEFAULT '待审核', applicant_visible INTEGER DEFAULT 1, leader_visible INTEGER DEFAULT 1, FOREIGN KEY (proj_id) REFERENCES projects (id), FOREIGN KEY (applicant_phone) REFERENCES users (phone))''')
        try: cursor.execute("ALTER TABLE applications ADD COLUMN leader_read INTEGER DEFAULT 0")
        except sqlite3.OperationalError: pass
        try: cursor.execute("ALTER TABLE applications ADD COLUMN applicant_read INTEGER DEFAULT 0")
        except sqlite3.OperationalError: pass
        cursor.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_proj_user ON applications(proj_id, applicant_phone)')

        # [DDL] 创建消息与聊天状态表
        cursor.execute('''CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY AUTOINCREMENT, sender_phone TEXT NOT NULL, chat_type TEXT NOT NULL, target_id TEXT NOT NULL, content TEXT NOT NULL, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS chat_state (phone TEXT, chat_type TEXT, target_id TEXT, last_read_msg_id INTEGER DEFAULT 0, cleared_up_to_msg_id INTEGER DEFAULT 0, PRIMARY KEY (phone, chat_type, target_id))''')

        # [DML] 初始化测试数据
        cursor.execute("SELECT COUNT(*) FROM users")
        if cursor.fetchone()[0] == 0:
            cursor.executemany('''INSERT INTO users (phone, student_id, name, password, college, major, skills, is_first_login) VALUES (?, ?, ?, ?, ?, ?, ?, ?)''', [
                ('13800000001', '202300000001', '张三(测试队长)', '8d969eef6ecad3c29a3a629280e686cf0c3f5d5a86aff3ca12020c923adc6c92', '计算机学院', '软件工程', 'Python,Vue,后端', 0),
                ('13800000002', '202300000002', '李四(测试队员)', '8d969eef6ecad3c29a3a629280e686cf0c3f5d5a86aff3ca12020c923adc6c92', '设计学院', '视觉传达', 'UI设计,Figma,画图', 0)
            ])
            cursor.execute('''INSERT INTO projects (title, leader_phone, description, tags, base_members, required_members, status) VALUES (?, ?, ?, ?, ?, ?, ?)''', ('【创新创业大赛】寻一位靠谱的UI设计师', '13800000001', '项目已经有后端和前端，目前打算做一个校园二手交易平台参加省赛，缺一位能够设计原型图和UI界面的同学，欢迎带作品来聊！', 'UI设计,Figma', 2, 3, '招募中'))
            new_proj_id = cursor.lastrowid
            cursor.execute('''INSERT INTO messages (sender_phone, chat_type, target_id, content) VALUES (?, ?, ?, ?)''', ('system', 'group', str(new_proj_id), '【系统】项目队伍已创建成功！'))
        
        conn.commit()


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield

app = FastAPI(title="校园组队系统 - 优化防抖稳定版", lifespan=lifespan)


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
        return {"status": "valid"} if conn.cursor().execute("SELECT phone FROM user_sessions WHERE session_id = ?", (session_id,)).fetchone() else {"status": "logged_out"}

@app.get("/api/user/{phone}")
async def get_user_profile(phone: str, request: Request):
    if not get_current_user(request): return Response(status_code=401)
    with sqlite3.connect(DB_FILE, timeout=10) as conn:
        conn.row_factory = sqlite3.Row
        user = conn.cursor().execute("SELECT phone, name, college, major, class_name, qq, wechat, bio, skills, honors, student_id FROM users WHERE phone = ?", (phone,)).fetchone()
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

def get_dashboard_panels(user_data, search_q="", search_tag=""):
    # 此函数为后端组装前端大厅数据的逻辑
    my_phone = user_data['phone']
    my_skills_set = set([s.strip() for s in (user_data['skills'] or '').split(',') if s.strip()])

    with sqlite3.connect(DB_FILE, timeout=10) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        all_projects = cursor.execute('''SELECT p.*, u.name as leader_name, (SELECT status FROM applications WHERE proj_id = p.id AND applicant_phone = ?) as my_status, (SELECT COUNT(*) FROM applications WHERE proj_id = p.id AND status = '已同意') as approved_count FROM projects p JOIN users u ON p.leader_phone = u.phone WHERE p.is_deleted = 0 AND (p.is_hidden = 0 OR p.leader_phone = ?) ORDER BY p.id DESC''', (my_phone, my_phone)).fetchall()
        my_apps = cursor.execute('''SELECT a.id as app_id, p.id as proj_id, p.title, a.status FROM applications a JOIN projects p ON a.proj_id = p.id WHERE a.applicant_phone = ? AND p.is_deleted = 0 AND a.applicant_visible = 1 ORDER BY a.id DESC''', (my_phone,)).fetchall()
        audits = cursor.execute('''SELECT a.id as app_id, p.id as proj_id, p.title, u.phone as applicant_phone, u.name as applicant_name, u.honors as applicant_honors, a.status FROM applications a JOIN projects p ON a.proj_id = p.id JOIN users u ON a.applicant_phone = u.phone WHERE p.leader_phone = ? AND p.is_deleted = 0 AND a.leader_visible = 1 ORDER BY a.id DESC''', (my_phone,)).fetchall()
        audit_count = cursor.execute('''SELECT COUNT(*) FROM applications a JOIN projects p ON a.proj_id = p.id WHERE p.leader_phone = ? AND p.is_deleted = 0 AND a.status = '待审核' AND a.leader_read = 0''', (my_phone,)).fetchone()[0]
        apply_count = cursor.execute('''SELECT COUNT(*) FROM applications WHERE applicant_phone = ? AND status IN ('已同意', '已拒绝', '已移出') AND applicant_visible = 1 AND applicant_read = 0''', (my_phone,)).fetchone()[0]
        members_data = cursor.execute('''SELECT a.proj_id, u.phone, u.name FROM applications a JOIN users u ON a.applicant_phone = u.phone WHERE a.status = '已同意' ''').fetchall()
        
        members_by_proj = {}
        for m in members_data: members_by_proj.setdefault(m['proj_id'], []).append(m)

    # 动态组装前端渲染所需的 HTML 结构（为了精简，此处省略具体的 HTML 标签拼接）
    projects_html, recommend_html, my_projects_html = "... [根据上面的 DB 数据生成对应的 HTML 面板结构] ...", "", ""
    audits_html, my_apps_html = "... [同理] ...", "... [同理] ..."

    return {
        "projects": projects_html,
        "my_projects": my_projects_html,
        "recommend": recommend_html,
        "my_apps": my_apps_html,
        "audits": audits_html,
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
            conn.execute("UPDATE applications SET leader_read=1 WHERE id IN (SELECT a.id FROM applications a JOIN projects p ON a.proj_id=p.id WHERE p.leader_phone=? AND a.status='待审核')", (user['phone'],))
        elif type == 'apply':
            conn.execute("UPDATE applications SET applicant_read=1 WHERE applicant_phone=? AND status IN ('已同意', '已拒绝', '已移出')", (user['phone'],))
        conn.commit()
    return JSONResponse({"status": "ok"})

def alert_and_redirect(msg: str, url: str = "/"):
    return HTMLResponse(f"<script>alert('{msg}'); window.location.href='{url}';</script>")


# --- 4. 账号操作路由 ---
@app.get("/login", response_class=HTMLResponse)
async def login_page():
    return "... [返回登录注册页面的前端 HTML 代码] ..."

@app.post("/do_register")
async def do_register(student_id: str = Form(...), name: str = Form(...), phone: str = Form(...), password: str = Form(...), confirm_password: str = Form(...)):
    if not student_id.isdigit() or len(student_id) != 12: return alert_and_redirect("学号必须严格为12位纯数字！", "/login")
    if password != confirm_password: return alert_and_redirect("两次输入的密码不一致！", "/login")
    new_sess = generate_session_id()
    with sqlite3.connect(DB_FILE, timeout=10) as conn:
        try:
            conn.execute("INSERT INTO users (phone, name, password, student_id, is_first_login) VALUES (?, ?, ?, ?, 1)", (phone, name, hash_password(password), student_id))
            conn.execute("DELETE FROM user_sessions WHERE phone = ?", (phone,))
            conn.execute("INSERT INTO user_sessions (session_id, phone) VALUES (?, ?)", (new_sess, phone))
            conn.commit()
        except sqlite3.IntegrityError:
            return alert_and_redirect("手机号或学号已被注册过！", "/login")
    res = RedirectResponse(url="/", status_code=303)
    res.set_cookie("session_token", new_sess, max_age=604800)
    return res

@app.post("/do_login")
async def do_login(username: str = Form(...), password: str = Form(...)):
    new_sess = generate_session_id()
    with sqlite3.connect(DB_FILE, timeout=10) as conn:
        user = conn.cursor().execute("SELECT phone FROM users WHERE (phone=? OR student_id=?) AND password=?", (username, username, hash_password(password))).fetchone()
        if user:
            phone = user[0]
            conn.execute("DELETE FROM user_sessions WHERE phone = ?", (phone,))
            conn.execute("INSERT INTO user_sessions (session_id, phone) VALUES (?, ?)", (new_sess, phone))
            conn.commit()
            res = RedirectResponse(url="/", status_code=303)
            res.set_cookie("session_token", new_sess, max_age=604800)
            return res
        return alert_and_redirect("❌ 账号或密码错误！", "/login")

@app.post("/change_password")
async def change_password(request: Request, student_id: str = Form(...), phone: str = Form(...), old_password: str = Form(...), new_password: str = Form(...), confirm_new_password: str = Form(...)):
    user = get_current_user(request)
    if not user: return JSONResponse({"msg": "登录已过期，请刷新页面！"})
    if new_password != confirm_new_password: return JSONResponse({"msg": "两次输入的新密码不一致！"})
    if user['phone'] != phone or user['student_id'] != student_id: return JSONResponse({"msg": "填写的学号或手机号与当前登录账号不匹配，无法修改！"})

    with sqlite3.connect(DB_FILE, timeout=10) as conn:
        curr = conn.cursor().execute("SELECT phone FROM users WHERE phone=? AND password=?", (phone, hash_password(old_password))).fetchone()
        if not curr: return JSONResponse({"msg": "❌ 您输入的旧密码错误！"})
        conn.execute("UPDATE users SET password=? WHERE phone=?", (hash_password(new_password), phone))
        conn.commit()
    return JSONResponse({"msg": "✅ 密码修改成功！下次请使用新密码登录。"})

@app.get("/logout")
async def logout(request: Request):
    session_id = request.cookies.get("session_token")
    if session_id:
        with sqlite3.connect(DB_FILE, timeout=10) as conn:
            conn.execute("DELETE FROM user_sessions WHERE session_id = ?", (session_id,))
            conn.commit()
    res = alert_and_redirect("已安全退出本设备！", "/login")
    res.delete_cookie("session_token")
    return res


# --- 5. 业务操作路由 ---
@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    user = get_current_user(request)
    if not user: return RedirectResponse(url="/login", status_code=303)
    
    with sqlite3.connect(DB_FILE, timeout=10) as conn:
        if user['is_first_login']: 
            conn.execute("UPDATE users SET is_first_login=0 WHERE phone=?", (user['phone'],))
            conn.commit()
            
    panels = get_dashboard_panels(dict(user))
    return "... [返回主大厅面板的前端 HTML 代码，包含 panels 数据] ..."

@app.post("/update_profile")
async def update_profile(request: Request, name: str = Form(...), college: str = Form(""), major: str = Form(""), class_name: str = Form(""), qq: str = Form(""), wechat: str = Form(""), bio: str = Form(""), skills: str = Form(""), honors: str = Form("")):
    user = get_current_user(request)
    if not user: return JSONResponse({"msg": "登录已过期，请刷新页面重新登录！"})
    with sqlite3.connect(DB_FILE, timeout=10) as conn:
        conn.execute('UPDATE users SET name=?, college=?, major=?, class_name=?, qq=?, wechat=?, bio=?, skills=?, honors=? WHERE phone=?', (name, college, major, class_name, qq, wechat, bio, skills, honors, user['phone']))
        conn.commit()
    return JSONResponse({"msg": "✅ 资料更新成功！"})

@app.post("/create")
async def create_project(request: Request, title: str = Form(...), description: str = Form(...), tags: str = Form(...), base_members: int = Form(...), required_members: int = Form(...)):
    user = get_current_user(request)
    if not user: return JSONResponse({"msg": "登录已过期，请刷新！"})
    with sqlite3.connect(DB_FILE, timeout=10) as conn:
        cursor = conn.cursor()
        cursor.execute("INSERT INTO projects (title, leader_phone, description, tags, base_members, required_members) VALUES (?, ?, ?, ?, ?, ?)", (title, user['phone'], description, tags, base_members, required_members))
        cursor.execute("INSERT INTO messages (sender_phone, chat_type, target_id, content) VALUES ('system', 'group', ?, ?)", (cursor.lastrowid, "【系统】项目队伍已创建成功！"))
        conn.commit()
    return JSONResponse({"msg": "🎉 发布成功！"})

@app.post("/apply")
async def apply_project(request: Request, proj_id: int = Form(...)):
    user = get_current_user(request)
    if not user: return JSONResponse({"msg": "登录已过期，请刷新！"})
    with sqlite3.connect(DB_FILE, timeout=10) as conn:
        cursor = conn.cursor()
        exist = cursor.execute("SELECT id, status FROM applications WHERE proj_id=? AND applicant_phone=?", (proj_id, user['phone'])).fetchone()
        if exist:
            if exist[1] in ('已移出', '已拒绝', '已取消'):
                cursor.execute("UPDATE applications SET status='待审核', applicant_visible=1, leader_visible=1, leader_read=0 WHERE id=?", (exist[0],))
                conn.commit()
                return JSONResponse({"msg": "✅ 已重新发起申请！"})
            else:
                return JSONResponse({"msg": "⚠️ 您已申请过此项目"})
        else:
            cursor.execute("INSERT INTO applications (proj_id, applicant_phone, leader_read, applicant_read) VALUES (?, ?, 0, 1)", (proj_id, user['phone']))
            conn.commit()
            return JSONResponse({"msg": "✅ 申请已发出！"})

@app.post("/cancel_apply")
async def cancel_apply(request: Request, app_id: int = Form(...)):
    user = get_current_user(request)
    if not user: return JSONResponse({"msg": "登录已过期，请刷新！"})
    with sqlite3.connect(DB_FILE, timeout=10) as conn: 
        conn.execute("UPDATE applications SET status='已取消', applicant_visible=0 WHERE id=? AND applicant_phone=? AND status='待审核'", (app_id, user['phone']))
        conn.commit()
    return JSONResponse({"msg": "🗑️ 申请已撤销"})

@app.post("/audit")
async def audit_application(request: Request, app_id: int = Form(...), proj_id: int = Form(...), applicant_name: str = Form(...), action: str = Form(...)):
    user = get_current_user(request)
    if not user: return JSONResponse({"msg": "登录已过期，请刷新！"})
    new_status = "已同意" if action == "accept" else "已拒绝"
    with sqlite3.connect(DB_FILE, timeout=10) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("UPDATE applications SET status=?, applicant_read=0 WHERE id=?", (new_status, app_id))
        if action == "accept":
            cursor.execute("INSERT INTO messages (sender_phone, chat_type, target_id, content) VALUES ('system', 'group', ?, ?)", (proj_id, f"🎉 欢迎新成员 【{applicant_name}】 加入队伍！"))
            proj = cursor.execute("SELECT base_members, required_members FROM projects WHERE id = ?", (proj_id,)).fetchone()
            app_cnt = cursor.execute("SELECT COUNT(*) FROM applications WHERE proj_id=? AND status='已同意'", (proj_id,)).fetchone()[0]
            if proj['base_members'] + app_cnt >= proj['required_members']: 
                cursor.execute("UPDATE projects SET status='已截止', is_hidden=1 WHERE id=?", (proj_id,))
        conn.commit()
    return JSONResponse({"msg": f"审批成功！结果为：{new_status}"})

@app.post("/toggle_status")
async def toggle_status(request: Request, proj_id: int = Form(...)):
    user = get_current_user(request)
    if not user: return JSONResponse({"msg": "登录已过期，请刷新！"})
    with sqlite3.connect(DB_FILE, timeout=10) as conn:
        ns = "招募中" if conn.cursor().execute("SELECT status FROM projects WHERE id=?", (proj_id,)).fetchone()[0] != "招募中" else "已截止"
        conn.execute("UPDATE projects SET status=? WHERE id=?", (ns, proj_id))
        conn.commit()
    return JSONResponse({"msg": "🔄 状态已切换"})

@app.post("/toggle_hide")
async def toggle_hide(request: Request, proj_id: int = Form(...)):
    user = get_current_user(request)
    if not user: return JSONResponse({"msg": "登录已过期！"})
    with sqlite3.connect(DB_FILE, timeout=10) as conn:
        res = conn.cursor().execute("SELECT is_hidden FROM projects WHERE id = ? AND leader_phone = ?", (proj_id, user['phone'])).fetchone()
        if res: 
            conn.execute("UPDATE projects SET is_hidden = ? WHERE id = ?", (0 if res[0] == 1 else 1, proj_id))
            conn.commit()
    return JSONResponse({"msg": "👁️ 可见性已更新"})

@app.post("/delete_project")
async def delete_project(request: Request, proj_id: int = Form(...)):
    user = get_current_user(request)
    if not user: return JSONResponse({"msg": "登录已过期！"})
    with sqlite3.connect(DB_FILE, timeout=10) as conn: 
        conn.execute("UPDATE projects SET is_deleted=1 WHERE id=? AND leader_phone=?", (proj_id, user['phone']))
        conn.commit()
    return JSONResponse({"msg": "🗑️ 项目已永久删除"})

@app.post("/hide_record")
async def hide_record(request: Request, app_id: int = Form(...), role: str = Form(...)):
    user = get_current_user(request)
    if not user: return JSONResponse({"msg": "登录已过期！"})
    with sqlite3.connect(DB_FILE, timeout=10) as conn:
        if role == 'applicant':
            conn.execute("UPDATE applications SET applicant_visible=0 WHERE id=? AND applicant_phone=?", (app_id, user['phone']))
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
        cursor.execute("UPDATE applications SET status='已移出', applicant_read=0 WHERE proj_id=? AND applicant_phone=?", (proj_id, target_phone))
        cursor.execute("INSERT INTO messages (sender_phone, chat_type, target_id, content) VALUES ('system', 'group', ?, ?)", (proj_id, f"管理操作：队长已将 【{t_name}】 移出队伍。"))
        conn.commit()
    return JSONResponse({"msg": "成功移出该成员"})


# --- 6. 聊天业务逻辑 ---
@app.get("/api/chat_list")
async def get_chat_list(request: Request):
    user = get_current_user(request)
    if not user: return JSONResponse([])
    my_phone = user['phone']
    with sqlite3.connect(DB_FILE, timeout=10) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute('''SELECT id as target_id, title as name, 'group' as type FROM projects WHERE leader_phone = ? AND is_deleted = 0 UNION SELECT p.id, p.title, 'group' FROM projects p JOIN applications a ON p.id = a.proj_id WHERE a.applicant_phone = ? AND a.status = '已同意' AND p.is_deleted = 0''', (my_phone, my_phone))
        groups = [dict(c) for c in cursor.fetchall()]
        cursor.execute('''SELECT DISTINCT u.phone as target_id, u.name as name, 'private' as type FROM messages m JOIN users u ON (u.phone = m.target_id AND m.sender_phone = ?) OR (u.phone = m.sender_phone AND m.target_id = ?) WHERE m.chat_type = 'private' AND u.phone != ?''', (my_phone, my_phone, my_phone))
        privates = [dict(c) for c in cursor.fetchall()]
        all_chats = groups + privates
        for c in all_chats:
            state = cursor.execute("SELECT last_read_msg_id, cleared_up_to_msg_id FROM chat_state WHERE phone=? AND chat_type=? AND target_id=?", (my_phone, c['type'], c['target_id'])).fetchone()
            last_read = state['last_read_msg_id'] if state else 0
            cleared = state['cleared_up_to_msg_id'] if state else 0
            if c['type'] == 'group':
                c['unread'] = cursor.execute("SELECT COUNT(*) FROM messages WHERE chat_type='group' AND target_id=? AND sender_phone!=? AND id>? AND id>?", (c['target_id'], my_phone, last_read, cleared)).fetchone()[0]
            else:
                c['unread'] = cursor.execute("SELECT COUNT(*) FROM messages WHERE chat_type='private' AND sender_phone=? AND target_id=? AND id>? AND id>?", (c['target_id'], my_phone, last_read, cleared)).fetchone()[0]
        return JSONResponse(all_chats)

@app.get("/chat", response_class=HTMLResponse)
async def chat_page(request: Request, type: str = "none", id: str = ""):
    user = get_current_user(request)
    if not user: return RedirectResponse("/login", status_code=303)
    # [后端获取聊天群成员逻辑...]
    return "... [返回消息大厅的前端 HTML 代码] ..."

@app.get("/api/messages")
async def get_messages(request: Request, type: str, id: str):
    user = get_current_user(request)
    if not user: return JSONResponse([])
    with sqlite3.connect(DB_FILE, timeout=10) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        state = cursor.execute("SELECT cleared_up_to_msg_id FROM chat_state WHERE phone=? AND chat_type=? AND target_id=?", (user['phone'], type, id)).fetchone()
        cleared_id = state['cleared_up_to_msg_id'] if state else 0
        
        if type == 'group':
            msgs = cursor.execute("SELECT m.*, u.name as sender_name FROM messages m LEFT JOIN users u ON m.sender_phone = u.phone WHERE chat_type = 'group' AND target_id = ? AND m.id > ? ORDER BY created_at ASC", (id, cleared_id)).fetchall()
        else:
            msgs = cursor.execute("SELECT m.*, u.name as sender_name FROM messages m LEFT JOIN users u ON m.sender_phone = u.phone WHERE chat_type = 'private' AND ((sender_phone = ? AND target_id = ?) OR (sender_phone = ? AND target_id = ?)) AND m.id > ? ORDER BY created_at ASC", (user['phone'], id, id, user['phone'], cleared_id)).fetchall()
            
        if msgs:
            max_id = msgs[-1]['id']
            exists = cursor.execute("SELECT 1 FROM chat_state WHERE phone=? AND chat_type=? AND target_id=?", (user['phone'], type, id)).fetchone()
            if exists:
                cursor.execute("UPDATE chat_state SET last_read_msg_id=? WHERE phone=? AND chat_type=? AND target_id=?", (max_id, user['phone'], type, id))
            else:
                cursor.execute("INSERT INTO chat_state (phone, chat_type, target_id, last_read_msg_id) VALUES (?, ?, ?, ?)", (user['phone'], type, id, max_id))
            conn.commit()

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
        conn.execute("INSERT INTO messages (sender_phone, chat_type, target_id, content) VALUES (?, ?, ?, ?)", (user['phone'], type, id, content))
        conn.commit()
    return JSONResponse({"msg": "发送成功"})

@app.post("/api/clear_chat")
async def clear_chat(request: Request, type: str = Form(...), id: str = Form(...)):
    user = get_current_user(request)
    if not user: return JSONResponse({"msg": "登录已过期！"})
    with sqlite3.connect(DB_FILE, timeout=10) as conn:
        cursor = conn.cursor()
        max_id = cursor.execute("SELECT MAX(id) FROM messages WHERE chat_type='group' AND target_id=?", (id,)).fetchone()[0] or 0 if type == 'group' else cursor.execute("SELECT MAX(id) FROM messages WHERE chat_type='private' AND ((sender_phone=? AND target_id=?) OR (sender_phone=? AND target_id=?))", (user['phone'], id, id, user['phone'])).fetchone()[0] or 0
        exists = cursor.execute("SELECT 1 FROM chat_state WHERE phone=? AND chat_type=? AND target_id=?", (user['phone'], type, id)).fetchone()
        if exists:
            cursor.execute("UPDATE chat_state SET cleared_up_to_msg_id=?, last_read_msg_id=? WHERE phone=? AND chat_type=? AND target_id=?", (max_id, max_id, user['phone'], type, id))
        else:
            cursor.execute("INSERT INTO chat_state (phone, chat_type, target_id, cleared_up_to_msg_id, last_read_msg_id) VALUES (?, ?, ?, ?, ?)", (user['phone'], type, id, max_id, max_id))
        conn.commit()
    return JSONResponse({"status": "cleared"})


if __name__ == "__main__":
    print("===================================================")
    print("🚀 校园组队系统启动成功！")
    print("🌐 访问地址: http://127.0.0.1:8002")
    print("===================================================")
    uvicorn.run(app, host="0.0.0.0", port=8002, log_level="warning")
