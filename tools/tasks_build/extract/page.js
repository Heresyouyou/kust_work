
(function () {
  'use strict';

  /* ================= 常量与状态 ================= */
  var APP_NAME = '大学 NPC 任务轴';
  var LS_NODES = 'npc_task_axis_nodes';
  var LS_CHECKINS = 'npc_task_axis_checkins';
  var LS_TAB = 'npc_task_axis_tab';
  var LS_SEEDED = 'npc_task_axis_seeded';
  var LS_BADGES = 'npc_task_axis_badges';
  var LS_FOCUS = 'npc_task_axis_focus';
  var LS_PET = 'npc_task_axis_pet';
  var LS_VIEW = 'npc_task_axis_view';

  var db = null;
  try { db = window.__SMART_PAGE__ && window.__SMART_PAGE__.database; } catch (e) { db = null; }

  var state = {
    cloud: false,
    nodes: [],
    checkins: [],
    tab: '通用证书',
    typeOptions: []
  };

  /* ================= 日期工具 ================= */
  function pad(n) { return (n < 10 ? '0' : '') + n; }
  function todayStr() { var d = new Date(); return d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate()); }
  function d10(v) { if (!v) { return ''; } if (typeof v === 'string') { return v.slice(0, 10); } return ''; }
  function toDate(s) { if (!s) { return null; } return new Date(s + 'T12:00:00'); }
  function addDays(s, n) { var d = toDate(s); if (!d) { return ''; } d.setDate(d.getDate() + n); return d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate()); }
  function addMonths(s, n) { var d = toDate(s); if (!d) { return ''; } d.setMonth(d.getMonth() + n); return d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate()); }
  function daysBetween(a, b) { var da = toDate(a), db2 = toDate(b); if (!da || !db2) { return null; } return Math.round((db2 - da) / 86400000); }
  function fmtCN(s) { if (!s) { return '—'; } var p = s.split('-'); return parseInt(p[0], 10) + '年' + parseInt(p[1], 10) + '月' + parseInt(p[2], 10) + '日'; }
  function monthOf(s) { return s ? s.slice(0, 7) : ''; }
  function monthLabel(ym) { return ym ? parseInt(ym.slice(5, 7), 10) + '月' : ''; }

  /* ================= 证书 / 赛事知识库（24 项） ================= */
  var KB = [
    { name: '大学英语四级考试（CET-4）', short: '四级', alias: 'cet4 cet-4 英语4级', type: '通用证书', org: '教育部教育考试院', site: 'https://cet-bm.neea.edu.cn', fee: 30, months: [6, 12], day: 14,
      off: { reg: -90, regEnd: -80, tick: -7, tickEnd: -1, score: 60 },
      plan: [{ back: 3, subject: '词汇', focus: '高频词汇 List 1-20 · 每日 100 词打卡' }, { back: 2, subject: '听力', focus: '新闻听力 + 长对话 · 每日精听 1 套' }, { back: 1, subject: '阅读 + 翻译', focus: '仔细阅读题型技巧 · 段落翻译真题' }, { back: 0, subject: '写作 + 模考', focus: '写作模板 3 套 · 全真模考 2 次' }] },
    { name: '普通话水平测试', short: '普通话', alias: 'psc putonghua', type: '通用证书', org: '国家语言文字工作委员会', site: 'https://www.cltt.org', fee: 50, months: [5, 11], day: 15,
      off: { reg: -40, regEnd: -25, tick: -5, tickEnd: -1, score: 30 },
      plan: [{ back: 2, subject: '朗读作品', focus: '60 篇作品朗读 · 轻声与儿化音专项' }, { back: 1, subject: '命题说话', focus: '30 个话题提纲 · 3 分钟计时训练' }] },
    { name: '全国计算机等级考试一级', short: '计一', alias: 'ncre1 计算机一级', type: '通用证书', org: '教育部教育考试院', site: 'https://ncre-bm.neea.cn', fee: 30, months: [3, 9], day: 26,
      off: { reg: -75, regEnd: -55, tick: -10, tickEnd: -3, score: 45 },
      plan: [{ back: 2, subject: '选择题', focus: '计算机基础 + 网络基本概念 · 选择题题库' }, { back: 1, subject: '操作题', focus: 'Windows + Office 操作题真题' }] },
    { name: '全国计算机等级考试二级', short: '计算机二级', alias: 'ncre 计算机二级 二级 ms office', type: '通用证书', org: '教育部教育考试院', site: 'https://ncre-bm.neea.cn', fee: 80, months: [3, 9], day: 26,
      off: { reg: -75, regEnd: -55, tick: -10, tickEnd: -3, score: 45 },
      plan: [{ back: 2, subject: '选择题', focus: '公共基础知识 500 题 · 每日 50 题' }, { back: 1, subject: '操作题', focus: 'Word / Excel / PPT 真题套卷 · 每日 1 套' }] },
    { name: '全国计算机等级考试三级', short: '计三', alias: 'ncre3 计算机三级', type: '通用证书', org: '教育部教育考试院', site: 'https://ncre-bm.neea.cn', fee: 80, months: [3, 9], day: 26,
      off: { reg: -75, regEnd: -55, tick: -10, tickEnd: -3, score: 45 },
      plan: [{ back: 2, subject: '笔试科目', focus: '所选科目考点精讲 · 知识框架图' }, { back: 1, subject: '真题', focus: '近 5 年真题 + 错题复盘' }] },
    { name: '中小学教师资格考试（笔试）', short: '教资', alias: 'ntce 教师资格证 教师资格', type: '通用证书', org: '教育部教育考试院', site: 'https://ntce.neea.edu.cn', fee: 70, months: [3, 10], day: 28,
      off: { reg: -110, regEnd: -100, tick: -10, tickEnd: -2, score: 40 },
      plan: [{ back: 2, subject: '综合素质', focus: '职业理念 + 法律法规 · 选择题高频考点' }, { back: 1, subject: '教育知识与能力', focus: '德育原则 · 学习理论 · 简答辨析高频' }, { back: 0, subject: '真题冲刺', focus: '近 5 年真题套卷 · 材料分析题模板' }] },
    { name: '中小学教师资格考试（面试）', short: '教资面试', alias: 'ntce 教师资格面试', type: '通用证书', org: '教育部教育考试院', site: 'https://ntce.neea.edu.cn', fee: 260, months: [1, 5], day: 12,
      off: { reg: -30, regEnd: -20, tick: -5, tickEnd: -1, score: 30 },
      plan: [{ back: 1, subject: '结构化问答', focus: '职业认知 · 应急应变题型模板' }, { back: 0, subject: '试讲演练', focus: '10 分钟试讲 · 每日 1 次全真模拟' }] },
    { name: '全国大学生广告艺术大赛', short: '大广赛', alias: 'guanggao sun-aoa 广告艺术', type: '专业赛事', org: '大广赛组委会', site: 'https://www.sun-aoa.org', fee: 0, months: [6], day: 20,
      off: { reg: -105, regEnd: -40, tick: -30, tickEnd: -20, score: 75 },
      plan: [{ back: 3, subject: '选题 + 策略单', focus: '研读命题策略单 · 确定创意方向' }, { back: 2, subject: '创意执行', focus: '平面 / 视频脚本产出初稿' }, { back: 1, subject: '打磨提交', focus: '成品精修 · 规范提交材料' }] },
    { name: '全国大学生数学建模竞赛', short: '数模', alias: 'cumcm 数学建模', type: '专业赛事', org: '中国工业与应用数学学会', site: 'https://www.mcm.edu.cn', fee: 300, months: [9], day: 8,
      off: { reg: -55, regEnd: -25, tick: -3, tickEnd: -1, score: 40 },
      plan: [{ back: 2, subject: '算法储备', focus: '回归 / 规划 / 图论模型代码模板' }, { back: 1, subject: '论文模板', focus: '论文框架搭建 · 历年优秀论文研读' }, { back: 0, subject: '模拟赛', focus: '3 天全真模拟 · 三人分工演练' }] },
    { name: '全国大学生电子设计竞赛', short: '电赛', alias: 'nuedc 电子设计', type: '专业赛事', org: '教育部电子信息类专业教指委', site: 'https://nuedc.xjtu.edu.cn', fee: 300, months: [8], day: 5,
      off: { reg: -70, regEnd: -40, tick: -7, tickEnd: -3, score: 45 },
      plan: [{ back: 2, subject: '模块训练', focus: '最小系统 · 电源 · 信号调理模块' }, { back: 1, subject: '历年真题', focus: '往年赛题复现 · 整机组装' }, { back: 0, subject: '联调冲刺', focus: '4 天 3 夜全真模拟 · 报告撰写' }] },
    { name: '蓝桥杯全国软件和信息技术专业人才大赛', short: '蓝桥杯', alias: 'lanqiao 蓝桥', type: '专业赛事', org: '工业和信息化部人才交流中心', site: 'https://dasai.lanqiao.cn', fee: 300, months: [4], day: 15,
      off: { reg: -150, regEnd: -100, tick: -7, tickEnd: -2, score: 35 },
      plan: [{ back: 3, subject: '基础算法', focus: '枚举 · 模拟 · 排序 · 蓝桥真题' }, { back: 2, subject: '专题突破', focus: '动态规划 · 贪心 · 图论专题刷题' }, { back: 1, subject: '真题模考', focus: '历年省赛真题 · 计时赛训练' }] },
    { name: '“挑战杯”全国大学生课外学术科技作品竞赛', short: '挑战杯', alias: 'tiaozhanbei tiaozhan 挑战', type: '专业赛事', org: '共青团中央 / 中国科协', site: 'https://www.tiaozhanbei.net', fee: 0, months: [6], day: 1,
      off: { reg: -150, regEnd: -90, tick: -20, tickEnd: -15, score: 60 },
      plan: [{ back: 4, subject: '选题立项', focus: '文献调研 · 确定研究方向与导师' }, { back: 2, subject: '实验推进', focus: '数据采集与分析 · 中期检查' }, { back: 0, subject: '材料打磨', focus: '论文 + 展示 PPT 精修' }] },
    { name: '中国国际“互联网+”大学生创新创业大赛', short: '互联网+', alias: 'ietc 互联网 互联网plus', type: '专业赛事', org: '教育部', site: 'https://cy.ncss.cn', fee: 0, months: [10], day: 15,
      off: { reg: -120, regEnd: -60, tick: -15, tickEnd: -10, score: 40 },
      plan: [{ back: 3, subject: '项目计划', focus: '商业计划书框架 · 市场调研' }, { back: 1, subject: '路演打磨', focus: 'PPT 路演 · 答辩问答预演' }] },
    { name: '“外研社·国才杯”全国英语写作大赛', short: '国才杯写作', alias: 'fltrp waiyanshe 写作大赛', type: '专业赛事', org: '外语教学与研究出版社', site: 'https://uchallenge.unipus.cn', fee: 50, months: [11], day: 5,
      off: { reg: -60, regEnd: -40, tick: -7, tickEnd: -3, score: 40 },
      plan: [{ back: 2, subject: '读后续写', focus: '续写技巧 + 范文精读' }, { back: 1, subject: '议论写作', focus: '议论文结构 · 高级表达积累' }] },
    { name: '全国大学生英语竞赛（NECCS）', short: '大英赛', alias: 'neccs 英语竞赛', type: '专业赛事', org: '高等学校大学外语教学研究会', site: 'http://www.chinanes.net', fee: 30, months: [4], day: 20,
      off: { reg: -90, regEnd: -60, tick: -7, tickEnd: -3, score: 40 },
      plan: [{ back: 2, subject: '题型专项', focus: '智力题 + 改错等独有题型训练' }, { back: 1, subject: '全真模考', focus: '真题计时 · 做题速度训练' }] },
    { name: '全国翻译专业资格考试（CATTI 三级笔译）', short: 'CATTI三笔', alias: 'catti 翻译', type: '通用证书', org: '人力资源和社会保障部', site: 'http://www.catticenter.com', fee: 90, months: [6, 11], day: 15,
      off: { reg: -50, regEnd: -40, tick: -7, tickEnd: -2, score: 45 },
      plan: [{ back: 2, subject: '英汉互译', focus: '政府工作报告双语精读' }, { back: 1, subject: '实务冲刺', focus: '真题精练 · 数字与专有名词译法' }] },
    { name: '剑桥商务英语中级（BEC Vantage）', short: 'BEC', alias: 'bec 商务英语', type: '通用证书', org: '剑桥大学考评局', site: 'https://cet-bm.neea.edu.cn', fee: 530, months: [5, 11], day: 25,
      off: { reg: -70, regEnd: -60, tick: -10, tickEnd: -3, score: 50 },
      plan: [{ back: 2, subject: '商务词汇', focus: '商务高频词 + 固定搭配' }, { back: 1, subject: '真题 + 口语', focus: '口语搭档模拟 · 商务写作模板' }] },
    { name: '国际人才英语考试（国才考试初级）', short: '国才初级', alias: 'etic 国才', type: '通用证书', org: '北京外国语大学', site: 'https://etic.claonline.cn', fee: 380, months: [5, 11], day: 16,
      off: { reg: -75, regEnd: -60, tick: -7, tickEnd: -3, score: 45 },
      plan: [{ back: 2, subject: '口头沟通', focus: '发言 / 讨论话题库练习' }, { back: 1, subject: '书面沟通', focus: '写作模板 + 观点素材积累' }] },
    { name: '普通话水平测试（二甲）', short: '二甲', alias: 'erjia psc2', type: '通用证书', org: '国家语言文字工作委员会', site: 'https://www.cltt.org', fee: 50, months: [5, 11], day: 15,
      off: { reg: -40, regEnd: -25, tick: -5, tickEnd: -1, score: 30 },
      plan: [{ back: 2, subject: '字词朗读', focus: '易错字词 300 组 · 儿化与轻声' }, { back: 1, subject: '命题说话', focus: '高频话题提纲 · 流畅度训练' }] },
    { name: '国际中文教师资格证书', short: '汉教', alias: 'ctcsol 国际中文 汉教', type: '通用证书', org: '中外语言交流合作中心', site: 'https://www.chineseteacher.org.cn', fee: 400, months: [9], day: 20,
      off: { reg: -120, regEnd: -90, tick: -10, tickEnd: -5, score: 50 },
      plan: [{ back: 2, subject: '基础知识', focus: '现代汉语 + 中国文化考点' }, { back: 1, subject: '教学能力', focus: '教案设计 + 试讲演练' }] },
    { name: '全国大学生计算机设计大赛', short: '计算机设计赛', alias: 'jsjsj 计算机设计', type: '专业赛事', org: '中国大学生计算机设计大赛组委会', site: 'https://jsjds.blcu.edu.cn', fee: 0, months: [4], day: 20,
      off: { reg: -120, regEnd: -60, tick: -10, tickEnd: -3, score: 50 },
      plan: [{ back: 3, subject: '选题策划', focus: '确定作品方向 · 功能设计' }, { back: 1, subject: '开发实现', focus: '原型 → 成品开发与测试' }, { back: 0, subject: '材料提交', focus: '演示视频 + 说明文档' }] },
    { name: '全国大学生英语演讲大赛', short: '英语演讲', alias: 'yanjiang 演讲', type: '专业赛事', org: '外语教学与研究出版社', site: 'https://uchallenge.unipus.cn', fee: 0, months: [11], day: 20,
      off: { reg: -90, regEnd: -60, tick: -10, tickEnd: -5, score: 40 },
      plan: [{ back: 2, subject: '演讲稿', focus: '主题演讲稿打磨' }, { back: 1, subject: '即兴演讲', focus: '即席问答训练' }] },
    { name: '会计专业技术初级资格考试', short: '初会', alias: 'chuji 初级会计 会计', type: '通用证书', org: '财政部', site: 'https://kzp.mof.gov.cn', fee: 100, months: [5], day: 18,
      off: { reg: -100, regEnd: -60, tick: -7, tickEnd: -2, score: 40 },
      plan: [{ back: 3, subject: '初级会计实务', focus: '资产 / 负债 / 所有者权益章节' }, { back: 2, subject: '经济法基础', focus: '税法 + 支付结算法律制度' }, { back: 0, subject: '冲刺模考', focus: '近 3 年真题 · 错题复盘' }] },
    { name: '全国大学生工程训练综合能力竞赛', short: '工程训练赛', alias: 'gongxun 工程训练', type: '专业赛事', org: '教育部工程训练教指委', site: 'http://www.gcxl.cn', fee: 100, months: [5], day: 10,
      off: { reg: -90, regEnd: -60, tick: -10, tickEnd: -5, score: 40 },
      plan: [{ back: 2, subject: '方案设计', focus: '命题分析 · 方案论证' }, { back: 1, subject: '制作装配', focus: '样机加工 · 装配调试' }] }
  ];

  function findKB(name) {
    var q = String(name || '').toLowerCase();
    for (var i = 0; i < KB.length; i++) {
      if (KB[i].name.indexOf(name) !== -1 || (KB[i].alias && KB[i].alias.indexOf(q) !== -1)) { return KB[i]; }
    }
    return null;
  }
  function searchKB(q) {
    var s = String(q || '').trim().toLowerCase();
    if (!s) { return []; }
    var hits = [];
    for (var i = 0; i < KB.length; i++) {
      var kb = KB[i];
      if (kb.name.toLowerCase().indexOf(s) !== -1 || (kb.alias && kb.alias.indexOf(s) !== -1) || kb.short.indexOf(s) !== -1) { hits.push(kb); }
    }
    return hits.slice(0, 6);
  }
  function nearestExam(kb) {
    var now = new Date();
    var y = now.getFullYear();
    var best = null;
    for (var t = 0; t < 3; t++) {
      for (var i = 0; i < kb.months.length; i++) {
        var m = kb.months[i];
        var d = new Date(y + t, m - 1, kb.day);
        if (d >= now) { var s = d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate()); if (!best || s < best) { best = s; } }
      }
    }
    return best;
  }
  function planFromKB(kb, examDate) {
    if (!examDate) { return []; }
    var plan = [];
    for (var i = 0; i < kb.plan.length; i++) {
      var p = kb.plan[i];
      plan.push({ month: addMonths(examDate, -p.back).slice(0, 7), subject: p.subject, focus: p.focus });
    }
    return plan;
  }
  function genericPlan(examDate) {
    if (!examDate) { return []; }
    return [
      { month: addMonths(examDate, -3).slice(0, 7), subject: '基础阶段', focus: '教材通读 · 知识框架搭建' },
      { month: addMonths(examDate, -1).slice(0, 7), subject: '强化阶段', focus: '真题训练 · 薄弱点专项突破' },
      { month: monthOf(examDate), subject: '冲刺阶段', focus: '模拟套卷 · 错题复盘' }
    ];
  }
  function ensurePlan(node) {
    if (node && node.plan && node.plan.length) { return node.plan; }
    var exam = node ? node.exam : '';
    if (!exam) { return []; }
    var kb = findKB(node.name);
    var plan = kb ? planFromKB(kb, exam) : genericPlan(exam);
    if (node) { node.plan = plan; }
    return plan;
  }

  function normalizeNode(rec) {
    var site = rec['报名网站'];
    var t = rec['类型'];
    var type = (t === '专业赛事' || t === 'p8n3l5yt6dcxq2b7') ? '专业赛事' : '通用证书';
    var plan = [];
    try { plan = rec['备考计划'] ? JSON.parse(rec['备考计划']) : []; } catch (e) { plan = []; }
    return {
      _id: rec['_id'] || ('l_' + Math.random().toString(36).slice(2, 12)),
      name: rec['名称'] || '',
      short: rec['简称'] || (rec['名称'] || '').slice(0, 4),
      type: type,
      site: (site && site.link) ? site.link : '',
      siteName: (site && site.text) ? site.text : '',
      place: rec['报名地点'] || '',
      fee: (typeof rec['报名费用'] === 'number') ? rec['报名费用'] : (parseFloat(rec['报名费用']) || 0),
      regStart: d10(rec['报名开始']), regEnd: d10(rec['报名截止']),
      tickStart: d10(rec['准考证开始']), tickEnd: d10(rec['准考证截止']),
      exam: d10(rec['考试日期']), score: d10(rec['查分日期']),
      registered: !!rec['已报名'],
      plan: plan
    };
  }
  function normalizeCheckin(rec) {
    return {
      _id: rec['_id'] || ('l_' + Math.random().toString(36).slice(2, 12)),
      node: rec['节点'] || '',
      date: d10(rec['日期']),
      subject: rec['科目'] || '',
      stage: rec['阶段'] || '',
      note: rec['备注'] || ''
    };
  }
  function typeIdFor(type) {
    for (var i = 0; i < state.typeOptions.length; i++) {
      if (state.typeOptions[i].text === type) { return state.typeOptions[i].id; }
    }
    return type;
  }
  function buildNodeProps(n) {
    var props = {};
    props['名称'] = { text: n.name };
    props['简称'] = { text: n.short };
    props['类型'] = { select: typeIdFor(n.type) };
    props['报名网站'] = { url: { text: n.siteName || '', link: n.site || '' } };
    props['报名地点'] = { text: n.place };
    props['报名费用'] = { number: n.fee || 0 };
    props['报名开始'] = { date: n.regStart || '' };
    props['报名截止'] = { date: n.regEnd || '' };
    props['准考证开始'] = { date: n.tickStart || '' };
    props['准考证截止'] = { date: n.tickEnd || '' };
    props['考试日期'] = { date: n.exam || '' };
    props['查分日期'] = { date: n.score || '' };
    props['已报名'] = { checkbox: !!n.registered };
    props['备考计划'] = { text: JSON.stringify(ensurePlan(n)) };
    return props;
  }

  function mirrorLocal() {
    try {
      localStorage.setItem(LS_NODES, JSON.stringify(state.nodes));
      localStorage.setItem(LS_CHECKINS, JSON.stringify(state.checkins));
    } catch (e) { }
  }
  function loadLocal() {
    try {
      state.nodes = JSON.parse(localStorage.getItem(LS_NODES) || '[]') || [];
      state.checkins = JSON.parse(localStorage.getItem(LS_CHECKINS) || '[]') || [];
    } catch (e) { state.nodes = []; state.checkins = []; }
    state.cloud = false;
  }

  function persistNode(n) {
    if (state.cloud && db) {
      var props = buildNodeProps(n);
      if (n._id && n._id.indexOf('l_') !== 0) {
        return db.updateRecord({ databaseId: 'lnmuAnycaz4YipNuhWlHmC', recordId: n._id, properties: props })
          .then(function (r) { if (r && r.id) { n._id = r.id; } mirrorLocal(); })
          .catch(function (err) { console.error('[database] 更新节点失败:', err); mirrorLocal(); });
      }
      return db.addRecord({ databaseId: 'lnmuAnycaz4YipNuhWlHmC', properties: props })
        .then(function (r) { if (r && (r.id || r.recordId)) { n._id = r.id || r.recordId; } mirrorLocal(); })
        .catch(function (err) { console.error('[database] 新增节点失败:', err); mirrorLocal(); });
    }
    mirrorLocal();
    return Promise.resolve();
  }
  function removeNodeRemote(n) {
    if (state.cloud && db && n._id && n._id.indexOf('l_') !== 0) {
      return db.deleteRecord({ databaseId: 'lnmuAnycaz4YipNuhWlHmC', recordId: n._id })
        .catch(function (err) { console.error('[database] 删除节点失败:', err); });
    }
    return Promise.resolve();
  }
  function persistCheckin(c) {
    var props = {};
    props['节点'] = { text: c.node };
    props['日期'] = { date: c.date };
    props['科目'] = { text: c.subject };
    props['阶段'] = { text: c.stage };
    props['备注'] = { text: c.note };
    if (state.cloud && db) {
      return db.addRecord({ databaseId: 'CtaG4iVTm0yPx8iXaKoTgQ', properties: props })
        .then(function (r) { if (r && (r.id || r.recordId)) { c._id = r.id || r.recordId; } mirrorLocal(); })
        .catch(function (err) { console.error('[database] 打卡写入失败:', err); mirrorLocal(); });
    }
    mirrorLocal();
    return Promise.resolve();
  }
  function removeCheckinRemote(c) {
    if (state.cloud && db && c._id && c._id.indexOf('l_') !== 0) {
      return db.deleteRecord({ databaseId: 'CtaG4iVTm0yPx8iXaKoTgQ', recordId: c._id })
        .catch(function (err) { console.error('[database] 取消打卡失败:', err); });
    }
    return Promise.resolve();
  }

  /* ================= 示例数据 ================= */
  function sampleNodes() {
    return [
      { name: '中小学教师资格考试（笔试）', short: '教资', type: '通用证书', site: 'https://ntce.neea.edu.cn', siteName: '中小学教师资格考试网', place: '本校教务处 · 大学生活动中心 201', fee: 140, regStart: '2026-07-04', regEnd: '2026-07-11', tickStart: '2026-10-19', tickEnd: '2026-10-30', exam: '2026-10-31', score: '2026-12-09', registered: true,
        plan: [
          { month: '2026-08', subject: '综合素质', focus: '职业理念 + 法律法规 · 选择题高频考点' },
          { month: '2026-09', subject: '教育知识与能力', focus: '德育原则 · 学习理论 · 简答辨析高频' },
          { month: '2026-10', subject: '真题冲刺', focus: '近 5 年真题套卷 · 材料分析题模板' }] },
      { name: '全国计算机等级考试二级', short: '计算机二级', type: '通用证书', site: 'https://ncre-bm.neea.cn', siteName: 'NCRE 报名系统', place: '第 3 机房楼 B302', fee: 80, regStart: '2026-06-20', regEnd: '2026-07-05', tickStart: '2026-09-15', tickEnd: '2026-09-25', exam: '2026-09-26', score: '2026-11-10', registered: true,
        plan: [
          { month: '2026-08', subject: '选择题', focus: '公共基础知识 500 题 · 每日 50 题' },
          { month: '2026-09', subject: '操作题', focus: 'Word / Excel / PPT 真题套卷 · 每日 1 套' }] },
      { name: '大学英语四级考试（CET-4）', short: '四级', type: '通用证书', site: 'https://cet-bm.neea.edu.cn', siteName: 'CET 报名系统', place: '全国大学英语四六级考试报名网', fee: 30, regStart: '2026-09-15', regEnd: '2026-09-25', tickStart: '2026-12-08', tickEnd: '2026-12-18', exam: '2026-12-19', score: '2027-02-25', registered: true,
        plan: [
          { month: '2026-09', subject: '词汇', focus: '高频词汇 List 1-20 · 每日 100 词打卡' },
          { month: '2026-10', subject: '听力', focus: '新闻听力 + 长对话 · 每日精听 1 套' },
          { month: '2026-11', subject: '阅读 + 翻译', focus: '仔细阅读题型技巧 · 段落翻译真题' },
          { month: '2026-12', subject: '写作 + 模考', focus: '写作模板 3 套 · 全真模考 2 次' }] },
      { name: '普通话水平测试', short: '普通话', type: '通用证书', site: 'https://www.cltt.org', siteName: '国家普通话水平测试网', place: '文学院语言实验室 304', fee: 50, regStart: '2026-10-10', regEnd: '2026-10-17', tickStart: '2026-11-07', tickEnd: '2026-11-13', exam: '2026-11-14', score: '2026-12-14', registered: false,
        plan: [
          { month: '2026-10', subject: '朗读作品', focus: '60 篇作品朗读 · 轻声与儿化音专项' },
          { month: '2026-11', subject: '命题说话', focus: '30 个话题提纲 · 3 分钟计时训练' }] },
      { name: '全国大学生广告艺术大赛', short: '大广赛', type: '专业赛事', site: 'https://www.sun-aoa.org', siteName: '大广赛官网', place: '线上提交 · 学校创新创业中心备案', fee: 0, regStart: '2026-03-07', regEnd: '2026-05-11', tickStart: '2026-05-21', tickEnd: '2026-05-31', exam: '2026-06-20', score: '2026-09-03', registered: false,
        plan: [
          { month: '2026-03', subject: '选题 + 策略单', focus: '研读命题策略单 · 确定创意方向' },
          { month: '2026-04', subject: '创意执行', focus: '平面 / 视频脚本产出初稿' },
          { month: '2026-05', subject: '打磨提交', focus: '成品精修 · 规范提交材料' }] }
    ];
  }
  function seedSample() {
    var nodes = sampleNodes();
    var chains = [];
    var i;
    for (i = 0; i < nodes.length; i++) {
      var n = { _id: 'l_seed_' + i + '_' + Math.random().toString(36).slice(2, 8) };
      var k;
      for (k in nodes[i]) { if (Object.prototype.hasOwnProperty.call(nodes[i], k)) { n[k] = nodes[i][k]; } }
      state.nodes.push(n);
      chains.push(persistNode(n));
    }
    var t = todayStr();
    var days = [-3, -2, -1];
    for (var j = 0; j < days.length; j++) {
      var c = { _id: 'l_seedc_' + j + '_' + Math.random().toString(36).slice(2, 8), node: '中小学教师资格考试（笔试）', date: addDays(t, days[j]), subject: '综合素质', stage: monthOf(addDays(t, days[j])), note: '' };
      state.checkins.push(c);
      chains.push(persistCheckin(c));
    }
    try { localStorage.setItem(LS_SEEDED, '1'); } catch (e) { }
    return Promise.all(chains).then(function () { mirrorLocal(); });
  }

  /* ================= 视图切换 ================= */
  function switchView(name) {
    var views = document.querySelectorAll('.view');
    for (var i = 0; i < views.length; i++) {
      views[i].className = (views[i].id === 'view-' + name) ? 'view active' : 'view';
    }
    var navs = document.querySelectorAll('.nav-item');
    for (var j = 0; j < navs.length; j++) {
      navs[j].className = (navs[j].getAttribute('data-view') === name) ? 'nav-item active' : 'nav-item';
    }
    if (name === 'npc') { renderNPCView(); }
    if (name === 'stage') { renderPrep(); }
    if (name === 'focus') { renderFocusView(); }
    if (name === 'pet') { renderPetView(); }
    try { localStorage.setItem(LS_VIEW, name); } catch (e) { }
  }

  /* ================= 玩家等级 / 剧情 / 经验 ================= */
  var CHAPTERS = [
    { lv: 1, icon: '🌱', title: '第一章 · 觉醒的新生', desc: '刚踏入大学地图，第一张证书任务正在前方刷新。完成首次报名，点亮时间轴的第一颗星吧。' },
    { lv: 2, icon: '📜', title: '第二章 · 点亮地图', desc: '你已经注册了 1+ 个任务，冒险路线开始成形。坚持打卡，积攒第一波经验。' },
    { lv: 3, icon: '⚔️', title: '第三章 · 副本初探', desc: '开启备考副本，按月推进的复习阶段等你逐一击破。连续打卡习惯养成中。' },
    { lv: 5, icon: '🔥', title: '第四章 · 连击加持', desc: '专注力觉醒：使用番茄钟进入沉浸学习，每个完成番茄都在为你加分。' },
    { lv: 7, icon: '🏰', title: '第五章 · 双子试炼', desc: '多条任务线并行，证书与赛事齐头并进。合理分配精力，稳中求进。' },
    { lv: 9, icon: '👑', title: '第六章 · 毕业勇者', desc: '多证在手，剧情终章。你就是这所大学传说中的人物，NPC 故事由你书写。' }
  ];
  function focusSessions() {
    var arr = [];
    try { arr = JSON.parse(localStorage.getItem(LS_FOCUS) || '[]') || []; } catch (e) { arr = []; }
    return arr;
  }
  function playerExp() {
    var exp = state.checkins.length * 5;
    for (var i = 0; i < state.nodes.length; i++) { if (state.nodes[i].registered) { exp += 10; } }
    exp += focusSessions().length * 15;
    return exp;
  }
  function playerLevel(exp) {
    var lv = 1, need = 40;
    while (exp >= need && lv < 10) { exp -= need; lv++; need = 40 + (lv - 1) * 25; }
    return lv;
  }
  function levelProgress(exp) {
    var lv = playerLevel(exp);
    var cur = exp;
    for (var i = 1; i < lv; i++) { cur -= 40 + (i - 1) * 25; }
    cur = Math.max(0, cur);
    var need = 40 + (lv - 1) * 25;
    return { cur: cur, need: need, pct: Math.min(100, Math.round(cur / need * 100)) };
  }
  function currentChapter(lv) {
    var ch = CHAPTERS[0];
    for (var i = 0; i < CHAPTERS.length; i++) { if (lv >= CHAPTERS[i].lv) { ch = CHAPTERS[i]; } }
    return ch;
  }
  function renderSidebar() {
    var exp = playerExp();
    var lv = playerLevel(exp);
    document.getElementById('sideLv').textContent = 'Lv.' + lv;
    document.getElementById('heroLv').textContent = 'Lv.' + lv + ' ' + currentChapter(lv).title.split('· ')[1] || ('Lv.' + lv);
    var prog = levelProgress(exp);
    document.getElementById('sideExpBar').style.width = prog.pct + '%';
    document.getElementById('sideExpText').textContent = prog.cur + ' / ' + prog.need + ' EXP';
    document.getElementById('sideStreak').textContent = globalStreak() + ' 天';
  }
  function renderStory() {
    var box = document.getElementById('storyBox');
    box.innerHTML = '';
    var exp = playerExp();
    var lv = playerLevel(exp);
    var ch = currentChapter(lv);
    var chap = el('div', 'story-chap', ch.icon + ' ' + ch.title + '　·　Lv.' + lv);
    box.appendChild(chap);
    box.appendChild(el('div', 'story-desc', ch.desc));
    var bar = el('div', 'story-bar');
    var fill = el('i');
    fill.style.width = (lv / 10 * 100) + '%';
    bar.appendChild(fill);
    box.appendChild(bar);
    var chips = el('div', 'story-chips');
    for (var i = 0; i < CHAPTERS.length; i++) {
      var cls = 'story-chip';
      if (lv >= CHAPTERS[i].lv) { cls += ' on'; }
      if (CHAPTERS[i] === ch) { cls += ' now'; }
      chips.appendChild(el('span', cls, CHAPTERS[i].icon + ' ' + CHAPTERS[i].title.replace('第', '') ));
    }
    box.appendChild(chips);
  }

  /* ================= 渲染：统一入口 ================= */
  function refreshAll() {
    renderSidebar();
    renderStory();
    renderTimeline();
    renderCountdown();
    renderPrep();
    renderBadges();
    renderStats();
    renderFocusView();
    renderPetView();
    renderAssistant();
  }

  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) { e.className = cls; }
    if (text !== undefined && text !== null) { e.textContent = text; }
    return e;
  }
  function escapeHtml(s) {
    return String(s === undefined || s === null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  /* ================= 渲染：NPC 时间轴 ================= */
  function renderNPCView() {
    renderSidebar();
    renderStory();
    renderTimeline();
    renderCountdown();
    renderBadges();
    renderStats();
  }
  function renderTimeline() {
    var box = document.getElementById('timeline');
    box.innerHTML = '';
    box.style.minWidth = '';
    var nodes = [];
    var i;
    for (i = 0; i < state.nodes.length; i++) {
      if (state.nodes[i].type === state.tab) { nodes.push(state.nodes[i]); }
    }
    nodes.sort(function (a, b) { return (a.exam || a.regEnd || '') < (b.exam || b.regEnd || '') ? -1 : 1; });
    if (!nodes.length) {
      box.appendChild(el('div', 'tl-empty', '当前时间轴还没有任务节点，点击「＋ 接新任务」添加吧'));
      return;
    }
    var today = todayStr();
    var min = '', max = '';
    for (i = 0; i < nodes.length; i++) {
      var n = nodes[i];
      var dates = [n.regStart, n.regEnd, n.tickStart, n.tickEnd, n.exam, n.score];
      for (var d = 0; d < dates.length; d++) {
        if (!dates[d]) { continue; }
        if (!min || dates[d] < min) { min = dates[d]; }
        if (!max || dates[d] > max) { max = dates[d]; }
      }
    }
    if (!min) { min = today; }
    if (!max) { max = addDays(today, 60); }
    min = addDays(min, -12); max = addDays(max, 12);
    var span = daysBetween(min, max) || 60;
    var width = Math.max(900, span * 5);
    box.style.minWidth = width + 'px';

    box.appendChild(el('div', 'tl-axis'));

    var cursor = monthOf(min) + '-01';
    var guard = 0;
    while (cursor <= max && guard < 60) {
      guard++;
      var posPct = ((daysBetween(min, cursor) || 0) / span) * 100;
      var tick = el('div', 'tl-tick');
      tick.style.left = posPct + '%';
      box.appendChild(tick);
      var lbl = el('div', 'tl-tick-lbl');
      lbl.style.left = posPct + '%';
      lbl.textContent = (monthOf(cursor).slice(5, 7) === '01' ? monthOf(cursor).slice(0, 4) + '年' : '') + parseInt(monthOf(cursor).slice(5, 7), 10) + '月';
      box.appendChild(lbl);
      cursor = addMonths(cursor, 1);
    }

    /* —— 防重叠：动态多 lane 贪心分配 —— */
    var NODE_W = 140, LANE_GAP = 58;
    var anchors = [];
    var lanes = [];
    var laneOf = [];
    for (i = 0; i < nodes.length; i++) {
      var node = nodes[i];
      var anchor = node.exam || node.score || node.regEnd || node.regStart || today;
      var pct = ((daysBetween(min, anchor) || 0) / span) * 100;
      pct = Math.max(3, Math.min(97, pct));
      var px = pct / 100 * width;
      anchors.push({ anchor: anchor, pct: pct, px: px });
      var chosen = -1;
      for (var L = 0; L < lanes.length; L++) {
        if (Math.abs(lanes[L].lastX - px) >= NODE_W) { chosen = L; break; }
      }
      if (chosen < 0) {
        chosen = lanes.length;
        lanes.push({ side: (lanes.length % 2 === 0) ? 'up' : 'down', lastX: -1e9 });
      }
      lanes[chosen].lastX = px;
      laneOf.push(chosen);
    }
    var sideOrd = [];
    var cntUp = 0, cntDown = 0;
    for (i = 0; i < lanes.length; i++) {
      if (lanes[i].side === 'up') { sideOrd.push(cntUp++); } else { sideOrd.push(cntDown++); }
    }
    var upH = cntUp * LANE_GAP + 56;
    var downH = cntDown * LANE_GAP + 70;
    var tlH = Math.max(240, upH + downH);
    box.style.height = tlH + 'px';
    var axisY = upH;

    for (i = 0; i < nodes.length; i++) {
      var node = nodes[i];
      var pos = anchors[i];
      var li = laneOf[i];
      var side = lanes[li].side;
      var ord = sideOrd[li];
      var near = isNear(node, today);
      var wrap = el('div', 'tl-node ' + (side === 'up' ? 'lane-up' : 'lane-down') + (node.registered ? ' reg' : '') + (near ? ' near' : ''));
      wrap.style.left = pos.pct + '%';
      wrap.style.top = axisY + 'px';
      wrap.title = node.name + (near ? '（临近节点）' : '');
      wrap.appendChild(el('div', 'dot'));
      var body = el('div', 'body');
      if (side === 'up') { body.style.bottom = (14 + ord * LANE_GAP) + 'px'; }
      else { body.style.top = (36 + ord * LANE_GAP) + 'px'; }
      body.appendChild(el('span', 'chip', node.short || node.name.slice(0, 4)));
      body.appendChild(el('div', 'when', node.exam ? (monthLabel(monthOf(node.exam)) + '开考') : '待定'));
      wrap.appendChild(body);
      (function (nd) { wrap.onclick = function () { openNodeCard(nd); }; })(node);
      box.appendChild(wrap);
    }
  }
  function isNear(node, today) {
    var ds = [node.regStart, node.regEnd, node.tickStart, node.tickEnd, node.exam, node.score];
    for (var i = 0; i < ds.length; i++) {
      if (!ds[i]) { continue; }
      var diff = daysBetween(today, ds[i]);
      if (diff !== null && diff >= 0 && diff <= 14) { return true; }
    }
    return false;
  }

  /* ================= 渲染：倒计时 ================= */
  function renderCountdown() {
    var grid = document.getElementById('cdGrid');
    grid.innerHTML = '';
    var today = todayStr();
    var items = [];
    for (var i = 0; i < state.nodes.length; i++) {
      var n = state.nodes[i];
      if (!n.registered || !n.exam) { continue; }
      var diff = daysBetween(today, n.exam);
      if (diff === null || diff < 0) { continue; }
      var pinned = false, flag = '';
      if (diff === 0) { pinned = true; flag = '今日考试'; }
      else if (diff === 1) { pinned = true; flag = '明日开考'; }
      else if (n.tickStart && n.tickEnd && today >= n.tickStart && today <= n.tickEnd) { pinned = true; flag = '准考证打印期'; }
      else if (n.score && today === n.score) { pinned = true; flag = '今日出分'; }
      else if (n.regEnd && daysBetween(today, n.regEnd) === 0) { pinned = true; flag = '报名今日截止'; }
      items.push({ n: n, diff: diff, pinned: pinned, flag: flag });
    }
    items.sort(function (a, b) {
      if (a.pinned !== b.pinned) { return a.pinned ? -1 : 1; }
      return a.diff - b.diff;
    });
    if (!items.length) {
      grid.appendChild(el('div', 'empty-tip', '还没有已报名的任务。在时间轴点击节点 →「标记已报名」，即可自动生成倒计时。'));
      return;
    }
    for (var k = 0; k < items.length; k++) {
      var it = items[k];
      var cls = it.diff <= 1 ? 'urgent' : (it.diff <= 7 ? 'warn' : 'normal');
      var card = el('div', 'cd-card ' + cls);
      var head = el('div', 'cd-head');
      head.appendChild(el('div', 'cd-name', it.n.short || it.n.name.slice(0, 6)));
      if (it.flag) { head.appendChild(el('span', 'cd-flag', '⚠ ' + it.flag)); }
      card.appendChild(head);
      var days = el('div', 'cd-days');
      days.appendChild(document.createTextNode(it.diff === 0 ? '今天' : String(it.diff)));
      days.appendChild(el('small', null, it.diff === 0 ? '就是今天，加油！' : ' 天后开考'));
      card.appendChild(days);
      var meta = el('div', 'cd-meta');
      meta.appendChild(el('div', null, '🗓 考试日期：' + fmtCN(it.n.exam)));
      if (it.n.tickStart) { meta.appendChild(el('div', null, '🖨 准考证：' + it.n.tickStart + ' ~ ' + it.n.tickEnd)); }
      if (it.n.score) { meta.appendChild(el('div', null, '📊 查分：' + it.n.score)); }
      card.appendChild(meta);
      (function (nd) { card.onclick = function () { openNodeCard(nd); }; })(it.n);
      grid.appendChild(card);
    }
  }

  /* ================= 渲染：阶段任务打卡 ================= */
  function nodeCheckinMap(node) {
    var map = {};
    for (var i = 0; i < state.checkins.length; i++) {
      if (state.checkins[i].node === node.name && state.checkins[i].date) {
        map[state.checkins[i].date] = state.checkins[i];
      }
    }
    return map;
  }
  function streakOf(node) {
    var map = nodeCheckinMap(node);
    var t = todayStr();
    var streak = 0;
    var cursor = map[t] ? t : addDays(t, -1);
    while (map[cursor]) { streak++; cursor = addDays(cursor, -1); }
    return streak;
  }
  function streakText(n) {
    if (n >= 30) { return '连续 30 天，月度满分达成！' }
    if (n >= 21) { return '连续 21 天，习惯已完全养成' }
    if (n >= 14) { return '连续 14 天，自律进阶玩家' }
    if (n >= 7) { return '连续 7 天，一周任务达成！' }
    if (n >= 3) { return '连续 3 天，节奏已入正轨' }
    if (n >= 1) { return '首战告捷！习惯从今天开始' }
    return '尚未开始打卡，今天就是第一天';
  }
  function currentStage(node) {
    var plan = ensurePlan(node);
    var ym = monthOf(todayStr());
    var cur = null, next = null;
    for (var i = 0; i < plan.length; i++) {
      if (plan[i].month === ym) { cur = plan[i]; }
      if (!next && plan[i].month > ym) { next = plan[i]; }
    }
    return cur || next || (plan.length ? plan[plan.length - 1] : null);
  }

  function renderPrep() {
    var wrap = document.getElementById('prepWrap');
    wrap.innerHTML = '';
    var today = todayStr();
    var active = [];
    for (var i = 0; i < state.nodes.length; i++) {
      var n = state.nodes[i];
      if (n.registered && n.exam && n.exam >= today) { active.push(n); }
    }
    if (!active.length) {
      wrap.appendChild(el('div', 'empty-tip', '暂无进行中的备考任务。标记「已报名」后，这里会生成阶段追踪与打卡计划。'));
      var go = el('button', 'btn btn-primary', '＋ 去接新任务');
      go.onclick = function () { openFormModal(null); };
      var row = el('div');
      row.style.textAlign = 'center';
      row.appendChild(go);
      wrap.appendChild(row);
      return;
    }
    for (var k = 0; k < active.length; k++) {
      wrap.appendChild(buildPrepCard(active[k]));
    }
  }
  function buildPrepCard(node) {
    var map = nodeCheckinMap(node);
    var total = 0;
    for (var key in map) { if (Object.prototype.hasOwnProperty.call(map, key)) { total++; } }
    var streak = streakOf(node);
    var plan = ensurePlan(node);
    var stage = currentStage(node);
    var today = todayStr();

    var card = el('div', 'prep-card');
    var head = el('div', 'prep-head');
    head.appendChild(el('div', 'prep-name', node.name));
    head.appendChild(el('span', 'prep-tag', streak + ' 天连续'));
    if (node.exam) {
      var d = daysBetween(today, node.exam);
      if (d !== null && d >= 0) { head.appendChild(el('span', 'prep-tag', '距考试 ' + d + ' 天')); }
    }
    card.appendChild(head);

    var stats = el('div', 'prep-stats');
    stats.appendChild(statItem(String(streak), '连续天数'));
    stats.appendChild(statItem(String(total), '累计打卡'));
    stats.appendChild(statItem(String(plan.length), '备考阶段'));
    stats.appendChild(statItem(node.exam || '—', '考试日期'));
    card.appendChild(stats);

    var sum = el('div', 'prep-stage-summary');
    sum.appendChild(el('div', null, streakText(streak)));
    var stLine = el('div');
    if (stage) {
      stLine.appendChild(document.createTextNode('当前阶段：'));
      var b = el('b', null, monthLabel(stage.month) + ' · ' + stage.subject);
      stLine.appendChild(b);
      stLine.appendChild(document.createTextNode(' —— ' + stage.focus));
    } else {
      stLine.textContent = '暂无阶段计划';
    }
    sum.appendChild(stLine);
    card.appendChild(sum);

    var acts = el('div', 'prep-actions');
    var viewBtn = el('button', 'btn btn-ghost btn-sm', '查看全部 ' + plan.length + ' 个阶段');
    viewBtn.onclick = function () { openStageModal(node); };
    acts.appendChild(viewBtn);
    var doneToday = !!map[today];
    var checkBtn = el('button', 'btn btn-gold btn-sm', doneToday ? '今日已打卡 ✓' : '⚡ 今日打卡');
    checkBtn.disabled = doneToday;
    checkBtn.onclick = function () { doCheckin(node, today, false); };
    acts.appendChild(checkBtn);
    card.appendChild(acts);
    return card;
  }
  function statItem(v, k) {
    var wrap = el('div', 'prep-stat');
    wrap.appendChild(el('div', 'v', v));
    wrap.appendChild(el('div', 'k', k));
    return wrap;
  }

  /* ================= 渲染：徽章墙 ================= */
  var BADGES = [
    { key: 's3', icon: '🥉', name: '三日之约', test: function (s, t) { return s >= 3; } },
    { key: 's7', icon: '🥈', name: '七日行者', test: function (s, t) { return s >= 7; } },
    { key: 's14', icon: '🥇', name: '双周骑士', test: function (s, t) { return s >= 14; } },
    { key: 's21', icon: '🏅', name: '三周勇者', test: function (s, t) { return s >= 21; } },
    { key: 's30', icon: '👑', name: '月度传说', test: function (s, t) { return s >= 30; } },
    { key: 't10', icon: '⭐', name: '十次修行', test: function (s, t) { return t >= 10; } },
    { key: 't30', icon: '🌟', name: '三十连击', test: function (s, t) { return t >= 30; } },
    { key: 't50', icon: '💎', name: '五十封神', test: function (s, t) { return t >= 50; } }
  ];
  function globalStreak() {
    var max = 0;
    var seen = {};
    for (var i = 0; i < state.nodes.length; i++) {
      var n = state.nodes[i];
      if (seen[n.name]) { continue; }
      seen[n.name] = true;
      var s = streakOf(n);
      if (s > max) { max = s; }
    }
    return max;
  }
  function renderBadges() {
    var box = document.getElementById('badges');
    box.innerHTML = '';
    var streak = globalStreak();
    var total = state.checkins.length;
    var unlocked = {};
    try { unlocked = JSON.parse(localStorage.getItem(LS_BADGES) || '{}') || {}; } catch (e) { unlocked = {}; }
    for (var i = 0; i < BADGES.length; i++) {
      var b = BADGES[i];
      var on = b.test(streak, total);
      var dom = el('div', 'badge' + (on ? ' on' : ''));
      dom.appendChild(el('div', 'b-ico', b.icon));
      dom.appendChild(el('div', 'b-name', b.name));
      box.appendChild(dom);
      if (on && !unlocked[b.key]) {
        unlocked[b.key] = 1;
        toast('🏅 解锁里程碑：' + b.name, true);
      }
    }
    try { localStorage.setItem(LS_BADGES, JSON.stringify(unlocked)); } catch (e) { }
  }

  /* ================= 渲染：统计 ================= */
  function renderStats() {
    document.getElementById('statNodes').textContent = String(state.nodes.length);
    var reg = 0;
    for (var i = 0; i < state.nodes.length; i++) { if (state.nodes[i].registered) { reg++; } }
    document.getElementById('statReg').textContent = String(reg);
    document.getElementById('statStreak').textContent = globalStreak() + ' 天';
    document.getElementById('statCheckins').textContent = String(state.checkins.length);
  }

  /* ================= 桌宠（全局悬浮 + 陪伴模块） ================= */
  var encouragePool = [
    '每一张证书都是你地图上点亮的传送点 ✨',
    '今天也是离上岸更近的一天！',
    '别人在开黑，你在悄悄升级 🎮',
    '稳住，我们能赢！按时打卡哦～',
    '错题本比刷题更值钱，翻翻它 📖',
    '早报名早安心，节点别错过啦 ⏰'
  ];
  var bubbleIdx = 0;
  function assistantNode() {
    var today = todayStr();
    for (var i = 0; i < state.nodes.length; i++) {
      var n = state.nodes[i];
      if (n.registered && n.exam && n.exam >= today) { return n; }
    }
    return null;
  }
  function assistantBubbleText(node) {
    var today = todayStr();
    var map = nodeCheckinMap(node);
    var d = daysBetween(today, node.exam);
    if (d !== null && d >= 0 && d <= 7) { return '考试倒计时 ' + d + ' 天！最后冲刺，稳住心态 💪'; }
    if (node.tickStart && node.tickEnd && today >= node.tickStart && today <= node.tickEnd) { return '准考证打印窗口已开启，记得提前打印 🖨'; }
    if (!map[today]) { return '今天还没打卡哦，任务经验值在等你 ⚡'; }
    return '今日任务已完成，经验值 +5 ✅';
  }
  function renderAssistant() {
    var dom = document.getElementById('assistant');
    var node = assistantNode();
    if (!node) { dom.className = 'assistant'; return; }
    dom.className = 'assistant show';
    document.getElementById('assistantText').textContent = assistantBubbleText(node);
    document.getElementById('btnGoCheck').onclick = function () { openStageModal(node); };
    document.getElementById('btnGoPet').onclick = function () { switchView('pet'); };
  }

  /* ================= 沉浸式学习：番茄钟 ================= */
  var FOCUS_MODES = {
    focus: { icon: '🍅', label: '专注', minutes: 25, tip: '专注时间 · 心无旁骛' },
    short: { icon: '☕', label: '短休', minutes: 5, tip: '短休时间 · 放松眼睛' },
    long: { icon: '🌙', label: '长休', minutes: 15, tip: '长休时间 · 彻底放松' }
  };
  var RING_C = 2 * Math.PI * 120;
  var pomo = { mode: 'focus', total: 25 * 60, left: 25 * 60, running: false, timer: null, tag: '' };
  function saveFocusSessions(arr) { try { localStorage.setItem(LS_FOCUS, JSON.stringify(arr)); } catch (e) { } }
  function focusTodayStats() {
    var arr = focusSessions();
    var t = todayStr();
    var todayCnt = 0, todayMin = 0, totalMin = 0, days = {};
    for (var i = 0; i < arr.length; i++) {
      var s = arr[i];
      days[s.date] = 1;
      totalMin += (s.minutes || 0);
      if (s.date === t) { todayCnt++; todayMin += (s.minutes || 0); }
    }
    var streak = 0;
    var cur = days[t] ? t : addDays(t, -1);
    while (days[cur]) { streak++; cur = addDays(cur, -1); }
    return { cnt: todayCnt, minutes: todayMin, streak: streak, total: arr.length, totalMin: totalMin };
  }
  function renderFocusView() {
    var st = focusTodayStats();
    document.getElementById('fsCnt').textContent = String(st.cnt);
    document.getElementById('fsMin').textContent = String(st.minutes);
    document.getElementById('fsStreak').textContent = String(st.streak);
    document.getElementById('fsTotal').textContent = String(st.total);
    /* 快捷标签：已报名任务名 */
    var chips = document.getElementById('focusChips');
    chips.innerHTML = '';
    var seen = {};
    var list = [];
    for (var i = 0; i < state.nodes.length; i++) {
      var n = state.nodes[i];
      if (n.registered && !seen[n.short]) { seen[n.short] = 1; list.push(n.short); }
    }
    for (var j = 0; j < list.length; j++) {
      (function (name) {
        var c = el('button', 'fchip', name);
        c.onclick = function () { document.getElementById('focusTag').value = name; };
        chips.appendChild(c);
      })(list[j]);
    }
    /* 最近记录 */
    var log = document.getElementById('focusLog');
    log.innerHTML = '';
    var arr = focusSessions().slice(-12).reverse();
    if (!arr.length) { log.appendChild(el('div', 'empty-tip', '还没有专注记录，开始第一个番茄吧 🍅')); }
    for (var k = 0; k < arr.length; k++) {
      var item = el('div', 'fl-item');
      item.appendChild(el('span', 't', (arr[k].tag || '自由学习')));
      item.appendChild(el('span', null, arr[k].date));
      item.appendChild(el('span', 'm', '🍅 ' + arr[k].minutes + '′'));
      log.appendChild(item);
    }
    updateFocusUI();
  }
  function updateFocusUI() {
    var mm = Math.floor(pomo.left / 60);
    var ss = pomo.left % 60;
    document.getElementById('focusTime').textContent = pad(mm) + ':' + pad(ss);
    var mode = FOCUS_MODES[pomo.mode];
    document.getElementById('focusLabel').textContent = mode.icon + ' ' + mode.label + ' · ' + mode.tip;
    var prog = pomo.total ? (pomo.left / pomo.total) : 0;
    var ring = document.getElementById('focusRing');
    ring.setAttribute('stroke-dasharray', String(RING_C));
    ring.setAttribute('stroke-dashoffset', String(RING_C * (1 - prog)));
    ring.setAttribute('class', 'ring-fg ' + pomo.mode);
    document.getElementById('modeFocus').className = pomo.mode === 'focus' ? 'active' : '';
    document.getElementById('modeShort').className = pomo.mode === 'short' ? 'active' : '';
    document.getElementById('modeLong').className = pomo.mode === 'long' ? 'active' : '';
    document.getElementById('btnPomoStart').textContent = pomo.running ? '▶ 进行中' : '▶ 开始';
  }
  function pomoSwitch(mode) {
    if (pomo.running) { window.clearInterval(pomo.timer); pomo.running = false; }
    pomo.mode = mode;
    pomo.total = FOCUS_MODES[mode].minutes * 60;
    pomo.left = pomo.total;
    updateFocusUI();
  }
  function pomoStart() {
    if (pomo.running) { return; }
    if (pomo.left <= 0) { pomo.left = pomo.total; }
    pomo.running = true;
    pomo.timer = window.setInterval(pomoTick, 1000);
    updateFocusUI();
    document.getElementById('focusTag').style.borderColor = '';
  }
  function pomoPause() {
    if (!pomo.running) { return; }
    pomo.running = false;
    window.clearInterval(pomo.timer);
    updateFocusUI();
  }
  function pomoReset() {
    pomo.running = false;
    window.clearInterval(pomo.timer);
    pomo.left = pomo.total;
    updateFocusUI();
  }
  function pomoTick() {
    pomo.left--;
    if (pomo.left <= 0) {
      pomo.left = 0;
      pomo.running = false;
      window.clearInterval(pomo.timer);
      completePomo();
      return;
    }
    updateFocusUI();
  }
  function completePomo() {
    var mode = FOCUS_MODES[pomo.mode];
    if (pomo.mode === 'focus') {
      var tag = document.getElementById('focusTag').value.trim();
      var arr = focusSessions();
      arr.push({ date: todayStr(), minutes: pomo.total / 60, tag: tag || '自由学习', ts: Date.now() });
      saveFocusSessions(arr);
      burstParticles();
      showBanner('🍅 专注完成！');
      toast('番茄 +1，经验 +15，休息一下吧');
      refreshAll();
    } else {
      burstParticles();
      showBanner('休息结束，满血复活！');
      toast('☕ 休息结束，开始下一轮专注吧');
      refreshAll();
    }
  }
  /* 全局暴露（onclick 内联绑定用） */
  window.pomoSwitch = pomoSwitch;

  /* ================= 桌宠状态与互动 ================= */
  var petLines = {
    pat: ['嘿嘿，摸头好舒服～继续加油哦！', '被你摸得有点困了…Zzz 😴', '摸摸！我今天也要陪你学完这轮！'],
    feed: ['🍰 好吃！学习能量 +1！', '哇，是给我的吗？最爱你了！', '吃饱饱，陪你打怪升级更有劲！'],
    cheer: ['别怕，每一个错题都是升级的阶梯 💪', '你不是一个人在战斗，我陪着你！', '今天的你，比昨天更接近目标 ✨'],
    focus: ['去吧！专注 25 分钟，回来我为你庆祝 🍅', '我会在这里等你，冲鸭！']
  };
  var petLineIdx = {};
  function petState() {
    var p = { fed: 0, pat: 0, last: '' };
    try { p = JSON.parse(localStorage.getItem(LS_PET) || '{}') || p; } catch (e) { p = { fed: 0, pat: 0, last: '' }; }
    return p;
  }
  function savePet(p) { try { localStorage.setItem(LS_PET, JSON.stringify(p)); } catch (e) { } }
  function petAwayDays(p) {
    var last = d10(p.last);
    if (!last) { return 0; }
    var diff = daysBetween(last, todayStr());
    return diff === null ? 0 : Math.max(0, diff);
  }
  function petMood(p) { return Math.max(5, Math.min(100, 62 + p.pat * 5 + p.fed * 4 - petAwayDays(p) * 4)); }
  function petFull(p) { return Math.max(0, Math.min(100, 70 + p.fed * 8 - petAwayDays(p) * 12)); }
  function petLine(key) {
    var pool = petLines[key] || petLines.cheer;
    petLineIdx[key] = (petLineIdx[key] || 0) + 1;
    return pool[(petLineIdx[key] - 1) % pool.length];
  }
  function renderPetView() {
    var p = petState();
    var mood = petMood(p);
    var full = petFull(p);
    document.getElementById('petMood').style.width = mood + '%';
    document.getElementById('petMoodTxt').textContent = mood + ' / 100';
    document.getElementById('petFull').style.width = full + '%';
    document.getElementById('petFullTxt').textContent = full + ' / 100';
    document.getElementById('petFav').textContent = '互动记录：摸摸头 ' + p.pat + ' 次 · 喂食 ' + p.fed + ' 次';
  }
  function petReact(key, anim) {
    var big = document.getElementById('petBig');
    big.className = 'pet-big' + (anim ? ' ' + anim : '');
    setTimeout(function () { big.className = 'pet-big'; }, 700);
    document.getElementById('petBubble').textContent = petLine(key);
    var p = petState();
    p.last = todayStr();
    if (key === 'pat') { p.pat++; }
    if (key === 'feed') { p.fed++; }
    savePet(p);
    renderPetView();
  }
  function petWiggle(anim) {
    var big = document.getElementById('petBig');
    big.className = 'pet-big' + (anim ? ' ' + anim : '');
    setTimeout(function () { big.className = 'pet-big'; }, 700);
  }

  /* ================= 打卡动作 ================= */
  function doCheckin(node, date, isMakeup) {
    var map = nodeCheckinMap(node);
    if (map[date]) { toast('该日期已打过卡啦'); return; }
    var plan = ensurePlan(node);
    var stage = null;
    for (var i = 0; i < plan.length; i++) { if (plan[i].month === monthOf(date)) { stage = plan[i]; break; } }
    var rec = {
      _id: 'l_' + Math.random().toString(36).slice(2, 12) + Date.now().toString(36),
      node: node.name, date: date,
      subject: stage ? stage.subject : '自由复习',
      stage: stage ? stage.month : monthOf(date),
      note: ''
    };
    state.checkins.push(rec);
    persistCheckin(rec).then(function () {
      refreshAll();
      burstParticles();
      showBanner(isMakeup ? '补卡成功' : '打卡成功');
      toast(streakText(streakOf(node)));
    });
  }
  function cancelCheckin(node, date) {
    var map = nodeCheckinMap(node);
    var rec = map[date];
    if (!rec) { return; }
    var idx = state.checkins.indexOf(rec);
    if (idx >= 0) { state.checkins.splice(idx, 1); }
    removeCheckinRemote(rec).then(function () { mirrorLocal(); refreshAll(); toast('已取消 ' + date + ' 的打卡'); });
  }

  /* ================= 弹窗通用 ================= */
  function openModal(html) {
    var overlay = document.getElementById('overlay');
    var dialog = document.getElementById('dialog');
    dialog.innerHTML = html;
    overlay.className = 'overlay show';
    var closer = dialog.querySelector('.dlg-close');
    if (closer) { closer.onclick = closeModal; }
  }
  function closeModal() { document.getElementById('overlay').className = 'overlay'; }

  /* ================= 节点信息卡 ================= */
  function openNodeCard(node) {
    var today = todayStr();
    var near = isNear(node, today);
    var html = '';
    html += '<div class="dlg-title">🗺 ' + escapeHtml(node.name);
    if (near) { html += '<span class="near-tag">临近节点</span>'; }
    html += '<button class="dlg-close">✕</button></div>';
    html += '<table class="info-table">';
    html += infoRow('类型', escapeHtml(node.type));
    html += infoRow('报名网站', node.site ? '<a href="' + escapeHtml(node.site) + '" target="_blank" rel="noopener">' + escapeHtml(node.siteName || node.site) + '</a>' : '—');
    html += infoRow('报名起止', escapeHtml(node.regStart ? node.regStart + ' ~ ' + node.regEnd : '—'));
    html += infoRow('准考证打印', escapeHtml(node.tickStart ? node.tickStart + ' ~ ' + node.tickEnd : '—'));
    html += infoRow('考试日期', escapeHtml(node.exam ? fmtCN(node.exam) : '—'));
    html += infoRow('查分日期', escapeHtml(node.score ? fmtCN(node.score) : '—'));
    html += infoRow('报名地点', escapeHtml(node.place || '—'));
    html += infoRow('报名费用', escapeHtml(node.fee ? '¥ ' + node.fee : '—'));
    html += infoRow('报名状态', node.registered ? '✅ 已报名' : '⬜ 未报名');
    html += '</table>';
    html += '<div class="dlg-actions">';
    html += '<button class="btn ' + (node.registered ? 'btn-ghost' : 'btn-primary') + ' btn-sm" id="ncToggleReg">' + (node.registered ? '取消报名' : '标记已报名') + '</button>';
    if (node.registered && node.exam && node.exam >= today) { html += '<button class="btn btn-gold btn-sm" id="ncPrep">📚 去打卡</button>'; }
    html += '<button class="btn btn-ghost btn-sm" id="ncEdit">✏️ 编辑</button>';
    html += '<button class="btn btn-danger btn-sm" id="ncDel">🗑 删除</button>';
    html += '</div>';
    openModal(html);

    document.getElementById('ncToggleReg').onclick = function () {
      node.registered = !node.registered;
      persistNode(node).then(function () {
        refreshAll();
        if (node.registered) { toast('✅ 报名成功，记得打卡！'); }
        openNodeCard(node);
      });
    };
    var prepBtn = document.getElementById('ncPrep');
    if (prepBtn) { prepBtn.onclick = function () { openStageModal(node); }; }
    document.getElementById('ncEdit').onclick = function () { openFormModal(node); };
    document.getElementById('ncDel').onclick = function () {
      if (!window.confirm('确定删除任务「' + node.name + '」？相关打卡记录将一并删除。')) { return; }
      var idx = state.nodes.indexOf(node);
      if (idx >= 0) { state.nodes.splice(idx, 1); }
      var cis = [];
      for (var i = state.checkins.length - 1; i >= 0; i--) {
        if (state.checkins[i].node === node.name) { cis.push(state.checkins[i]); state.checkins.splice(i, 1); }
      }
      var chain = removeNodeRemote(node);
      for (var j = 0; j < cis.length; j++) { chain = chain.then(removeCheckinRemote(cis[j])); }
      chain.then(function () { mirrorLocal(); refreshAll(); closeModal(); toast('任务已删除'); });
    };
  }
  function infoRow(k, v) { return '<tr><td>' + k + '</td><td>' + v + '</td></tr>'; }

  /* ================= 添加 / 编辑表单（含知识库搜索自动填充） ================= */
  var formPlan = null;
  function openFormModal(node) {
    formPlan = null;
    var isNew = !node;
    var n = node || { name: '', short: '', type: state.tab, site: '', siteName: '', place: '', fee: '', regStart: '', regEnd: '', tickStart: '', tickEnd: '', exam: '', score: '', registered: false, plan: [] };
    if (isNew) {
      try { var draft = JSON.parse(localStorage.getItem('npc_task_axis_form_draft') || '{}'); if (draft && draft.name) { n = Object.assign(n, draft); } } catch (e) { }
    }
    var html = '';
    html += '<div class="dlg-title">' + (isNew ? '✨ 接新任务' : '✏️ 编辑任务') + '<span style="font-size:12px;color:#a09db5;font-weight:400">输入名称自动匹配知识库并填充</span><button class="dlg-close">✕</button></div>';
    html += '<div class="f-grid">';
    html += '<div class="f-item full"><label>名称（输入触发智能匹配）</label><input id="fName" autocomplete="off" placeholder="如：四级 / 教资 / 大广赛" value="' + escapeHtml(n.name) + '"><div class="kb-drop" id="kbDrop"></div></div>';
    html += '<div class="f-item"><label>简称（时间轴节点显示）</label><input id="fShort" placeholder="如：四级" value="' + escapeHtml(n.short) + '"></div>';
    html += '<div class="f-item"><label>类型</label><select id="fType"></select></div>';
    html += '<div class="f-item full"><label>报名网站</label><input id="fSite" placeholder="https://..." value="' + escapeHtml(n.site) + '"></div>';
    html += '<div class="f-item"><label>报名地点</label><input id="fPlace" placeholder="如：本校教务处 / 线上" value="' + escapeHtml(n.place) + '"></div>';
    html += '<div class="f-item"><label>报名费用（元）</label><input id="fFee" type="number" min="0" placeholder="0" value="' + escapeHtml(n.fee === 0 ? '' : String(n.fee)) + '"></div>';
    html += '<div class="f-item"><label>报名开始</label><input id="fRegStart" type="date" value="' + escapeHtml(n.regStart) + '"></div>';
    html += '<div class="f-item"><label>报名截止</label><input id="fRegEnd" type="date" value="' + escapeHtml(n.regEnd) + '"></div>';
    html += '<div class="f-item"><label>准考证开始</label><input id="fTickStart" type="date" value="' + escapeHtml(n.tickStart) + '"></div>';
    html += '<div class="f-item"><label>准考证截止</label><input id="fTickEnd" type="date" value="' + escapeHtml(n.tickEnd) + '"></div>';
    html += '<div class="f-item"><label>考试日期</label><input id="fExam" type="date" value="' + escapeHtml(n.exam) + '"></div>';
    html += '<div class="f-item"><label>查分日期</label><input id="fScore" type="date" value="' + escapeHtml(n.score) + '"></div>';
    html += '<div class="f-item f-check"><input id="fReg" type="checkbox"' + (n.registered ? ' checked' : '') + '><label for="fReg" style="font-size:13px">已报名（勾选后自动生成倒计时与打卡计划）</label></div>';
    html += '</div>';
    html += '<div class="dlg-actions"><button class="btn btn-primary" id="fSave">💾 保存任务</button></div>';
    openModal(html);

    var typeSel = document.getElementById('fType');
    typeSel.innerHTML = '';
    var opts = state.typeOptions.length ? state.typeOptions : [{ text: '通用证书', id: '通用证书' }, { text: '专业赛事', id: '专业赛事' }];
    for (var i = 0; i < opts.length; i++) {
      var o = document.createElement('option');
      o.value = opts[i].id;
      o.textContent = opts[i].text;
      if (opts[i].text === n.type) { o.selected = true; }
      typeSel.appendChild(o);
    }
    if (!state.typeOptions.length) { typeSel.value = n.type; }

    var nameInput = document.getElementById('fName');
    var drop = document.getElementById('kbDrop');
    nameInput.oninput = function () {
      var q = nameInput.value;
      var hits = searchKB(q);
      drop.innerHTML = '';
      if (!q.trim() || !hits.length) { drop.className = 'kb-drop'; return; }
      for (var i2 = 0; i2 < hits.length; i2++) {
        (function (kb) {
          var item = el('div', 'kb-item');
          var nm = el('div', 'k-name');
          nm.appendChild(document.createTextNode(kb.name));
          item.appendChild(nm);
          item.appendChild(el('div', 'k-meta', kb.org + ' · ' + kb.type + ' · 常规场次：' + kb.months.join(' / ') + '月' + (kb.fee ? ' · ¥' + kb.fee : ' · 免费')));
          item.onmousedown = function (e) {
            e.preventDefault();
            applyKB(kb);
          };
          drop.appendChild(item);
        })(hits[i2]);
      }
      drop.className = 'kb-drop show';
    };
    nameInput.onblur = function () { setTimeout(function () { drop.className = 'kb-drop'; }, 180); };
    document.getElementById('dialog').addEventListener('input', function () {
      try {
        localStorage.setItem('npc_task_axis_form_draft', JSON.stringify({
          name: document.getElementById('fName').value,
          short: document.getElementById('fShort').value,
          site: document.getElementById('fSite').value,
          place: document.getElementById('fPlace').value,
          fee: document.getElementById('fFee').value,
          regStart: document.getElementById('fRegStart').value,
          regEnd: document.getElementById('fRegEnd').value,
          tickStart: document.getElementById('fTickStart').value,
          tickEnd: document.getElementById('fTickEnd').value,
          exam: document.getElementById('fExam').value,
          score: document.getElementById('fScore').value
        }));
      } catch (e) { }
    });

    function applyKB(kb) {
      var exam = nearestExam(kb);
      document.getElementById('fName').value = kb.name;
      document.getElementById('fShort').value = kb.short;
      document.getElementById('fSite').value = kb.site;
      document.getElementById('fPlace').value = '';
      document.getElementById('fFee').value = String(kb.fee || '');
      if (exam) {
        document.getElementById('fExam').value = exam;
        document.getElementById('fRegStart').value = addDays(exam, kb.off.reg);
        document.getElementById('fRegEnd').value = addDays(exam, kb.off.regEnd);
        document.getElementById('fTickStart').value = addDays(exam, kb.off.tick);
        document.getElementById('fTickEnd').value = addDays(exam, kb.off.tickEnd);
        document.getElementById('fScore').value = addDays(exam, kb.off.score);
      }
      var typeSel2 = document.getElementById('fType');
      for (var i3 = 0; i3 < typeSel2.options.length; i3++) {
        if (typeSel2.options[i3].textContent === kb.type) { typeSel2.selectedIndex = i3; break; }
      }
      formPlan = planFromKB(kb, exam);
      drop.className = 'kb-drop';
      toast('已按「' + kb.short + '」最近场次自动填充，可手动修改');
    }

    document.getElementById('fSave').onclick = function () {
      var name = document.getElementById('fName').value.trim();
      if (!name) { toast('请填写任务名称'); return; }
      var typeId = typeSel.value;
      var typeText = typeSel.options[typeSel.selectedIndex].textContent;
      var plan = formPlan || (node && node.plan && node.plan.length ? node.plan : null);
      var target = node || { _id: 'l_' + Math.random().toString(36).slice(2, 12) + Date.now().toString(36) };
      target.name = name;
      target.short = document.getElementById('fShort').value.trim() || name.slice(0, 4);
      target.type = (typeText === '专业赛事' || typeId === 'p8n3l5yt6dcxq2b7') ? '专业赛事' : '通用证书';
      target.site = document.getElementById('fSite').value.trim();
      target.siteName = target.site ? (name + ' 报名入口') : '';
      target.place = document.getElementById('fPlace').value.trim();
      target.fee = parseFloat(document.getElementById('fFee').value) || 0;
      target.regStart = document.getElementById('fRegStart').value;
      target.regEnd = document.getElementById('fRegEnd').value;
      target.tickStart = document.getElementById('fTickStart').value;
      target.tickEnd = document.getElementById('fTickEnd').value;
      target.exam = document.getElementById('fExam').value;
      target.score = document.getElementById('fScore').value;
      target.registered = document.getElementById('fReg').checked;
      if (plan) { target.plan = plan; }
      if (!target.plan || !target.plan.length) { target.plan = ensurePlan(target); }
      if (isNew) { state.nodes.push(target); }
      persistNode(target).then(function () {
        try { localStorage.removeItem('npc_task_axis_form_draft'); } catch (e) { }
        mirrorLocal();
        refreshAll();
        closeModal();
        toast(isNew ? '✅ 任务已创建' : '✅ 任务已更新');
        if (target.registered && isNew) { toast('🎓 报名成功，记得打卡！', true); }
      });
    };
  }

  /* ================= 备考阶段弹窗（含手动新增 / 删除阶段） ================= */
  function openStageModal(node) {
    var plan = ensurePlan(node);
    var map = nodeCheckinMap(node);
    var today = todayStr();
    var ym = monthOf(today);
    var html = '';
    html += '<div class="dlg-title">📚 ' + escapeHtml(node.name) + ' · 备考阶段<button class="dlg-close">✕</button></div>';
    var streak = streakOf(node);
    var total = 0;
    for (var key in map) { if (Object.prototype.hasOwnProperty.call(map, key)) { total++; } }
    html += '<div class="prep-stats"><div class="prep-stat"><div class="v">' + streak + '</div><div class="k">连续天数</div></div><div class="prep-stat"><div class="v">' + total + '</div><div class="k">累计打卡</div></div><div class="prep-stat"><div class="v">' + plan.length + '</div><div class="k">阶段总数</div></div></div>';
    html += '<div class="stage-list" id="stageList"></div>';
    html += '<div class="dlg-actions"><button class="btn btn-gold" id="stageCheckToday">' + (map[today] ? '今日已打卡 ✓' : '⚡ 今日打卡') + '</button></div>';
    html += '<div class="stage-add-form"><div class="cp-title">＋ 手动新增阶段（月份 / 科目 / 核心重点）</div>';
    html += '<div class="f-grid"><div class="f-item"><label>月份</label><input id="saMonth" type="month" value="' + ym + '"></div>';
    html += '<div class="f-item"><label>科目 / 内容</label><input id="saSubject" placeholder="如：听力专项" maxlength="20"></div>';
    html += '<div class="f-item"><label>核心重点</label><input id="saFocus" placeholder="如：精听 1 套真题" maxlength="40"></div></div>';
    html += '<div class="dlg-actions" style="margin-top:12px"><button class="btn btn-ghost btn-sm" id="stageAddBtn">＋ 添加阶段</button></div></div>';
    openModal(html);

    var listBox = document.getElementById('stageList');
    for (var i = 0; i < plan.length; i++) {
      listBox.appendChild(buildStageItem(node, plan[i], map, today, ym));
    }
    var btn = document.getElementById('stageCheckToday');
    btn.disabled = !!map[today];
    btn.onclick = function () { doCheckin(node, today, false); setTimeout(function () { if (document.getElementById('overlay').className.indexOf('show') !== -1) { openStageModal(node); } }, 400); };

    document.getElementById('stageAddBtn').onclick = function () {
      var month = document.getElementById('saMonth').value;
      var subject = document.getElementById('saSubject').value.trim();
      var focus = document.getElementById('saFocus').value.trim();
      if (!month || !subject) { toast('请填写月份与科目'); return; }
      plan.push({ month: month, subject: subject, focus: focus || '自主复习' });
      plan.sort(function (a, b) { return a.month < b.month ? -1 : 1; });
      node.plan = plan;
      persistNode(node).then(function () {
        mirrorLocal();
        toast('✅ 已添加阶段：' + month + ' · ' + subject);
        openStageModal(node);
      });
    };
  }
  function buildStageItem(node, stage, map, today, ym) {
    var item = el('div', 'stage-item' + (stage.month === ym ? ' now' : ''));
    var head = el('div', 'stage-head');
    head.appendChild(el('span', 'stage-month', stage.month));
    head.appendChild(el('span', 'stage-sub', stage.subject));
    if (stage.month === ym) { head.appendChild(el('span', 'stage-done', '进行中')); }

    var monthStart = stage.month + '-01';
    var nextMonth = addMonths(monthStart, 1);
    var lastDay = addDays(nextMonth, -1);
    var endDate = lastDay < today ? lastDay : today;
    var elapsed = daysBetween(monthStart, endDate);
    if (elapsed === null || elapsed < 0) { elapsed = 0; }
    var checked = 0;
    var dayList = [];
    var cursor = monthStart;
    var guard = 0;
    while (cursor <= lastDay && guard < 40) {
      guard++;
      if (cursor <= today) { dayList.push(cursor); }
      if (map[cursor]) { checked++; }
      cursor = addDays(cursor, 1);
    }
    var pct = elapsed > 0 ? Math.min(100, Math.round(checked / (elapsed + 1) * 100)) : 0;
    head.appendChild(el('span', 'stage-done', pct + '%'));
    var delBtn = el('button', 'stage-del', '✕');
    delBtn.title = '删除该阶段';
    delBtn.onclick = function (e) {
      e.stopPropagation();
      if (!window.confirm('删除阶段「' + stage.month + ' · ' + stage.subject + '」？该阶段的打卡记录仍会保留。')) { return; }
      var plan = ensurePlan(node);
      for (var i = 0; i < plan.length; i++) {
        if (plan[i] === stage) { plan.splice(i, 1); break; }
      }
      node.plan = plan;
      persistNode(node).then(function () { mirrorLocal(); openStageModal(node); });
    };
    head.appendChild(delBtn);
    item.appendChild(head);
    item.appendChild(el('div', 'stage-focus', '核心重点：' + stage.focus));

    var bar = el('div', 'stage-bar');
    var fill = el('i');
    fill.style.width = pct + '%';
    bar.appendChild(fill);
    item.appendChild(bar);
    item.appendChild(el('div', 'stage-bar-txt', '完成率 ' + pct + '%（已打卡 ' + checked + ' 天 / 已过 ' + (elapsed + 1) + ' 天）'));

    var panel = el('div', 'chip-panel');
    panel.appendChild(el('div', 'cp-title', '打卡记录（点击补卡 / 取消）：'));
    var chips = el('div', 'day-chips');
    for (var d = 0; d < dayList.length; d++) {
      (function (date) {
        var chip = el('button', 'day-chip' + (map[date] ? ' done' : '') + (date === today ? ' today' : ''), String(parseInt(date.slice(8, 10), 10)));
        chip.title = date + (map[date] ? ' 已打卡' : ' 未打卡');
        chip.onclick = function () {
          if (map[date]) { cancelCheckin(node, date); }
          else { doCheckin(node, date, date !== today); }
          setTimeout(function () { if (document.getElementById('overlay').className.indexOf('show') !== -1) { openStageModal(node); } }, 400);
        };
        chips.appendChild(chip);
      })(dayList[d]);
    }
    if (!dayList.length) { chips.appendChild(el('span', null, '该月份尚未开始')); }
    panel.appendChild(chips);
    item.appendChild(panel);
    return item;
  }

  /* ================= 特效：粒子 + 横幅 + Toast ================= */
  var canvas = document.getElementById('fxCanvas');
  var ctx = canvas.getContext ? canvas.getContext('2d') : null;
  var particles = [];
  var rafId = 0;
  function burstParticles() {
    if (!ctx) { return; }
    canvas.width = window.innerWidth;
    canvas.height = window.innerHeight;
    canvas.style.width = canvas.width + 'px';
    canvas.style.height = canvas.height + 'px';
    var colors = ['#5b5bd6', '#9a4fd8', '#f6a201', '#12b886', '#e6336b', '#38bdf8'];
    for (var i = 0; i < 90; i++) {
      particles.push({
        x: canvas.width / 2 + (Math.random() - 0.5) * 160,
        y: canvas.height / 2 + (Math.random() - 0.5) * 60,
        vx: (Math.random() - 0.5) * 13,
        vy: -Math.random() * 11 - 3,
        size: Math.random() * 7 + 3,
        color: colors[Math.floor(Math.random() * colors.length)],
        life: 1,
        shape: Math.random() > 0.5 ? 'rect' : 'circle',
        rot: Math.random() * Math.PI
      });
    }
    if (!rafId) { rafId = requestAnimationFrame(stepParticles); }
  }
  function stepParticles() {
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    var alive = [];
    for (var i = 0; i < particles.length; i++) {
      var p = particles[i];
      p.vy += 0.28;
      p.x += p.vx;
      p.y += p.vy;
      p.rot += 0.12;
      p.life -= 0.008;
      if (p.life > 0 && p.y < canvas.height + 40) {
        ctx.globalAlpha = Math.max(0, p.life);
        ctx.fillStyle = p.color;
        if (p.shape === 'rect') {
          ctx.save();
          ctx.translate(p.x, p.y);
          ctx.rotate(p.rot);
          ctx.fillRect(-p.size / 2, -p.size / 2, p.size, p.size * 0.6);
          ctx.restore();
        } else {
          ctx.beginPath();
          ctx.arc(p.x, p.y, p.size / 2, 0, Math.PI * 2);
          ctx.fill();
        }
        alive.push(p);
      }
    }
    ctx.globalAlpha = 1;
    particles = alive;
    if (particles.length) {
      rafId = requestAnimationFrame(stepParticles);
    } else {
      rafId = 0;
      ctx.clearRect(0, 0, canvas.width, canvas.height);
    }
  }
  function showBanner(text) {
    document.getElementById('bannerText').textContent = text || '打卡成功';
    var wrap = document.getElementById('bannerWrap');
    wrap.className = 'banner-wrap show';
    setTimeout(function () { wrap.className = 'banner-wrap'; }, 1600);
  }
  function toast(msg, gold) {
    var wrap = document.getElementById('toastWrap');
    var t = el('div', 'toast' + (gold ? ' gold' : ''), msg);
    wrap.appendChild(t);
    setTimeout(function () {
      t.style.transition = 'opacity .3s';
      t.style.opacity = '0';
      setTimeout(function () { if (t.parentNode) { t.parentNode.removeChild(t); } }, 320);
    }, 2600);
  }

  /* ================= 事件绑定与初始化 ================= */
  var navs = document.querySelectorAll('.nav-item');
  for (var ni = 0; ni < navs.length; ni++) {
    (function (item) {
      item.onclick = function () { switchView(item.getAttribute('data-view')); };
    })(navs[ni]);
  }

  document.getElementById('tabCert').onclick = function () { switchTab('通用证书'); };
  document.getElementById('tabMatch').onclick = function () { switchTab('专业赛事'); };
  function switchTab(tab) {
    state.tab = tab;
    document.getElementById('tabCert').className = tab === '通用证书' ? 'active' : '';
    document.getElementById('tabMatch').className = tab === '专业赛事' ? 'active' : '';
    try { localStorage.setItem(LS_TAB, tab); } catch (e) { }
    renderTimeline();
  }

  document.getElementById('btnAdd').onclick = function () { openFormModal(null); };
  document.getElementById('btnQuickAdd').onclick = function () { openFormModal(null); };
  document.getElementById('btnStageAdd').onclick = function () { openFormModal(null); };

  document.getElementById('btnPomoStart').onclick = pomoStart;
  document.getElementById('btnPomoPause').onclick = pomoPause;
  document.getElementById('btnPomoReset').onclick = pomoReset;

  document.getElementById('petBig').onclick = function () { petReact('pat', 'wiggle'); };
  document.getElementById('btnPetPat').onclick = function () { petReact('pat', 'wiggle'); };
  document.getElementById('btnPetFeed').onclick = function () { petReact('feed', 'bounce'); };
  document.getElementById('btnPetCheer').onclick = function () {
    bubbleIdx = (bubbleIdx + 1) % encouragePool.length;
    document.getElementById('petBubble').textContent = encouragePool[bubbleIdx];
    petWiggle('wiggle');
  };
  document.getElementById('btnPetFocus').onclick = function () {
    switchView('focus');
    toast('🍅 桌宠为你加油，开始专注吧！');
  };

  document.getElementById('assistantHat').onclick = function () {
    bubbleIdx = (bubbleIdx + 1) % encouragePool.length;
    document.getElementById('assistantText').textContent = encouragePool[bubbleIdx];
  };


  document.getElementById('overlay').onclick = function (e) {
    if (e.target === document.getElementById('overlay')) { closeModal(); }
  };
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') { closeModal(); }
  });

  function onReady() {
    var seeded = false;
    try { seeded = !!localStorage.getItem(LS_SEEDED); } catch (e) { seeded = false; }
    try {
      var savedTab = localStorage.getItem(LS_TAB);
      if (savedTab === '专业赛事' || savedTab === '通用证书') { state.tab = savedTab; }
    } catch (e) { }
    try {
      var savedView = localStorage.getItem(LS_VIEW);
      if (savedView) { switchView(savedView); }
    } catch (e) { }
    if (!state.nodes.length && !seeded) {
      seedSample().then(refreshAll);
    } else {
      refreshAll();
    }
    if (state.tab !== '通用证书') { switchTab(state.tab); }
  }

  function loadCloud() {
    var p1 = db.query({ databaseId: 'lnmuAnycaz4YipNuhWlHmC', pageSize: 200 })
      .then(function (nodeRes) {
        state.nodes = (nodeRes.results || []).map(normalizeNode);
      });
    var p2 = db.query({ databaseId: 'CtaG4iVTm0yPx8iXaKoTgQ', pageSize: 500 })
      .then(function (ciRes) {
        state.checkins = (ciRes.results || []).map(normalizeCheckin);
      });
    return Promise.all([p1, p2]).then(function () {
      state.cloud = true;
      mirrorLocal();
    });
  }

  if (db) {
    db.getSchema({ databaseId: 'lnmuAnycaz4YipNuhWlHmC' })
      .then(function (schema) {
        var props = schema.properties || [];
        for (var i = 0; i < props.length; i++) {
          if (props[i].name === '类型' && props[i].config && props[i].config.options) {
            state.typeOptions = props[i].config.options;
          }
        }
        return loadCloud();
      })
      .then(onReady)
      .catch(function (err) {
        console.error('[database] 云端加载失败，降级本地存储:', err);
        loadLocal();
        onReady();
      });
  } else {
    loadLocal();
    onReady();
  }
})();
