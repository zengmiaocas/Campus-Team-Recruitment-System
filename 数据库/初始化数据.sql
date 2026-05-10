-- 插入测试体验账号（密码默认为 123456 的 SHA-256 哈希值）
-- SHA-256("123456") = 8d969eef6ecad3c29a3a629280e686cf0c3f5d5a86aff3ca12020c923adc6c92
INSERT INTO users (phone, student_id, name, password, college, major, skills, is_first_login) 
VALUES 
('13800000001', '202300000001', '张三(测试队长)', '8d969eef6ecad3c29a3a629280e686cf0c3f5d5a86aff3ca12020c923adc6c92', '计算机学院', '软件工程', 'Python,Vue,后端', 0),
('13800000002', '202300000002', '李四(测试队员)', '8d969eef6ecad3c29a3a629280e686cf0c3f5d5a86aff3ca12020c923adc6c92', '设计学院', '视觉传达', 'UI设计,Figma,画图', 0);

-- 插入一个初始的招募项目（由张三发布）
INSERT INTO projects (title, leader_phone, description, tags, base_members, required_members, status)
VALUES 
('【创新创业大赛】寻一位靠谱的UI设计师', '13800000001', '项目已经有后端和前端，目前打算做一个校园二手交易平台参加省赛，缺一位能够设计原型图和UI界面的同学，欢迎带作品来聊！', 'UI设计,Figma', 2, 3, '招募中');

-- 插入一条对应的系统群聊创建消息
INSERT INTO messages (sender_phone, chat_type, target_id, content) 
VALUES 
('system', 'group', '1', '【系统】项目队伍已创建成功！');
