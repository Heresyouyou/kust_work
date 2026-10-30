# Phase 5 Web 管理端小结

## 1. 完成项

| 子项 | 状态 | 关键产出 |
|---|---|---|
| 5a 前端库本地化 | ✅ | `server/static/vendor/`：htmx 2.0.4 / Alpine 3.14.8 / marked 15.0.6 / Chart.js 4.4.7 |
| 5b 展示侧页面 | ✅ | `/` 仪表盘、`/modules` 模块树、`/module/<name>` 条目列表、`/entry/<id>` 详情、`/search` 检索 |
| 5c 主题 | ✅ | `static/css/theme.css`：全部 CSS 变量，`data-theme` 切换 light/dark，零硬编码颜色 |
| 5d /admin 鉴权 | ✅ | 读取 `config/admin_token.txt`，Flask session，未登录拦截 |
| 5e 条目 CRUD | ✅ | `/admin/entry/new`、`/admin/entry/<id>/edit`、保存、软删除、恢复 |
| 5g 写后一致性 | ✅ | git 快照 → 同步重建索引 → `changelog.jsonl`；已验证一致性 |

## 2. 已验证结论

| 验证项 | 方法 | 结果 |
|---|---|---|
| 展示侧页面 200 | `curl / /modules /module/mineralogy /entry/... /search?q=石英` | 全部返回 200，内容正确 |
| 管理端未鉴权不可访问 | `GET /admin/dashboard` 未登录 | 302 到 `/admin` 登录页 |
| 登录后可进后台 | POST token 后访问 dashboard | 200，显示条目数 61 |
| 新建条目可检索 | 创建测试条目后立刻 `/api/search` | Top-1 命中新条目 |
| 删除后不可检索 | 软删除后立刻 `/api/search` | 测试条目不再命中 |
| 恢复后可检索 | 恢复后立刻 `/api/search` | 测试条目再次命中 |
| light/dark 主题 | CSS 变量 + `data-theme` + localStorage | 浏览器切换正常 |

## 3. 重要文件路径

- `server/app.py`：Flask 三蓝图（页面 / API / /admin）
- `server/static/css/theme.css`
- `server/templates/{base,index,modules,module,entry,search,error}.html`
- `server/templates/admin/{login,dashboard,entries,edit}.html`
- `config/admin_token.txt`
- `changelog.jsonl`（自动追加）

## 4. 已知限制 / 未验证

- Flask 开发服务器（threaded=True），未部署 supervisor 生产守护；Phase 6 将补齐运维文档
- 管理端「跨模块移动」已通过编辑条目换 `module` + 自动迁移文件路径实现，但缺少独立的「整理」批量操作界面
- 模块树的新建/重命名/排序未做 Web UI（content/ 下手动创建目录即可，不影响主线）

## 5. 下一步

Phase 6 自更新机制与验收：
- 6a CLI 增量摄取脚本复用 `ingest.rebuild`
- 6b 运维文档（目录结构 / 启停 / frpc 预留 / 更新流程）
- 6c 验收测试 ≥10 题，输出命中率表格
- 6d 对照任务书 7 条验收标准逐项核验
